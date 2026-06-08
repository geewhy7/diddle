import os
import sys
from contextlib import asynccontextmanager
from datetime import date

EPOCH = date(2026, 6, 7)   # day 1

def game_day() -> int:
    return (date.today() - EPOCH).days + 1

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(__file__))
from game import load_words, build_graph, largest_component, pick_puzzle, validate
from tg import verify_init_data
from db import init_db, save_score, get_leaderboard, get_user_stats, get_ordinal_position, get_group_stats, record_group_member

BOT_TOKEN     = os.environ["TELEGRAM_TOKEN"]
DB_PATH       = os.environ.get("DB_PATH", "diddle.db")
DEV_SKIP_AUTH = os.environ.get("DEV_SKIP_AUTH", "").lower() == "true"
_DEV_USER     = {"id": 999_999, "first_name": "Claude", "username": "claude_dev"}


def _auth_user(init_data: str) -> tuple[dict, int | None]:
    """
    Verify initData. Returns (user_data, chat_id).
    DEV_SKIP_AUTH=true accepts empty initData as a test user with no chat.
    """
    if DEV_SKIP_AUTH and not init_data:
        return _DEV_USER, None
    return verify_init_data(init_data, BOT_TOKEN)

_words: set[str] = set()
_graph: dict = {}
_puzzle: dict | None = None
_puzzle_day: int = -1


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _words, _graph
    # load_words fetches word lists over HTTP — runs once at startup only
    raw = load_words(5)
    graph = build_graph(raw)
    _words = largest_component(graph)
    _graph = graph
    await init_db(DB_PATH)
    yield


app = FastAPI(lifespan=lifespan)

CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "https://diddle.retard.zone")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGIN],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


def today_puzzle() -> dict:
    global _puzzle, _puzzle_day
    today = date.today().toordinal()
    if _puzzle_day != today:
        start, end, path = pick_puzzle(_words, _graph)
        _puzzle = {
            "start": start,
            "end": end,
            "optimal_steps": len(path) - 1,
            "day": game_day(),
            "word_length": 5,
        }
        _puzzle_day = today
    return _puzzle


def _rank(leaderboard: list[dict], moves: int, gave_up: bool) -> int:
    if gave_up:
        # gave_up entries sort after all completions
        return sum(1 for e in leaderboard if not e["gave_up"]) + 1
    return sum(1 for e in leaderboard if not e["gave_up"] and e["moves"] < moves) + 1


def _message(delta: int, gave_up: bool) -> str:
    if gave_up:
        return "Better luck tomorrow!"
    if delta == 0:
        return "Perfect!"
    if delta == 1:
        return "So close — 1 over par!"
    return f"+{delta} over par"


# ── Public endpoints ───────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "day": game_day()}


@app.get("/puzzle")
async def puzzle():
    return today_puzzle()


@app.get("/words")
async def words():
    return PlainTextResponse("\n".join(sorted(_words)))


# ── Authenticated endpoints ────────────────────────────────────────────────────

class ScoreSubmission(BaseModel):
    init_data: str
    path: list[str]
    gave_up: bool
    invalid_attempts: int = 0
    chat_id: int | None = None


@app.post("/score")
async def score(submission: ScoreSubmission):
    try:
        user, init_chat_id = _auth_user(submission.init_data)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=403, detail="Could not identify user")

    puz = today_puzzle()
    start, end, optimal = puz["start"], puz["end"], puz["optimal_steps"]

    # Normalise to lowercase — never trust client casing
    path = [w.strip().lower() for w in submission.path]

    # Re-validate the full submitted path before touching the database
    if not path:
        raise HTTPException(status_code=422, detail="Path cannot be empty")
    if path[0] != start:
        raise HTTPException(status_code=422, detail=f"Path must start with '{start}'")
    if not submission.gave_up and path[-1] != end:
        raise HTTPException(status_code=422, detail=f"Path must end with '{end}'")
    for i in range(len(path) - 1):
        err = validate(path[i], path[i + 1], _words)
        if err:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid move {path[i]}→{path[i + 1]}: {err}",
            )

    display_name = user.get("first_name", "Player")
    if user.get("last_name"):
        display_name += f" {user['last_name']}"

    play_date = date.today().isoformat()

    # Prefer chat_id from initData (authoritative); fall back to client-supplied value
    chat_id = init_chat_id if init_chat_id is not None else submission.chat_id

    # save_score returns existing row silently on duplicate submission
    stored = await save_score(
        DB_PATH,
        user_id=user_id,
        username=user.get("username"),
        display_name=display_name,
        play_date=play_date,
        word_length=puz["word_length"],
        moves=len(path) - 1,
        optimal=optimal,
        gave_up=submission.gave_up,
        path=path,
        invalid_attempts=submission.invalid_attempts,
        chat_id=chat_id,
    )

    board = await get_leaderboard(DB_PATH, play_date, puz["word_length"])
    delta = stored["moves"] - optimal
    ordinal = await get_ordinal_position(
        DB_PATH, play_date, puz["word_length"],
        stored["submitted_at"], stored["chat_id"],
    )

    return {
        "moves":            stored["moves"],
        "optimal":          optimal,
        "delta":            delta,
        "rank":             _rank(board, stored["moves"], stored["gave_up"]),
        "ordinal_position": ordinal,
        "message":          _message(delta, stored["gave_up"]),
    }


def _check_leaderboard_auth(authorization: str) -> None:
    """Accept tma <initData> from the frontend OR bot <token> from the bot process."""
    if authorization.startswith("bot ") and authorization[4:] == BOT_TOKEN:
        return
    if not authorization.startswith("tma "):
        raise HTTPException(status_code=403, detail="Missing auth token")
    try:
        _auth_user(authorization[4:])   # raises on bad sig; return value unused here
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))


@app.get("/leaderboard")
async def leaderboard(
    authorization: str = Header(default=""),
    chat_id: int | None = Query(default=None),
):
    _check_leaderboard_auth(authorization)

    puz = today_puzzle()
    return await get_leaderboard(DB_PATH, date.today().isoformat(), puz["word_length"], chat_id)


@app.get("/stats/group")
async def stats_group(
    authorization: str = Header(default=""),
    chat_id: int | None = Query(default=None),
):
    _check_leaderboard_auth(authorization)

    puz = today_puzzle()
    result = await get_group_stats(
        DB_PATH, date.today().isoformat(), puz["word_length"],
        puz["optimal_steps"], chat_id,
    )
    return {"puzzle_day": puz["day"], **result}


class GroupMemberRecord(BaseModel):
    user_id:      int
    chat_id:      int
    display_name: str
    username:     str | None = None


@app.post("/group/member")
async def group_member(record: GroupMemberRecord, authorization: str = Header(default="")):
    """Bot calls this whenever a user interacts in a group. Bot-token auth only."""
    if not authorization.startswith("bot ") or authorization[4:] != BOT_TOKEN:
        raise HTTPException(status_code=403, detail="Bot auth required")
    await record_group_member(
        DB_PATH,
        user_id=record.user_id,
        chat_id=record.chat_id,
        display_name=record.display_name,
        username=record.username,
    )
    return {"ok": True}


@app.get("/stats")
async def stats(authorization: str = Header(default="")):
    if not authorization.startswith("tma "):
        raise HTTPException(status_code=403, detail="Missing tma token")
    try:
        user, _chat_id = _auth_user(authorization[4:])
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=403, detail="Could not identify user")

    puz = today_puzzle()
    return await get_user_stats(DB_PATH, user_id, puz["word_length"])


# StaticFiles must be mounted last — API routes registered above take priority
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
