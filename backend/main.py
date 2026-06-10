import html
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import date

import httpx

log = logging.getLogger(__name__)

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
from db import (
    init_db, save_score, get_leaderboard, get_user_stats, get_ordinal_position,
    get_alltime_stats, get_user_today_scores,
    get_group_message_row, upsert_group_message_row,
    upsert_group_activity, get_group_activity, get_group_scores,
    upsert_progress, delete_progress, get_user_progress, get_progress_for_users,
)

BOT_TOKEN     = os.environ["TELEGRAM_TOKEN"]
DB_PATH       = os.environ.get("DB_PATH", "diddle.db")
GAME_URL      = os.environ.get("GAME_URL", "https://diddle.retard.zone")
DEV_SKIP_AUTH = os.environ.get("DEV_SKIP_AUTH", "").lower() == "true"
FORCE_CHALLENGE = os.environ.get("FORCE_CHALLENGE", "").lower() == "true"
_DEV_USER     = {"id": 999_999, "first_name": "Claude", "username": "claude_dev"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw)
    except ValueError:
        if raw:
            log.warning("%s=%r is not an integer — using default %d", name, raw, default)
        return default


# Difficulty knobs — all overridable in .env (backend restart required).
# Changing a range mid-day re-rolls that day's puzzle; prefer changing after midnight.
PUZZLE_MIN_STEPS      = _env_int("PUZZLE_MIN_STEPS", 4)
PUZZLE_MAX_STEPS      = _env_int("PUZZLE_MAX_STEPS", 7)
CHALLENGE_MIN_STEPS   = _env_int("CHALLENGE_MIN_STEPS", 9)
CHALLENGE_MAX_STEPS   = _env_int("CHALLENGE_MAX_STEPS", 15)
CHALLENGE_FREQ_TOP_N  = _env_int("CHALLENGE_FREQ_TOP_N", 20_000)
CHALLENGE_SEED_OFFSET = 100_000  # separates challenge seeds from regular seeds


def is_challenge_day() -> bool:
    return FORCE_CHALLENGE or date.today().weekday() == 2  # Wednesday


def _auth_user(init_data: str) -> tuple[dict, int | None]:
    """
    Verify initData. Returns (user_data, chat_id).
    DEV_SKIP_AUTH=true accepts empty initData as a test user with no chat.
    """
    if DEV_SKIP_AUTH and not init_data:
        return _DEV_USER, None
    return verify_init_data(init_data, BOT_TOKEN)

_words: dict[int, set[str]] = {}   # keyed by word length
_graph: dict[int, dict]     = {}
_puzzles: dict[int, dict]   = {}
_puzzle_days: dict[int, int] = {}

# Challenge mode: top-20k frequency subset, longer chains
_challenge_words: dict[int, set[str]] = {}
_challenge_graph: dict[int, dict]     = {}
_challenge_puzzles: dict[int, dict]   = {}
_challenge_puzzle_days: dict[int, int] = {}

