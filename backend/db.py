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
    path              TEXT NOT NULL,
    invalid_attempts  INTEGER NOT NULL DEFAULT 0,
    chat_id           INTEGER,
    submitted_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, play_date, word_length)
);
CREATE INDEX IF NOT EXISTS idx_scores_date ON scores(play_date);

CREATE TABLE IF NOT EXISTS group_messages (
    chat_id     INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    play_date   TEXT NOT NULL,
    play_url    TEXT,
    PRIMARY KEY (chat_id, play_date)
);

CREATE TABLE IF NOT EXISTS progress (
    user_id      INTEGER NOT NULL,
    play_date    TEXT NOT NULL,
    word_length  INTEGER NOT NULL,
    path         TEXT NOT NULL DEFAULT '[]',
    chat_id      INTEGER,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, play_date, word_length)
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
"""


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
) -> dict:
    """
    Insert a score. On UNIQUE conflict (same user + day + word_length) the
    existing row is returned unchanged — retries and replays are silent.
    Returns {"moves", "optimal", "gave_up", "submitted_at", "chat_id"}.
    """
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute(
                """INSERT INTO scores
                       (user_id, username, display_name, play_date, word_length,
                        moves, optimal, gave_up, path, invalid_attempts, chat_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, username, display_name, play_date, word_length,
                 moves, optimal, int(gave_up), json.dumps(path),
                 invalid_attempts, chat_id),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            pass  # duplicate submission — fall through to SELECT
        cur = await db.execute(
            """SELECT moves, optimal, gave_up, submitted_at, chat_id, path FROM scores
               WHERE user_id = ? AND play_date = ? AND word_length = ?""",
            (user_id, play_date, word_length),
        )
        row = await cur.fetchone()
        return {
            "moves":        row[0],
            "optimal":      row[1],
            "gave_up":      bool(row[2]),
            "submitted_at": row[3],
            "chat_id":      row[4],
            "path":         json.loads(row[5]) if row[5] else [],
        }


async def get_ordinal_position(
    db_path: str,
    play_date: str,
    word_length: int,
    submitted_at: str,
    chat_id: int | None,
) -> int:
    """
    Count of gave_up=0 scores submitted before this one for today in the
    same chat context (or across all NULL-chat scores when chat_id is None).
    Returns 1-based position.
    """
    async with aiosqlite.connect(db_path) as db:
        if chat_id is None:
            cur = await db.execute(
                """SELECT COUNT(*) FROM scores
                   WHERE play_date = ? AND word_length = ?
                     AND gave_up = 0 AND chat_id IS NULL
                     AND submitted_at < ?""",
                (play_date, word_length, submitted_at),
            )
        else:
            cur = await db.execute(
                """SELECT COUNT(*) FROM scores
                   WHERE play_date = ? AND word_length = ?
                     AND gave_up = 0 AND chat_id = ?
                     AND submitted_at < ?""",
                (play_date, word_length, chat_id, submitted_at),
            )
        (count,) = await cur.fetchone()
    return count + 1


def _compute_streaks(rows: list, today: date) -> tuple[int, int]:
    """
    rows: [(play_date_str, gave_up_int), ...] sorted ascending by date.
    Returns (current_streak, longest_streak).
    A gave_up day breaks the streak the same as a skipped day.
    Current streak counts backwards from today; if today hasn't been played
    yet it counts from yesterday so early-day checks don't reset to 0.
    """
    plays = {r[0]: bool(r[1]) for r in rows}

    # Longest streak — forward pass
    longest = run = 0
    prev_win_date = None
    for ds in sorted(plays):
        d = date.fromisoformat(ds)
        if plays[ds]:                                    # gave_up
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
        return 0, longest                   # today gave_up, or no recent win

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
        # Total played (wins + gave_up)
        cur = await db.execute(
            "SELECT COUNT(*) FROM scores WHERE user_id = ? AND word_length = ?",
            (user_id, word_length),
        )
        (total_played,) = await cur.fetchone()

        # Wins and sum of extra moves
        cur = await db.execute(
            """SELECT COUNT(*), COALESCE(SUM(moves - optimal), 0)
               FROM scores WHERE user_id = ? AND word_length = ? AND gave_up = 0""",
            (user_id, word_length),
        )
        row = await cur.fetchone()
        total_won, total_extra = row[0], row[1]

        # Distribution keyed by extra moves, capped at 6 (maps to 6+)
        cur = await db.execute(
            """SELECT MIN(moves - optimal, 6), COUNT(*)
               FROM scores WHERE user_id = ? AND word_length = ? AND gave_up = 0
               GROUP BY MIN(moves - optimal, 6)""",
            (user_id, word_length),
        )
        distribution = {str(r[0]): r[1] for r in await cur.fetchall()}

        # All play dates for streak computation
        cur = await db.execute(
            """SELECT play_date, gave_up FROM scores
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
        if word_length is None:
            cur = await db.execute(
                """SELECT display_name, moves, optimal, gave_up, word_length, NULL, NULL
                   FROM scores
                   WHERE play_date = ?
                   ORDER BY gave_up ASC, moves ASC""",
                (play_date,),
            )
        else:
            cur = await db.execute(
                """SELECT s.display_name, s.moves, s.optimal, s.gave_up, s.word_length,
                          (SELECT AVG(s2.moves - s2.optimal)
                           FROM scores s2
                           WHERE s2.user_id = s.user_id
                             AND s2.word_length = ?
                             AND s2.gave_up = 0),
                          (SELECT COUNT(*)
                           FROM scores s2
                           WHERE s2.user_id = s.user_id
                             AND s2.word_length = ?
                             AND s2.gave_up = 0)
                   FROM scores s
                   WHERE s.play_date = ? AND s.word_length = ?
                   ORDER BY s.gave_up ASC, s.moves ASC""",
                (word_length, word_length, play_date, word_length),
            )
        rows = await cur.fetchall()
        result = []
        for r in rows:
            raw_avg, days = r[5], r[6]
            avg = round(raw_avg, 1) if (raw_avg is not None and days is not None and days >= 3) else None
            result.append({
                "name":        r[0],
                "moves":       r[1],
                "optimal":     r[2],
                "gave_up":     bool(r[3]),
                "word_length": r[4],
                "avg":         avg,
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
               WHERE play_date = ? AND word_length = ? AND gave_up = 0""",
            (date.today().isoformat(), word_length),
        )
        (avg_diff,) = await cur.fetchone()
        puzzle_difficulty = round(avg_diff, 1) if avg_diff is not None else None

        cur = await db.execute(
            """SELECT display_name,
                      AVG(moves - optimal) AS handicap,
                      COUNT(*)             AS days_played
               FROM scores
               WHERE word_length = ? AND gave_up = 0
               GROUP BY user_id
               ORDER BY AVG(moves - optimal) ASC""",
            (word_length,),
        )
        rows = await cur.fetchall()

    return {
        "puzzle_difficulty": puzzle_difficulty,
        "players": [
            {"name": r[0], "handicap": round(r[1], 1), "days_played": r[2]}
            for r in rows
        ],
    }


