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
) -> dict:
    """
    Insert a score. On UNIQUE conflict (same user + day + word_length) the
    existing row is returned unchanged — retries and replays are silent.
    Returns {"moves": int, "optimal": int, "gave_up": bool}.
    """
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute(
                """INSERT INTO scores
                       (user_id, username, display_name, play_date, word_length,
                        moves, optimal, gave_up, path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, username, display_name, play_date, word_length,
                 moves, optimal, int(gave_up), json.dumps(path)),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            pass  # duplicate submission — fall through to SELECT
        cur = await db.execute(
            """SELECT moves, optimal, gave_up FROM scores
               WHERE user_id = ? AND play_date = ? AND word_length = ?""",
            (user_id, play_date, word_length),
        )
        row = await cur.fetchone()
        return {"moves": row[0], "optimal": row[1], "gave_up": bool(row[2])}


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


async def get_leaderboard(db_path: str, play_date: str, word_length: int) -> list[dict]:
    """
    Return all scores for the given day.
    Completions sorted by moves ascending; gave_up entries at the bottom.
    """
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            """SELECT display_name, moves, optimal, gave_up FROM scores
               WHERE play_date = ? AND word_length = ?
               ORDER BY gave_up ASC, moves ASC""",
            (play_date, word_length),
        )
        rows = await cur.fetchall()
        return [
            {"name": r[0], "moves": r[1], "optimal": r[2], "gave_up": bool(r[3])}
            for r in rows
        ]