WORD_LENGTHS = (4, 5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from wordfreq import top_n_list
        freq_top = set(top_n_list("en", CHALLENGE_FREQ_TOP_N))
    except ImportError:
        freq_top = set()
        log.warning("wordfreq unavailable — challenge word lists will equal regular lists")

    for length in WORD_LENGTHS:
        raw   = load_words(length)
        graph = build_graph(raw)
        _words[length] = largest_component(graph)
        _graph[length] = graph

        # Challenge set: connected regular words filtered to top-N frequency
        c_raw = {w for w in _words[length] if w in freq_top} if freq_top else _words[length]
        c_graph = build_graph(c_raw)
        _challenge_words[length] = largest_component(c_graph)
        _challenge_graph[length] = c_graph
        log.info("Challenge word set (%dL): %d words", length, len(_challenge_words[length]))

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


def today_puzzle(length: int = 5) -> dict:
    today = date.today().toordinal()

    if is_challenge_day():
        if _challenge_puzzle_days.get(length) != today:
            try:
                start, end, path = pick_puzzle(
                    _challenge_words[length], _challenge_graph[length],
                    min_steps=CHALLENGE_MIN_STEPS, max_steps=CHALLENGE_MAX_STEPS,
                    seed=today + CHALLENGE_SEED_OFFSET,
                )
            except RuntimeError:
                log.warning("Challenge puzzle (%dL): no %d-%d step pair found, widening range",
                            length, CHALLENGE_MIN_STEPS, CHALLENGE_MAX_STEPS)
                start, end, path = pick_puzzle(
                    _challenge_words[length], _challenge_graph[length],
                    min_steps=6, max_steps=12,
                    seed=today + CHALLENGE_SEED_OFFSET,
                )
            _challenge_puzzles[length] = {
                "start":         start,
                "end":           end,
                "optimal_steps": len(path) - 1,
                "day":           game_day(),
                "word_length":   length,
                "is_challenge":  True,
            }
            _challenge_puzzle_days[length] = today
        return _challenge_puzzles[length]

    if _puzzle_days.get(length) != today:
        try:
            start, end, path = pick_puzzle(
                _words[length], _graph[length],
                min_steps=PUZZLE_MIN_STEPS, max_steps=PUZZLE_MAX_STEPS,
            )
        except RuntimeError:
            log.warning("Puzzle (%dL): no %d-%d step pair found, falling back to 4-7",
                        length, PUZZLE_MIN_STEPS, PUZZLE_MAX_STEPS)
            start, end, path = pick_puzzle(_words[length], _graph[length],
                                           min_steps=4, max_steps=7)
        _puzzles[length] = {
            "start":         start,
            "end":           end,
            "optimal_steps": len(path) - 1,
            "day":           game_day(),
            "word_length":   length,
            "is_challenge":  False,
        }
        _puzzle_days[length] = today
    return _puzzles[length]


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


# ── Group message helpers ──────────────────────────────────────────────────────

def _delta_emoji(delta: int) -> str:
    if delta == 0:  return "🎯"
    if delta <= 2:  return "⭐"
    if delta <= 4:  return "😂"
    return "🤡"


def _game_day_for_date(play_date: str) -> int:
    return (date.fromisoformat(play_date) - EPOCH).days + 1


def _lock_squares(word: str, target: str) -> str:
    """Current word rendered as locked-letter squares vs the target."""
    return "".join("🟩" if a == b else "⬜" for a, b in zip(word, target))


async def build_group_message(chat_id: int, play_date: str) -> str:
    """
    Live board (HTML parse mode). group_activity decides WHO is on the board
    for this chat; each player's state is derived from their scores and
    progress by user_id+date — never per-chat — so playing in a new chat
    after solving still shows the result.
    """
    header   = f"🔤 <b>Diddle #{_game_day_for_date(play_date)}</b>"
    par_line = " · ".join(f"{l}-letter par {today_puzzle(l)['optimal_steps']}" for l in WORD_LENGTHS)
    if is_challenge_day():
        par_line = f"😈 Wicked Wednesday — {par_line}"

    activity = await get_group_activity(DB_PATH, chat_id, play_date)
    scored   = await get_group_scores(DB_PATH, chat_id, play_date)

    # Who's on the board: activity is primary; scored users fill gaps
    members: dict[int, str] = {a["user_id"]: a["display_name"] for a in activity}
    for s in scored:
        members.setdefault(s["user_id"], s["display_name"])

    if not members:
        return f"{header}\n{par_line}\n\nNo one has played yet — be first!"

    # Live progress per (user, length) — unique by PK, chat-agnostic
    progress: dict[tuple[int, int], list] = {}
    for p in await get_progress_for_users(DB_PATH, list(members), play_date):
        progress[(p["user_id"], p["word_length"])] = p["path"]

    done_rows:    list[tuple[tuple, str]] = []   # (sort_key, line)
    playing_rows: list[str] = []
    gaveup_rows:  list[str] = []

    for uid, raw_name in members.items():
        name   = html.escape(raw_name)
        scores = await get_user_today_scores(DB_PATH, uid, play_date)
        by_len = {s["word_length"]: s for s in scores}

        segments = []
        for length in WORD_LENGTHS:
            s = by_len.get(length)
            if s:
                if s["gave_up"]:
                    segments.append(f"{length}L 💀")
                elif s["delta"] == 0:
                    segments.append(f"{length}L par")
                else:
                    segments.append(f"{length}L +{s['delta']}")
            else:
                path = progress.get((uid, length))
                if path and len(path) > 1:
                    puz = today_puzzle(length)
                    segments.append(
                        f"{length}L {_lock_squares(path[-1], puz['end'])} "
                        f"{len(path) - 1}/{puz['optimal_steps']}"
                    )

        if len(by_len) == len(WORD_LENGTHS):
            # Finished everything (solved or gave up)
            completed = [s for s in scores if not s["gave_up"]]
            if not completed:
                gaveup_rows.append(f"💀 <b>{name}</b> — gave up")
            else:
                total_delta = sum(s["delta"] for s in completed)
                gaveups     = len(scores) - len(completed)
                line = f"{_delta_emoji(total_delta)} <b>{name}</b> — {' · '.join(segments)}"
                done_rows.append(((gaveups, total_delta), line))
        else:
            playing_rows.append(f"⏱️ <b>{name}</b> — {' · '.join(segments) or 'warming up…'}")

    done_rows.sort(key=lambda x: x[0])
    lines = [header, par_line, ""]
    lines += [line for _, line in done_rows]
    lines += playing_rows
    lines += gaveup_rows
    return "\n".join(lines)


async def edit_group_message(chat_id: int, play_date: str) -> None:
    row = await get_group_message_row(DB_PATH, chat_id, play_date)
    if not row:
        return
    text = await build_group_message(chat_id, play_date)
    payload: dict = {
        "chat_id":    chat_id,
        "message_id": row["message_id"],
        "text":       text,
        "parse_mode": "HTML",
    }
    if row.get("play_url"):
        payload["reply_markup"] = {"inline_keyboard": [[{"text": "Play Diddle 🎮", "url": row["play_url"]}]]}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                json=payload,
                timeout=8.0,
            )
            if not r.is_success:
                body = r.text
                if "message is not modified" not in body:
                    log.warning("editMessageText %s/%s failed: %s", chat_id, row["message_id"], body)
    except Exception as exc:
        log.warning("edit_group_message network error: %s", exc)


