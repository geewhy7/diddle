import json
from datetime import date, timedelta

import aiosqlite

_CREATE = """
CREATE TABLE IF NOT EXISTS scores (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    username          TEXT,
    display_name      TEXT NOT NULL,
    play_date         TEXT NOT NULL,
    word_length       INTEGER NOT NULL DEFAULT 5,
    moves             INTEGER NOT NULL,
    optimal           INTEGER NOT NULL,
    gave_up           INTEGER NOT NULL DEFAULT 0,
    timed_out         INTEGER NOT NULL DEFAULT 0,
    time_attack       INTEGER NOT NULL DEFAULT 0,
    path              TEXT NOT NULL,
    invalid_attempts  INTEGER NOT NULL DEFAULT 0,
    chat_id           INTEGER,
    solve_seconds     INTEGER,
    hard_mode         INTEGER NOT NULL DEFAULT 0,   -- played the harder puzzle
    submitted_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, play_date, word_length, hard_mode)
);
CREATE INDEX IF NOT EXISTS idx_scores_date ON scores(play_date);

CREATE TABLE IF NOT EXISTS group_messages (
    chat_id     INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    play_date   TEXT NOT NULL,
    play_url    TEXT,
    is_photo    INTEGER NOT NULL DEFAULT 0,   -- board is a rendered photo (vs text)
    PRIMARY KEY (chat_id, play_date)
);

CREATE TABLE IF NOT EXISTS progress (
    user_id      INTEGER NOT NULL,
    play_date    TEXT NOT NULL,
    word_length  INTEGER NOT NULL,
    hard_mode    INTEGER NOT NULL DEFAULT 0,
    path         TEXT NOT NULL DEFAULT '[]',
    chat_id      INTEGER,
    started_at   TEXT,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, play_date, word_length, hard_mode)
);

CREATE TABLE IF NOT EXISTS day_modes (
    play_date   TEXT PRIMARY KEY,
    hard_mode   INTEGER NOT NULL DEFAULT 0,
    time_limit  INTEGER NOT NULL DEFAULT 0,   -- shot-clock seconds; 0 = untimed
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS group_activity (
    chat_id      INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    play_date    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'playing',
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (chat_id, user_id, play_date)
);

"""

_MIGRATE = """
ALTER TABLE scores ADD COLUMN invalid_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scores ADD COLUMN chat_id INTEGER;
ALTER TABLE group_messages ADD COLUMN play_url TEXT;
ALTER TABLE scores ADD COLUMN solve_seconds INTEGER;
ALTER TABLE progress ADD COLUMN started_at TEXT;
ALTER TABLE scores ADD COLUMN timed_out INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scores ADD COLUMN time_attack INTEGER NOT NULL DEFAULT 0;
ALTER TABLE group_messages ADD COLUMN is_photo INTEGER NOT NULL DEFAULT 0;
"""


async def _columns(db, table: str) -> set[str]:
    cur = await db.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in await cur.fetchall()}


