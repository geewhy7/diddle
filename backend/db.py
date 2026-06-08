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

CREATE TABLE IF NOT EXISTS group_members (
    user_id      INTEGER NOT NULL,
    chat_id      INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    username     TEXT,
    last_seen    TEXT NOT NULL,
    PRIMARY KEY (user_id, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_gm_chat ON group_members(chat_id);
"""

_MIGRATE = """
ALTER TABLE scores ADD COLUMN invalid_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scores ADD COLUMN chat_id INTEGER;
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
            """SELECT moves, optimal, gave_up, submitted_at, chat_id FROM scores
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


async def record_group_member(
    db_path: str,
    user_id: int,
    chat_id: int,
    display_name: str,
    username: str | None,
) -> None:
    """
    Upsert a (user_id, chat_id) pair in group_members.
    Called by the bot whenever a user runs a command in a group.
    This is how the leaderboard knows which users belong to which group.
    """
    from datetime import datetime
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO group_members (user_id, chat_id, display_name, username, last_seen)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, chat_id) DO UPDATE SET
                   display_name = excluded.display_name,
                   username     = excluded.username,
                   last_seen    = excluded.last_seen""",
            (user_id, chat_id, display_name, username, now),
        )
        await db.commit()


async def get_leaderboard(
    db_path: str,
    play_date: str,
    word_length: int,
    chat_id: int | None = None,
) -> list[dict]:
    """
    Return scores for the given day.
    If chat_id is provided, restrict to users who are members of that group
    (via the group_members table — populated by bot command interactions).
    Scores are global; the group filter is on membership, not where the game was played.
    Completions sorted by moves ascending; gave_up entries at the bottom.
    """
    async with aiosqlite.connect(db_path) as db:
        if chat_id is None:
            cur = await db.execute(
                """SELECT display_name, moves, optimal, gave_up FROM scores
                   WHERE play_date = ? AND word_length = ?
                   ORDER BY gave_up ASC, moves ASC""",
                (play_date, word_length),
            )
        else:
            cur = await db.execute(
                """SELECT s.display_name, s.moves, s.optimal, s.gave_up
                   FROM scores s
                   JOIN group_members gm ON gm.user_id = s.user_id AND gm.chat_id = ?
                   WHERE s.play_date = ? AND s.word_length = ?
                   ORDER BY s.gave_up ASC, s.moves ASC""",
                (chat_id, play_date, word_length),
            )
        rows = await cur.fetchall()
        return [
            {"name": r[0], "moves": r[1], "optimal": r[2], "gave_up": bool(r[3])}
            for r in rows
        ]


async def get_group_stats(
    db_path: str,
    play_date: str,
    word_length: int,
    optimal: int,
    chat_id: int | None = None,
) -> dict:
    """
    Returns puzzle_difficulty (avg delta for today's completions) and per-user
    handicaps (avg lifetime delta, completions only, min 3 days played).
    Filtered by group_members when chat_id is provided.
    """
    async with aiosqlite.connect(db_path) as db:
        if chat_id is None:
            cur = await db.execute(
                """SELECT AVG(moves - optimal) FROM scores
                   WHERE play_date = ? AND word_length = ? AND gave_up = 0""",
                (play_date, word_length),
            )
        else:
            cur = await db.execute(
                """SELECT AVG(s.moves - s.optimal) FROM scores s
                   JOIN group_members gm ON gm.user_id = s.user_id AND gm.chat_id = ?
                   WHERE s.play_date = ? AND s.word_length = ? AND s.gave_up = 0""",
                (chat_id, play_date, word_length),
            )
        (avg_diff,) = await cur.fetchone()
        puzzle_difficulty = round(avg_diff, 1) if avg_diff is not None else None

        if chat_id is None:
            cur = await db.execute(
                """SELECT s.display_name,
                          AVG(s.moves - s.optimal) AS handicap,
                          COUNT(*)                 AS days_played
                   FROM scores s
                   WHERE s.word_length = ? AND s.gave_up = 0
                   GROUP BY s.user_id
                   HAVING COUNT(*) >= 3
                   ORDER BY AVG(s.moves - s.optimal) ASC""",
                (word_length,),
            )
        else:
            cur = await db.execute(
                """SELECT s.display_name,
                          AVG(s.moves - s.optimal) AS handicap,
                          COUNT(*)                 AS days_played
                   FROM scores s
                   JOIN group_members gm ON gm.user_id = s.user_id AND gm.chat_id = ?
                   WHERE s.word_length = ? AND s.gave_up = 0
                   GROUP BY s.user_id
                   HAVING COUNT(*) >= 3
                   ORDER BY AVG(s.moves - s.optimal) ASC""",
                (chat_id, word_length),
            )
        rows = await cur.fetchall()

    handicaps = [
        {"name": r[0], "handicap": round(r[1], 1), "days_played": r[2]}
        for r in rows
    ]
    return {
        "puzzle_difficulty": puzzle_difficulty,
        "handicaps":         handicaps,
    }