class PostGroupMessageRequest(BaseModel):
    chat_id: int
    play_url: str


@app.post("/group_message/post")
async def post_group_message(
    req: PostGroupMessageRequest,
    authorization: str = Header(default=""),
):
    if not (authorization.startswith("bot ") and authorization[4:] == BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Bot auth required")

    play_date = date.today().isoformat()

    # Delete old board message if one exists — silent on failure
    existing = await get_group_message_row(DB_PATH, req.chat_id, play_date)
    if existing:
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
                    json={"chat_id": req.chat_id, "message_id": existing["message_id"]},
                    timeout=8.0,
                )
        except Exception as exc:
            log.warning("deleteMessage failed: %s", exc)

    text = await build_group_message(req.chat_id, play_date)
    keyboard = {"inline_keyboard": [[{"text": "Play Diddle 🎮", "url": req.play_url}]]}

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": req.chat_id, "text": text, "parse_mode": "HTML", "reply_markup": keyboard},
            timeout=10.0,
        )

    if not r.is_success:
        raise HTTPException(status_code=502, detail=f"Telegram sendMessage failed: {r.text}")

    new_message_id = r.json()["result"]["message_id"]
    await upsert_group_message_row(DB_PATH, req.chat_id, new_message_id, play_date, req.play_url)
    return {"message_id": new_message_id}


# ── Public endpoints ───────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "day": game_day()}


@app.get("/puzzle")
async def puzzle(length: int = Query(default=5)):
    if length not in WORD_LENGTHS:
        raise HTTPException(status_code=400, detail=f"length must be one of {WORD_LENGTHS}")
    return today_puzzle(length)


@app.get("/words")
async def words(length: int = Query(default=5)):
    if length not in _words:
        raise HTTPException(status_code=400, detail=f"length must be one of {WORD_LENGTHS}")
    return PlainTextResponse("\n".join(sorted(_words[length])))