async def _migrate_hard_mode(db) -> None:
    """Add the hard_mode dimension to scores + progress on existing DBs.

    hard_mode joins the UNIQUE key on scores (so a player can play BOTH the
    normal and the hard variant of a length on the same day — two rows) and the
    PRIMARY KEY on progress (so normal- and hard-mode runs resume independently).
    SQLite can't ALTER a constraint, so each table is rebuilt once; the presence
    of the hard_mode column gates the rebuild, making it idempotent. Existing
    rows are all normal mode (hard_mode = 0)."""
    if "hard_mode" not in await _columns(db, "scores"):
        await db.executescript(
            """
            CREATE TABLE scores_new (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL,
                username          TEXT,
                display_name      TEXT NOT NULL,
                play_date         TEXT NOT NULL,
                word_length       INTEGER NOT NULL DEFAULT 5,
                moves             INTEGER NOT NULL,
                optimal           INTEGER NOT NULL,
                gave_up           INTEGER NOT NULL DEFAULT 0,
                timed_out         INTEGER NOT NULL DEFAULT 0,
                time_attack       INTEGER NOT NULL DEFAULT 0,
                path              TEXT NOT NULL,
                invalid_attempts  INTEGER NOT NULL DEFAULT 0,
                chat_id           INTEGER,
                solve_seconds     INTEGER,
                hard_mode         INTEGER NOT NULL DEFAULT 0,
                submitted_at      TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, play_date, word_length, hard_mode)
            );
            INSERT INTO scores_new
                (id, user_id, username, display_name, play_date, word_length,
                 moves, optimal, gave_up, timed_out, time_attack, path,
                 invalid_attempts, chat_id, solve_seconds, hard_mode, submitted_at)
                SELECT id, user_id, username, display_name, play_date, word_length,
                       moves, optimal, gave_up, timed_out, time_attack, path,
                       invalid_attempts, chat_id, solve_seconds, 0, submitted_at
                FROM scores;
            DROP TABLE scores;
            ALTER TABLE scores_new RENAME TO scores;
            CREATE INDEX IF NOT EXISTS idx_scores_date ON scores(play_date);
            """
        )
    if "hard_mode" not in await _columns(db, "progress"):
        await db.executescript(
            """
            CREATE TABLE progress_new (
                user_id      INTEGER NOT NULL,
                play_date    TEXT NOT NULL,
                word_length  INTEGER NOT NULL,
                hard_mode    INTEGER NOT NULL DEFAULT 0,
                path         TEXT NOT NULL DEFAULT '[]',
                chat_id      INTEGER,
                started_at   TEXT,
                updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, play_date, word_length, hard_mode)
            );
            INSERT INTO progress_new
                (user_id, play_date, word_length, hard_mode, path, chat_id, started_at, updated_at)
                SELECT user_id, play_date, word_length, 0, path, chat_id, started_at, updated_at
                FROM progress;
            DROP TABLE progress;
            ALTER TABLE progress_new RENAME TO progress;
            """
        )


async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_CREATE)
        # Apply any columns that may be missing from older schemas
        for stmt in _MIGRATE.strip().splitlines():
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already exists
        await _migrate_hard_mode(db)
        await db.commit()