async def get_user_today_scores(db_path: str, user_id: int, play_date: str) -> list[dict]:
    """All of the user's scores for today across both word lengths."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            """SELECT word_length, moves, optimal, gave_up, path FROM scores
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
            "delta":       (r[1] - r[2]) if not r[3] else None,
            "path":        json.loads(r[4]) if r[4] else [],
        }
        for r in rows
    ]


async def get_group_message_row(db_path: str, chat_id: int, play_date: str) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT message_id, play_url FROM group_messages WHERE chat_id = ? AND play_date = ?",
            (chat_id, play_date),
        )
        row = await cur.fetchone()
    return {"message_id": row[0], "play_url": row[1]} if row else None


async def save_group_message_row(db_path: str, chat_id: int, message_id: int, play_date: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO group_messages (chat_id, message_id, play_date) VALUES (?, ?, ?)",
            (chat_id, message_id, play_date),
        )
        await db.commit()


async def upsert_group_message_row(db_path: str, chat_id: int, message_id: int, play_date: str, play_url: str | None = None) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO group_messages (chat_id, message_id, play_date, play_url) VALUES (?, ?, ?, ?)",
            (chat_id, message_id, play_date, play_url),
        )
        await db.commit()


async def get_group_scores(db_path: str, chat_id: int, play_date: str) -> list[dict]:
    """Users who submitted scores with this chat_id today — fallback for group_activity gaps."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            """SELECT user_id, display_name,
                      MAX(CASE WHEN gave_up = 0 THEN 1 ELSE 0 END) AS has_win
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
    path: list, chat_id: int | None
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO progress (user_id, play_date, word_length, path, chat_id, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(user_id, play_date, word_length) DO UPDATE SET
                   path       = excluded.path,
                   chat_id    = excluded.chat_id,
                   updated_at = datetime('now')""",
            (user_id, play_date, word_length, json.dumps(path), chat_id),
        )
        await db.commit()


async def delete_progress(db_path: str, user_id: int, play_date: str, word_length: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM progress WHERE user_id = ? AND play_date = ? AND word_length = ?",
            (user_id, play_date, word_length),
        )
        await db.commit()


async def get_user_progress(db_path: str, user_id: int, play_date: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT word_length, path FROM progress WHERE user_id = ? AND play_date = ?",
            (user_id, play_date),
        )
        rows = await cur.fetchall()
    return [{"word_length": r[0], "path": json.loads(r[1]) if r[1] else []} for r in rows]


async def get_progress_for_users(db_path: str, user_ids: list[int], play_date: str) -> list[dict]:
    if not user_ids:
        return []
    placeholders = ",".join("?" * len(user_ids))
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            f"""SELECT user_id, word_length, path, updated_at FROM progress
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
        elif status == "gaveup":
            await db.execute(
                """INSERT INTO group_activity
                       (chat_id, user_id, display_name, play_date, status, updated_at)
                   VALUES (?, ?, ?, ?, 'gaveup', datetime('now'))
                   ON CONFLICT(chat_id, user_id, play_date) DO UPDATE SET
                       display_name = excluded.display_name,
                       status       = CASE WHEN status = 'playing' THEN 'gaveup' ELSE status END,
                       updated_at   = datetime('now')""",
                (chat_id, user_id, display_name, play_date),
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