# ── Authenticated endpoints ────────────────────────────────────────────────────

class ProgressRequest(BaseModel):
    init_data: str
    path: list[str]
    word_length: int
    chat_id: int | None = None


@app.post("/progress")
async def update_progress(req: ProgressRequest):
    try:
        user, _ = _auth_user(req.init_data)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=403, detail="Could not identify user")
    if req.word_length not in WORD_LENGTHS:
        raise HTTPException(status_code=400, detail=f"word_length must be one of {WORD_LENGTHS}")

    play_date = date.today().isoformat()
    path = [w.strip().lower() for w in req.path]
    await upsert_progress(DB_PATH, user_id, play_date, req.word_length, path, req.chat_id)
    if req.chat_id:
        await edit_group_message(req.chat_id, play_date)
    return {"ok": True}


class PlayingRequest(BaseModel):
    init_data: str
    chat_id: int | None = None


@app.post("/playing")
async def mark_playing(req: PlayingRequest):
    try:
        user, _ = _auth_user(req.init_data)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=403, detail="Could not identify user")
    if req.chat_id:
        display_name = user.get("first_name", "Player")
        if user.get("last_name"):
            display_name += f" {user['last_name']}"
        play_date = date.today().isoformat()
        await upsert_group_activity(DB_PATH, req.chat_id, user_id, display_name, play_date, "playing")
        await edit_group_message(req.chat_id, play_date)
    return {"ok": True}


class ScoreSubmission(BaseModel):
    init_data: str
    path: list[str]
    gave_up: bool
    word_length: int = 5
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

    word_length = submission.word_length
    if word_length not in WORD_LENGTHS:
        raise HTTPException(status_code=400, detail=f"word_length must be one of {WORD_LENGTHS}")
    puz = today_puzzle(word_length)
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
        err = validate(path[i], path[i + 1], _words[word_length])
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

    await delete_progress(DB_PATH, user_id, play_date, word_length)

    if chat_id:
        status = "gaveup" if submission.gave_up else "done"
        await upsert_group_activity(DB_PATH, chat_id, user_id, display_name, play_date, status)
        await edit_group_message(chat_id, play_date)

    board = await get_leaderboard(DB_PATH, play_date, word_length)
    delta = stored["moves"] - optimal
    ordinal = await get_ordinal_position(
        DB_PATH, play_date, word_length,
        stored["submitted_at"], None,
    )

    return {
        "moves":            stored["moves"],
        "optimal":          optimal,
        "delta":            delta,
        "gave_up":          stored["gave_up"],
        "path":             stored["path"],
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
    length: int | None = Query(default=None),
):
    _check_leaderboard_auth(authorization)
    return await get_leaderboard(DB_PATH, date.today().isoformat(), length)


@app.get("/stats/alltime")
async def stats_alltime(
    authorization: str = Header(default=""),
    length: int = Query(default=5),
):
    _check_leaderboard_auth(authorization)
    puz = today_puzzle(length)
    result = await get_alltime_stats(DB_PATH, length)
    return {"puzzle_day": puz["day"], **result}


@app.get("/stats")
async def stats(
    authorization: str = Header(default=""),
    length: int = Query(default=5),
):
    if not authorization.startswith("tma "):
        raise HTTPException(status_code=403, detail="Missing tma token")
    try:
        user, _chat_id = _auth_user(authorization[4:])
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=403, detail="Could not identify user")

    return await get_user_stats(DB_PATH, user_id, length)


@app.get("/me")
async def me(authorization: str = Header(default="")):
    """Today's scores + in-progress paths for the authenticated user."""
    if not authorization.startswith("tma "):
        raise HTTPException(status_code=403, detail="Missing tma token")
    try:
        user, _chat_id = _auth_user(authorization[4:])
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=403, detail="Could not identify user")
    play_date = date.today().isoformat()
    scores   = await get_user_today_scores(DB_PATH, user_id, play_date)
    progress = await get_user_progress(DB_PATH, user_id, play_date)
    return {"scores": scores, "progress": progress}


# StaticFiles must be mounted last — API routes registered above take priority
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