async def save_score(
    db_path: str,
    *,
    user_id: int,
    username: str | None,
    display_name: str,
    play_date: str,
    word_length: int,
    moves: int,
    optimal: int,
    gave_up: bool,
    path: list[str],
    invalid_attempts: int = 0,
    chat_id: int | None = None,
    solve_seconds: int | None = None,
    timed_out: bool = False,
    time_attack: bool = False,
    hard_mode: bool = False,
) -> dict:
    """
    Insert a score. On UNIQUE conflict (same user + day + word_length +
    hard_mode) the existing row is returned unchanged — retries and replays are
    silent. The normal and hard variant of a length are distinct rows.
    Returns {"moves", "optimal", "gave_up", "timed_out", "time_attack",
    "hard_mode", "submitted_at", "chat_id", "solve_seconds"}.
    """
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute(
                """INSERT INTO scores
                       (user_id, username, display_name, play_date, word_length,
                        moves, optimal, gave_up, timed_out, time_attack, path,
                        invalid_attempts, chat_id, solve_seconds, hard_mode)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, username, display_name, play_date, word_length,
                 moves, optimal, int(gave_up), int(timed_out), int(time_attack),
                 json.dumps(path), invalid_attempts, chat_id, solve_seconds,
                 int(hard_mode)),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            pass  # duplicate submission — fall through to SELECT
        cur = await db.execute(
            """SELECT moves, optimal, gave_up, timed_out, submitted_at, chat_id,
                      path, solve_seconds, time_attack, hard_mode
               FROM scores
               WHERE user_id = ? AND play_date = ? AND word_length = ? AND hard_mode = ?""",
            (user_id, play_date, word_length, int(hard_mode)),
        )
        row = await cur.fetchone()
        return {
            "moves":         row[0],
            "optimal":       row[1],
            "gave_up":       bool(row[2]),
            "timed_out":     bool(row[3]),
            "submitted_at":  row[4],
            "chat_id":       row[5],
            "path":          json.loads(row[6]) if row[6] else [],
            "solve_seconds": row[7],
            "time_attack":   bool(row[8]),
            "hard_mode":     bool(row[9]),
        }


async def get_ordinal_position(
    db_path: str,
    play_date: str,
    word_length: int,
    submitted_at: str,
    chat_id: int | None,
    hard_mode: bool = False,
) -> int:
    """
    Count of completed (not gave-up, not timed-out) scores submitted before
    this one for today in the same chat context (or across all NULL-chat
    scores when chat_id is None) and the same variant (normal vs hard).
    Returns 1-based position.
    """
    async with aiosqlite.connect(db_path) as db:
        if chat_id is None:
            cur = await db.execute(
                """SELECT COUNT(*) FROM scores
                   WHERE play_date = ? AND word_length = ? AND hard_mode = ?
                     AND gave_up = 0 AND timed_out = 0 AND chat_id IS NULL
                     AND submitted_at < ?""",
                (play_date, word_length, int(hard_mode), submitted_at),
            )
        else:
            cur = await db.execute(
                """SELECT COUNT(*) FROM scores
                   WHERE play_date = ? AND word_length = ? AND hard_mode = ?
                     AND gave_up = 0 AND timed_out = 0 AND chat_id = ?
                     AND submitted_at < ?""",
                (play_date, word_length, int(hard_mode), chat_id, submitted_at),
            )
        (count,) = await cur.fetchone()
    return count + 1


def _compute_streaks(rows: list, today: date) -> tuple[int, int]:
    """
    rows: [(play_date_str, failed_int), ...] sorted ascending by date,
    where failed = gave_up OR timed_out. Returns (current_streak, longest_streak).
    A failed day breaks the streak the same as a skipped day.
    Current streak counts backwards from today; if today hasn't been played
    yet it counts from yesterday so early-day checks don't reset to 0.
    """
    plays = {r[0]: bool(r[1]) for r in rows}

    # Longest streak — forward pass
    longest = run = 0
    prev_win_date = None
    for ds in sorted(plays):
        d = date.fromisoformat(ds)
        if plays[ds]:                                    # failed (gave up / timed out)
            run = 0
            prev_win_date = None
        else:
            if prev_win_date is None or (d - prev_win_date).days == 1:
                run += 1
            else:                                        # gap resets
                run = 1
            prev_win_date = d
        longest = max(longest, run)

    # Current streak — walk backwards from today (or yesterday)
    today_str = today.isoformat()
    yest_str  = (today - timedelta(days=1)).isoformat()

    if today_str in plays and not plays[today_str]:
        start = today
    elif today_str not in plays and yest_str in plays and not plays[yest_str]:
        start = today - timedelta(days=1)   # haven't played today yet
    else:
        return 0, longest                   # today failed, or no recent win

    current = 0
    d = start
    while True:
        ds = d.isoformat()
        if ds not in plays or plays[ds]:
            break
        current += 1
        d -= timedelta(days=1)

    return current, longest


async def get_user_stats(db_path: str, user_id: int, word_length: int) -> dict:
    today = date.today()
    async with aiosqlite.connect(db_path) as db:
        # Total played (wins + gave_up + timed_out)
        cur = await db.execute(
            "SELECT COUNT(*) FROM scores WHERE user_id = ? AND word_length = ?",
            (user_id, word_length),
        )
        (total_played,) = await cur.fetchone()

        # Wins and sum of extra moves
        cur = await db.execute(
            """SELECT COUNT(*), COALESCE(SUM(moves - optimal), 0)
               FROM scores
               WHERE user_id = ? AND word_length = ? AND gave_up = 0 AND timed_out = 0""",
            (user_id, word_length),
        )
        row = await cur.fetchone()
        total_won, total_extra = row[0], row[1]

        # Distribution keyed by extra moves, capped at 6 (maps to 6+)
        cur = await db.execute(
            """SELECT MIN(moves - optimal, 6), COUNT(*)
               FROM scores
               WHERE user_id = ? AND word_length = ? AND gave_up = 0 AND timed_out = 0
               GROUP BY MIN(moves - optimal, 6)""",
            (user_id, word_length),
        )
        distribution = {str(r[0]): r[1] for r in await cur.fetchall()}

        # All play dates for streak computation
        cur = await db.execute(
            """SELECT play_date, MAX(gave_up, timed_out) FROM scores
               WHERE user_id = ? AND word_length = ?
               ORDER BY play_date ASC""",
            (user_id, word_length),
        )
        play_rows = await cur.fetchall()

    current_streak, longest_streak = _compute_streaks(play_rows, today)

    return {
        "current_streak": current_streak,
        "longest_streak":  longest_streak,
        "total_played":    total_played,
        "total_won":       total_won,
        "total_extra":     total_extra,
        "distribution":    distribution,
    }


async def get_leaderboard(
    db_path: str,
    play_date: str,
    word_length: int | None = None,
) -> list[dict]:
    """
    All scores for the given day.
    word_length=None: returns all scores (no avg annotation, for bot use).
    word_length=4|5: filtered + each row includes player's all-time avg delta
                     (completions only, None when fewer than 3 days played).
    """
    async with aiosqlite.connect(db_path) as db:
        # Failures sort after all completions; timed-out above gave-up
        # (the clock killed them — quitting is worse). `tt_wins` = all-time
        # count of that player's clean time-attack solves (the ⚡ flex).
        if word_length is None:
            cur = await db.execute(
                """SELECT s.display_name, s.moves, s.optimal, s.gave_up, s.timed_out,
                          s.word_length, NULL, NULL, s.solve_seconds, s.time_attack,
                          (SELECT COUNT(*)
                           FROM scores s2
                           WHERE s2.user_id = s.user_id AND s2.hard_mode = s.hard_mode
                             AND s2.time_attack = 1 AND s2.gave_up = 0 AND s2.timed_out = 0),
                          s.hard_mode
                   FROM scores s
                   WHERE s.play_date = ?
                   ORDER BY MAX(s.gave_up, s.timed_out) ASC, s.gave_up ASC,
                            (s.solve_seconds IS NULL) ASC,
                            (COALESCE(s.solve_seconds, 0) + s.moves * 60) ASC""",
                (play_date,),
            )
        else:
            cur = await db.execute(
                """SELECT s.display_name, s.moves, s.optimal, s.gave_up, s.timed_out,
                          s.word_length,
                          (SELECT AVG(s2.moves - s2.optimal)
                           FROM scores s2
                           WHERE s2.user_id = s.user_id
                             AND s2.word_length = ? AND s2.hard_mode = s.hard_mode
                             AND s2.gave_up = 0 AND s2.timed_out = 0),
                          (SELECT COUNT(*)
                           FROM scores s2
                           WHERE s2.user_id = s.user_id
                             AND s2.word_length = ? AND s2.hard_mode = s.hard_mode
                             AND s2.gave_up = 0 AND s2.timed_out = 0),
                          s.solve_seconds, s.time_attack,
                          (SELECT COUNT(*)
                           FROM scores s2
                           WHERE s2.user_id = s.user_id
                             AND s2.word_length = ? AND s2.hard_mode = s.hard_mode
                             AND s2.time_attack = 1 AND s2.gave_up = 0 AND s2.timed_out = 0),
                          s.hard_mode
                   FROM scores s
                   WHERE s.play_date = ? AND s.word_length = ?
                   ORDER BY MAX(s.gave_up, s.timed_out) ASC, s.gave_up ASC,
                            (s.solve_seconds IS NULL) ASC,
                            (COALESCE(s.solve_seconds, 0) + s.moves * 60) ASC""",
                (word_length, word_length, word_length, play_date, word_length),
            )
        rows = await cur.fetchall()
        result = []
        for r in rows:
            raw_avg, days = r[6], r[7]
            avg = round(raw_avg, 1) if (raw_avg is not None and days is not None and days >= 3) else None
            moves, solve_seconds = r[1], r[8]
            # Hybrid score = solve seconds + 60 per ladder move (lower wins).
            # None when solve_seconds is missing (legacy rows) — sorts last.
            score = (solve_seconds + moves * 60) if solve_seconds is not None else None
            result.append({
                "name":          r[0],
                "moves":         moves,
                "optimal":       r[2],
                "gave_up":       bool(r[3]),
                "timed_out":     bool(r[4]),
                "word_length":   r[5],
                "avg":           avg,
                "solve_seconds": solve_seconds,
                "time_attack":   bool(r[9]),
                "tt_wins":       r[10],
                "hard_mode":     bool(r[11]),
                "score":         score,
                "delta":         moves - r[2],
            })
        return result


async def get_alltime_stats(db_path: str, word_length: int) -> dict:
    """
    Global aggregate stats across all players and all days.
    Returns today's puzzle difficulty and a per-player handicap leaderboard
    (avg moves-above-par, completions only, min 1 day played), sorted ascending.
    """
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            """SELECT AVG(moves - optimal) FROM scores
               WHERE play_date = ? AND word_length = ? AND hard_mode = 0
                 AND gave_up = 0 AND timed_out = 0""",
            (date.today().isoformat(), word_length),
        )
        (avg_diff,) = await cur.fetchone()
        puzzle_difficulty = round(avg_diff, 1) if avg_diff is not None else None

        # Normal-mode handicap only — hard runs are a separate, tougher track and
        # would skew the all-time ladder. (A hard /alltime can come later.)
        cur = await db.execute(
            """SELECT display_name,
                      AVG(moves - optimal) AS handicap,
                      COUNT(*)             AS days_played,
                      SUM(time_attack)     AS tt_wins
               FROM scores
               WHERE word_length = ? AND hard_mode = 0 AND gave_up = 0 AND timed_out = 0
               GROUP BY user_id
               ORDER BY AVG(moves - optimal) ASC""",
            (word_length,),
        )
        rows = await cur.fetchall()

    return {
        "puzzle_difficulty": puzzle_difficulty,
        "players": [
            {"name": r[0], "handicap": round(r[1], 1), "days_played": r[2],
             "tt_wins": r[3] or 0}
            for r in rows
        ],
    }


async def get_user_today_scores(db_path: str, user_id: int, play_date: str) -> list[dict]:
    """All of the user's scores for today across both word lengths."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            """SELECT word_length, moves, optimal, gave_up, timed_out, path, time_attack,
                      solve_seconds, hard_mode
               FROM scores
               WHERE user_id = ? AND play_date = ?""",
            (user_id, play_date),
        )
        rows = await cur.fetchall()
    return [
        {
            "word_length": r[0],
            "moves":       r[1],
            "optimal":     r[2],
            "gave_up":     bool(r[3]),
            "timed_out":   bool(r[4]),
            "delta":       (r[1] - r[2]) if not (r[3] or r[4]) else None,
            "path":        json.loads(r[5]) if r[5] else [],
            "time_attack": bool(r[6]),
            "solve_seconds": r[7],
            "hard_mode":   bool(r[8]),
        }
        for r in rows
    ]


async def get_group_message_row(db_path: str, chat_id: int, play_date: str) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT message_id, play_url, is_photo FROM group_messages WHERE chat_id = ? AND play_date = ?",
            (chat_id, play_date),
        )
        row = await cur.fetchone()
    return {"message_id": row[0], "play_url": row[1], "is_photo": bool(row[2])} if row else None


async def save_group_message_row(db_path: str, chat_id: int, message_id: int, play_date: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO group_messages (chat_id, message_id, play_date) VALUES (?, ?, ?)",
            (chat_id, message_id, play_date),
        )
        await db.commit()


async def upsert_group_message_row(db_path: str, chat_id: int, message_id: int, play_date: str, play_url: str | None = None, is_photo: bool = False) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO group_messages (chat_id, message_id, play_date, play_url, is_photo) VALUES (?, ?, ?, ?, ?)",
            (chat_id, message_id, play_date, play_url, int(is_photo)),
        )
        await db.commit()


async def get_group_scores(db_path: str, chat_id: int, play_date: str) -> list[dict]:
    """Users who submitted scores with this chat_id today — fallback for group_activity gaps."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            """SELECT user_id, display_name,
                      MAX(CASE WHEN gave_up = 0 AND timed_out = 0 THEN 1 ELSE 0 END) AS has_win
               FROM scores
               WHERE chat_id = ? AND play_date = ?
               GROUP BY user_id""",
            (chat_id, play_date),
        )
        rows = await cur.fetchall()
    return [
        {
            "user_id":      r[0],
            "display_name": r[1],
            "status":       "done" if r[2] else "gaveup",
        }
        for r in rows
    ]


async def upsert_progress(
    db_path: str, user_id: int, play_date: str, word_length: int,
    path: list, chat_id: int | None, hard_mode: bool = False
) -> None:
    async with aiosqlite.connect(db_path) as db:
        # started_at is stamped once on first insert and never overwritten —
        # it anchors the solve timer to the first reveal of the puzzle. Normal
        # and hard runs of a length are separate rows, resumed independently.
        await db.execute(
            """INSERT INTO progress (user_id, play_date, word_length, hard_mode, path, chat_id, started_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
               ON CONFLICT(user_id, play_date, word_length, hard_mode) DO UPDATE SET
                   path       = excluded.path,
                   chat_id    = excluded.chat_id,
                   started_at = COALESCE(progress.started_at, excluded.started_at),
                   updated_at = datetime('now')""",
            (user_id, play_date, word_length, int(hard_mode), json.dumps(path), chat_id),
        )
        await db.commit()


async def get_progress_started_at(
    db_path: str, user_id: int, play_date: str, word_length: int, hard_mode: bool = False
) -> str | None:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT started_at FROM progress WHERE user_id = ? AND play_date = ? AND word_length = ? AND hard_mode = ?",
            (user_id, play_date, word_length, int(hard_mode)),
        )
        row = await cur.fetchone()
    return row[0] if row else None


async def delete_progress(db_path: str, user_id: int, play_date: str, word_length: int, hard_mode: bool = False) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM progress WHERE user_id = ? AND play_date = ? AND word_length = ? AND hard_mode = ?",
            (user_id, play_date, word_length, int(hard_mode)),
        )
        await db.commit()


async def get_user_progress(db_path: str, user_id: int, play_date: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT word_length, path, started_at, hard_mode FROM progress WHERE user_id = ? AND play_date = ?",
            (user_id, play_date),
        )
        rows = await cur.fetchall()
    return [
        {"word_length": r[0], "path": json.loads(r[1]) if r[1] else [], "started_at": r[2],
         "hard_mode": bool(r[3])}
        for r in rows
    ]


async def get_progress_for_users(db_path: str, user_ids: list[int], play_date: str) -> list[dict]:
    if not user_ids:
        return []
    placeholders = ",".join("?" * len(user_ids))
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            f"""SELECT user_id, word_length, path, updated_at, hard_mode FROM progress
                WHERE user_id IN ({placeholders}) AND play_date = ?
                ORDER BY updated_at DESC""",
            (*user_ids, play_date),
        )
        rows = await cur.fetchall()
    return [
        {
            "user_id":     r[0],
            "word_length": r[1],
            "path":        json.loads(r[2]) if r[2] else [],
            "updated_at":  r[3],
            "hard_mode":   bool(r[4]),
        }
        for r in rows
    ]


async def upsert_group_activity(
    db_path: str, chat_id: int, user_id: int, display_name: str, play_date: str, status: str
) -> None:
    async with aiosqlite.connect(db_path) as db:
        if status == "done":
            await db.execute(
                """INSERT INTO group_activity
                       (chat_id, user_id, display_name, play_date, status, updated_at)
                   VALUES (?, ?, ?, ?, 'done', datetime('now'))
                   ON CONFLICT(chat_id, user_id, play_date) DO UPDATE SET
                       display_name = excluded.display_name,
                       status       = 'done',
                       updated_at   = datetime('now')""",
                (chat_id, user_id, display_name, play_date),
            )
        elif status in ("gaveup", "timedout"):
            # failure statuses only ever upgrade from 'playing'
            await db.execute(
                """INSERT INTO group_activity
                       (chat_id, user_id, display_name, play_date, status, updated_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(chat_id, user_id, play_date) DO UPDATE SET
                       display_name = excluded.display_name,
                       status       = CASE WHEN status = 'playing' THEN excluded.status ELSE status END,
                       updated_at   = datetime('now')""",
                (chat_id, user_id, display_name, play_date, status),
            )
        else:  # 'playing'
            await db.execute(
                """INSERT INTO group_activity
                       (chat_id, user_id, display_name, play_date, status, updated_at)
                   VALUES (?, ?, ?, ?, 'playing', datetime('now'))
                   ON CONFLICT(chat_id, user_id, play_date) DO UPDATE SET
                       display_name = excluded.display_name,
                       updated_at   = datetime('now')""",
                (chat_id, user_id, display_name, play_date),
            )
        await db.commit()


async def upsert_day_mode(
    db_path: str, play_date: str, hard_mode: bool, time_limit: int
) -> None:
    """One row per day recording what kind of day it was (hard mode? timed?
    both? neither?) — config can change between eras, scores can't say."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO day_modes (play_date, hard_mode, time_limit, recorded_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(play_date) DO UPDATE SET
                   hard_mode   = excluded.hard_mode,
                   time_limit  = excluded.time_limit,
                   recorded_at = datetime('now')""",
            (play_date, int(hard_mode), time_limit),
        )
        await db.commit()


async def get_group_activity(db_path: str, chat_id: int, play_date: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            """SELECT user_id, display_name, status FROM group_activity
               WHERE chat_id = ? AND play_date = ?
               ORDER BY updated_at ASC""",
            (chat_id, play_date),
        )
        rows = await cur.fetchall()
    return [{"user_id": r[0], "display_name": r[1], "status": r[2]} for r in rows]


async def get_chat_roster(db_path: str, chat_id: int) -> dict[int, str]:
    """Persistent membership of a chat: every user ever seen here on ANY date,
    via group_activity or a score tagged with this chat_id. Maps user_id to the
    most-recent display name. This is the roster a chat's board draws from —
    once you've interacted from a chat you stay registered to it. Who actually
    *shows* on a given day is then filtered by that day's activity in
    compute_board_data."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            """SELECT user_id, display_name FROM (
                   SELECT user_id, display_name, updated_at AS ts
                       FROM group_activity WHERE chat_id = ?
                   UNION ALL
                   SELECT user_id, display_name, submitted_at AS ts
                       FROM scores WHERE chat_id = ?
               ) ORDER BY ts ASC""",
            (chat_id, chat_id),
        )
        rows = await cur.fetchall()
    roster: dict[int, str] = {}
    for uid, name in rows:
        roster[uid] = name   # rows are oldest-first, so the newest name wins
    return roster


async def get_user_board_chats(db_path: str, user_id: int, play_date: str) -> list[int]:
    """Every chat that (a) has a board posted today and (b) lists this user in
    its all-time roster — i.e. all boards that should re-render when this user
    plays, even chats they didn't play from today."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            """SELECT gm.chat_id FROM group_messages gm
               WHERE gm.play_date = ?
                 AND EXISTS (
                     SELECT 1 FROM group_activity ga
                         WHERE ga.chat_id = gm.chat_id AND ga.user_id = ?
                     UNION ALL
                     SELECT 1 FROM scores s
                         WHERE s.chat_id = gm.chat_id AND s.user_id = ?
                 )""",
            (play_date, user_id, user_id),
        )
        rows = await cur.fetchall()
    return [r[0] for r in rows]


async def get_user_chat_ids(db_path: str, user_id: int) -> list[int]:
    """Every chat this user has ever interacted from (group_activity or a
    chat-tagged score row) — the chats unioned to build their connections."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            """SELECT DISTINCT chat_id FROM group_activity WHERE user_id = ?
               UNION
               SELECT DISTINCT chat_id FROM scores WHERE user_id = ? AND chat_id IS NOT NULL""",
            (user_id, user_id),
        )
        rows = await cur.fetchall()
    return [r[0] for r in rows]


async def get_connections(db_path: str, user_id: int) -> dict[int, str]:
    """Everyone this user is connected to: the union of chat rosters across
    every chat they've ever played/chatted from. Deliberately asymmetric — a
    player in several chats sees a wider set of names than someone who only
    shares one chat with them (each side unions their OWN chat list)."""
    roster: dict[int, str] = {}
    for chat_id in await get_user_chat_ids(db_path, user_id):
        roster.update(await get_chat_roster(db_path, chat_id))
    roster.pop(user_id, None)
    return roster


async def get_peer_scores(
    db_path: str, user_id: int, play_date: str, word_length: int, hard_mode: bool
) -> list[dict]:
    """Today's finished (solved or gave-up) runs for this puzzle variant from
    everyone the requesting user is connected to — feeds the finish screen's
    ladder-picker peer pills."""
    connections = await get_connections(db_path, user_id)
    if not connections:
        return []
    async with aiosqlite.connect(db_path) as db:
        placeholders = ",".join("?" for _ in connections)
        cur = await db.execute(
            f"""SELECT user_id, display_name, moves, gave_up, timed_out, path,
                       solve_seconds, time_attack
                FROM scores
                WHERE play_date = ? AND word_length = ? AND hard_mode = ?
                  AND user_id IN ({placeholders})
                ORDER BY MAX(gave_up, timed_out) ASC, gave_up ASC,
                         (COALESCE(solve_seconds, 0) + moves * 60) ASC""",
            (play_date, word_length, int(hard_mode), *connections.keys()),
        )
        rows = await cur.fetchall()
    return [{
        "user_id":       r[0],
        "name":          connections.get(r[0], r[1]),
        "moves":         r[2],
        "gave_up":       bool(r[3]),
        "timed_out":     bool(r[4]),
        "path":          json.loads(r[5]) if r[5] else [],
        "solve_seconds": r[6],
        "time_attack":   bool(r[7]),
    } for r in rows]
