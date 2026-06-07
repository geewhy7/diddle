import json

import aiosqlite

_CREATE = """
CREATE TABLE IF NOT EXISTS scores (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    username     TEXT,
    display_name TEXT NOT NULL,
    play_date    TEXT NOT NULL,
    word_length  INTEGER NOT NULL DEFAULT 5,
    moves        INTEGER NOT NULL,
    optimal      INTEGER NOT NULL,
    gave_up      INTEGER NOT NULL DEFAULT 0,
    path         TEXT NOT NULL,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, play_date, word_length)
);
CREATE INDEX IF NOT EXISTS idx_scores_date ON scores(play_date);
"""


async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_CREATE)
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
