import html
import json
import logging
import os
import random
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

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
from game import load_words, build_graph, largest_component, pick_puzzle, validate, bfs
from tg import verify_init_data
from db import (
    init_db, save_score, get_leaderboard, get_user_stats, get_ordinal_position,
    get_alltime_stats, get_user_today_scores,
    get_group_message_row, upsert_group_message_row,
    upsert_group_activity,
    get_chat_roster, get_user_board_chats,
    upsert_progress, delete_progress, get_user_progress, get_progress_for_users,
    get_progress_started_at, upsert_day_mode,
)
import board_render

BOT_TOKEN     = os.environ["TELEGRAM_TOKEN"]
DB_PATH       = os.environ.get("DB_PATH", "diddle.db")
GAME_URL      = os.environ.get("GAME_URL", "https://your-domain.example")
DEV_SKIP_AUTH = os.environ.get("DEV_SKIP_AUTH", "").lower() == "true"
FORCE_CHALLENGE = os.environ.get("FORCE_CHALLENGE", "").lower() == "true"
_DEV_USER     = {"id": 999_999, "first_name": "Dev", "username": "devuser"}


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
CHALLENGE_FREQ_TOP_N  = _env_int("CHALLENGE_FREQ_TOP_N", 20_000)   # 4L par pool: general top-N
CHALLENGE_SEED_OFFSET = 100_000  # separates challenge seeds from regular seeds
# 5L par pool = the most-common slice of the 5L base pool (Wordle ∩ Scrabble),
# by frequency. Big enough that ordinary words (e.g. WANDS) are gateways, small
# enough that a rare-word shortcut under par is still possible. The base pool is
# large, so a general top-N cutoff (like 4L's) excludes too much — a percentile
# of the base pool is the right lever here.
CHALLENGE_5L_TOP_PCT  = _env_int("CHALLENGE_5L_TOP_PCT", 85)
# Cap how far under par a hard puzzle can be beaten in the full play set: reject
# (reseed) any 5L pair whose true optimum is more than this below the pool par.
# Keeps under-par rare/small (Birdie/Eagle) and kills "par 9 / solve 3" phantoms.
CHALLENGE_MAX_UNDER   = _env_int("CHALLENGE_MAX_UNDER", 2)
# Same guard for NORMAL puzzles: par is the optimum in the common (selection)
# pool, but a player can type any valid word (full validation set), so a pair
# whose true optimum is far below the common-pool par ships a phantom par
# (par 7 that solves in 4 via a sub-cutoff word like WOKS). Reseed until the
# true optimum is within this many steps of par. Keeps small under-par golf.
PUZZLE_MAX_UNDER      = _env_int("PUZZLE_MAX_UNDER", 1)

# Per-guess shot clock in seconds; 0 disables it (the frontend then shows the
# plain count-up solve timer instead). Enforced client-side only.
GUESS_TIME_LIMIT = _env_int("GUESS_TIME_LIMIT", 0)

# Added to the date seed for ALL puzzles (regular + challenge). Bumping it
# re-rolls every day from now on — the "new era" lever. Like the difficulty
# ranges, changing it mid-day re-rolls that day's puzzle under late submitters.
PUZZLE_SEED_OFFSET = _env_int("PUZZLE_SEED_OFFSET", 0)


def is_challenge_day() -> bool:
    """Hard Mode hits one deterministic-random day each ISO week.
    Salt 'hardmode' is load-bearing: chosen so week 24/2026 (the first Hard
    Mode week) lands on Wednesday — changing it re-rolls every week's day."""
    if FORCE_CHALLENGE:
        return True
    today = date.today()
    year, week, _ = today.isocalendar()
    return today.weekday() == random.Random(f"hardmode-{year}-{week}").randrange(7)


def _auth_user(init_data: str) -> tuple[dict, int | None]:
    """
    Verify initData. Returns (user_data, chat_id).
    DEV_SKIP_AUTH=true accepts empty initData as a test user with no chat.
    """
    if DEV_SKIP_AUTH and not init_data:
        return _DEV_USER, None
    return verify_init_data(init_data, BOT_TOKEN)

_words: dict[int, set[str]] = {}   # SELECTION + par pool (normal puzzle), keyed by length
_graph: dict[int, dict]     = {}
_puzzles: dict[int, dict]   = {}
_puzzle_days: dict[int, int] = {}

# Validation dictionary — what a player is allowed to TYPE (served via /words,
# used by /score). A superset of _words so par stays on the curated pool while
# rarer-but-real words remain playable: 4L = the full Scrabble/ENABLE list,
# 5L = the full Wordle list. Since the 5L selection pool is Wordle ∩ Scrabble,
# Wordle-only words (e.g. LANDE) are typeable but never chosen for puzzles/par.
_valid_words: dict[int, set[str]] = {}
_valid_graph: dict[int, dict]     = {}

# Challenge mode: top-20k frequency subset, longer chains
_challenge_words: dict[int, set[str]] = {}
_challenge_graph: dict[int, dict]     = {}
_challenge_puzzles: dict[int, dict]   = {}
_challenge_puzzle_days: dict[int, int] = {}

WORD_LENGTHS = (4, 5)


def _load_scrabble(length: int) -> set[str]:
    """All valid Scrabble words of a length: dwyl ∩ ENABLE, no frequency cap.
    The 4-letter validation dictionary — 'if Scrabble says it's a word, you can
    play it'. Mirrors game.load_words' sources but skips the freq filter. Built
    in main.py so game.py stays untouched."""
    import urllib.request
    DWYL   = "https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt"
    ENABLE = "https://raw.githubusercontent.com/dolph/dictionary/master/enable1.txt"
    def _grab(url):
        raw = urllib.request.urlopen(url, timeout=20).read().decode()
        return {w.strip().lower() for w in raw.splitlines()
                if len(w.strip()) == length and w.strip().isalpha()}
    return _grab(DWYL) & _grab(ENABLE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from wordfreq import top_n_list, zipf_frequency
        freq_top = set(top_n_list("en", CHALLENGE_FREQ_TOP_N))
    except ImportError:
        freq_top = set()
        zipf_frequency = None
        log.warning("wordfreq unavailable — challenge word lists will equal regular lists")

    for length in WORD_LENGTHS:
        raw   = load_words(length)

        # Selection + par pool. 5L: the curated Wordle play set includes entries
        # that aren't valid Scrabble words (e.g. LANDE) — intersect with the
        # Scrabble list (dwyl ∩ ENABLE) so puzzles and the optimal path never lean
        # on them. Guessing still allows the full Wordle list (set as validation
        # below). Falls back to the unfiltered play set if the Scrabble fetch
        # fails, so puzzles always generate. 4L selection is a common-word pool
        # already, left as-is.
        sel_raw = raw
        if length == 5:
            try:
                filtered = raw & _load_scrabble(length)
                if filtered:
                    sel_raw = filtered
                    log.info("Selection pool (5L): %d words (Wordle ∩ Scrabble, from %d Wordle)",
                             len(filtered), len(raw))
                else:
                    log.warning("Wordle ∩ Scrabble empty (5L) — selection = full Wordle play set")
            except Exception as exc:
                log.warning("Scrabble dict fetch failed (5L) — selection = full Wordle play set: %s", exc)

        graph = build_graph(sel_raw)
        _words[length] = largest_component(graph)
        _graph[length] = graph

        # Hard-mode pools. `_challenge_words` = the pool we pick START/END from
        # (kept common so endpoints are recognizable); `_challenge_graph` = the
        # graph PAR is measured in (the "common words" par is judged against).
        #  • 5L: endpoints from the general top-N (common), but par measured over
        #    the top CHALLENGE_5L_TOP_PCT% of the base pool by frequency. The 5L
        #    base pool is Wordle ∩ Scrabble (non-Scrabble words like LANDE already
        #    dropped above); judging par against only top-N would still drop
        #    ordinary gateways (e.g. WANDS) and inflate par, so the percentile
        #    keeps gateways passable while leaving a rare tail for under-par
        #    shortcuts. The under-par guard in _hard_pick bounds the gap.
        #  • 4L (and other): single pool = play words ∩ general top-N (already
        #    frequency-capped, so endpoints and par share it). Left as-is.
        if length == 5 and zipf_frequency is not None:
            # secondary key = word, so words tied on frequency order the same way
            # every process (set iteration is hash-randomized) — the pool, and
            # thus the daily puzzle, must be identical across backend restarts.
            ordered  = sorted(_words[length], key=lambda w: (-zipf_frequency(w, "en"), w))
            keep     = max(1, int(len(ordered) * CHALLENGE_5L_TOP_PCT / 100))
            par_graph = build_graph(set(ordered[:keep]))
            par_comp  = largest_component(par_graph)
            _challenge_graph[length] = par_graph
            # endpoints = the common (top-N) words within the par component
            common = {w for w in par_comp if w in freq_top} if freq_top else par_comp
            _challenge_words[length] = common or par_comp
        else:
            c_raw = {w for w in _words[length] if w in freq_top} if freq_top else _words[length]
            c_graph = build_graph(c_raw)
            _challenge_words[length] = largest_component(c_graph)
            _challenge_graph[length] = c_graph
        log.info("Hard pool (%dL): %d endpoints, par-graph %d words",
                 length, len(_challenge_words[length]), len(_challenge_graph[length]))

        # Validation dictionary (what you can TYPE). 4L = the full Scrabble list
        # (dwyl ∩ ENABLE), so any real Scrabble word plays even if it's below the
        # common cutoff used for the puzzle/par. 5L = the full Wordle play set (any
        # Wordle word plays as a guess), a SUPERSET of its Scrabble-filtered
        # selection pool above. Falls back to _words on fetch failure so
        # validation never breaks.
        if length == 4:
            try:
                vset = _load_scrabble(length) | _words[length]
                _valid_words[length] = vset
                _valid_graph[length] = build_graph(vset)
                log.info("Validation dict (%dL): %d words (Scrabble/ENABLE)", length, len(vset))
            except Exception as exc:
                log.warning("Scrabble dict fetch failed (%dL) — validating against play set: %s", length, exc)
                _valid_words[length] = _words[length]
                _valid_graph[length] = _graph[length]
        else:
            _valid_words[length] = raw
            _valid_graph[length] = build_graph(raw)
            log.info("Validation dict (%dL): %d words (full Wordle play set)", length, len(raw))

    await init_db(DB_PATH)
    await board_render.start_renderer()   # headless Chromium; text fallback if it fails
    yield
    await board_render.stop_renderer()


app = FastAPI(lifespan=lifespan)

CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "https://your-domain.example")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGIN],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


def _hard_pick(length: int, today: int) -> tuple[str, str, int]:
    """Pick the hard variant's (start, end, par) for a length.

    par = optimal in the challenge (common-word) pool — that's what makes a
    rare-word under-par possible. The guard rejects any pair whose TRUE optimum
    in the full VALIDATION set is more than CHALLENGE_MAX_UNDER below par, so
    under-par stays rare/small and we never ship a phantom par (par 9 that
    solves in 3). Reseeds are deterministic so clients and restarts agree."""
    cw, cg = _challenge_words[length], _challenge_graph[length]
    base = today + CHALLENGE_SEED_OFFSET + PUZZLE_SEED_OFFSET
    cap = CHALLENGE_MAX_UNDER
    attempts = 100
    best = None   # (gap, start, end, par) — closest-to-cap fallback

    def _pick(seed):
        try:
            s, e, p = pick_puzzle(cw, cg, min_steps=CHALLENGE_MIN_STEPS,
                                  max_steps=CHALLENGE_MAX_STEPS, seed=seed)
        except RuntimeError:
            s, e, p = pick_puzzle(cw, cg, min_steps=6, max_steps=12, seed=seed)
        return s, e, len(p) - 1

    for i in range(attempts):
        try:
            s, e, par = _pick(base + i * 7919)   # distinct deterministic reseeds
        except RuntimeError:
            continue
        # real optimum = shortest path in the set players can actually type
        fp = bfs(_valid_graph[length], s, e)
        real = (len(fp) - 1) if fp else par
        gap = par - real
        if gap <= cap:
            return s, e, par
        if best is None or gap < best[0]:
            best = (gap, s, e, par)

    if best is not None:
        log.warning("Hard %dL: no pair within under-par cap %s after %d tries; "
                    "using closest (gap %d)", length, cap, attempts, best[0])
        return best[1], best[2], best[3]
    raise RuntimeError(f"no hard {length}L pair found")


def _normal_pick(length: int, today: int) -> tuple[str, str, int]:
    """Pick the normal variant's (start, end, par) for a length.

    Mirror of _hard_pick's under-par guard, applied to the regular pool: par is
    the optimum in the common selection pool (`_graph`), but the guard reseeds
    deterministically until the TRUE optimum in the full validation set
    (`_valid_graph`) is within PUZZLE_MAX_UNDER of par — so we never ship a
    phantom par (par 7 that solves in 4 via a sub-cutoff word). Small under-par
    golf still possible. game.py untouched."""
    cap = PUZZLE_MAX_UNDER
    base = today + PUZZLE_SEED_OFFSET
    attempts = 100
    best = None   # (gap, start, end, par) — closest-to-cap fallback

    def _pick(seed):
        try:
            s, e, p = pick_puzzle(_words[length], _graph[length],
                                  min_steps=PUZZLE_MIN_STEPS,
                                  max_steps=PUZZLE_MAX_STEPS, seed=seed)
        except RuntimeError:
            s, e, p = pick_puzzle(_words[length], _graph[length],
                                  min_steps=4, max_steps=7, seed=seed)
        return s, e, len(p) - 1

    for i in range(attempts):
        # i == 0 keeps the original (unguarded) seed so most days are unchanged;
        # only days that would ship a phantom par get reseeded.
        try:
            s, e, par = _pick(base + i * 7919)   # distinct deterministic reseeds
        except RuntimeError:
            continue
        fp = bfs(_valid_graph[length], s, e)   # optimum in the typeable set
        real = (len(fp) - 1) if fp else par
        gap = par - real
        if gap <= cap:
            return s, e, par
        if best is None or gap < best[0]:
            best = (gap, s, e, par)

    if best is not None:
        log.warning("Normal %dL: no pair within under-par cap %s after %d tries; "
                    "using closest (gap %d)", length, cap, attempts, best[0])
        return best[1], best[2], best[3]
    raise RuntimeError(f"no normal {length}L pair found")


def today_puzzle(length: int = 5, hard: bool = False) -> dict:
    """Today's puzzle for a length. hard=True returns the Hard Mode variant
    (denser word pool, longer ladder). Both variants are available every day —
    Hard Mode is now a per-player opt-in toggle, not a forced weekly event."""
    today = date.today().toordinal()

    if hard:
        if _challenge_puzzle_days.get(length) != today:
            start, end, optimal = _hard_pick(length, today)
            _challenge_puzzles[length] = {
                "start":         start,
                "end":           end,
                "optimal_steps": optimal,
                "day":           game_day(),
                "word_length":   length,
                "is_challenge":  True,
                "hard_mode":     True,
            }
            _challenge_puzzle_days[length] = today
        return _challenge_puzzles[length]

    if _puzzle_days.get(length) != today:
        start, end, optimal = _normal_pick(length, today)
        _puzzles[length] = {
            "start":         start,
            "end":           end,
            "optimal_steps": optimal,
            "day":           game_day(),
            "word_length":   length,
            "is_challenge":  False,
            "hard_mode":     False,
        }
        _puzzle_days[length] = today
    return _puzzles[length]


def hard_puzzle_or_none(length: int) -> dict | None:
    """Hard variant for a length, or None if no qualifying pair exists (so
    /puzzle degrades to normal-only instead of erroring)."""
    try:
        return today_puzzle(length, hard=True)
    except RuntimeError:
        log.warning("Hard puzzle (%dL) unavailable — no qualifying pair", length)
        return None


def _completed(e: dict) -> bool:
    return not e["gave_up"] and not e.get("timed_out")


def _rank(leaderboard: list[dict], score: int | None, failed: bool) -> int:
    """Rank among completions by hybrid score (lower wins). Failed/DNF or a
    missing score sorts after every scored completion."""
    if failed or score is None:
        return sum(1 for e in leaderboard if _completed(e)) + 1
    return sum(1 for e in leaderboard
               if _completed(e) and e.get("score") is not None and e["score"] < score) + 1


def _message(delta: int, gave_up: bool, timed_out: bool = False) -> str:
    if timed_out:
        return "Out of time!"
    if gave_up:
        return "Better luck tomorrow!"
    if delta == 0:
        return "Perfect!"
    if delta == 1:
        return "So close — 1 over par!"
    return f"+{delta} over par"


# ── Group message helpers ──────────────────────────────────────────────────────

def _delta_emoji(delta: int) -> str:
    if delta < 0:   return "🦅"   # under par — golf!
    if delta == 0:  return "🎯"
    if delta <= 2:  return "⭐"
    if delta <= 4:  return "😂"
    return "🤡"


def _delta_tag(delta: int) -> str:
    """Par-relative tag for the score display: P (par) / +N (over) / −N (under)."""
    return "P" if delta == 0 else (f"+{delta}" if delta > 0 else f"−{-delta}")


def _game_day_for_date(play_date: str) -> int:
    return (date.fromisoformat(play_date) - EPOCH).days + 1


def _lock_squares(word: str, target: str) -> str:
    """Current word rendered as locked-letter squares vs the target."""
    return "".join("🟩" if a == b else "⬜" for a, b in zip(word, target))


def _ordinal(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _date_label(play_date: str) -> str:
    d = date.fromisoformat(play_date)
    return f"{d.strftime('%B')} {_ordinal(d.day)}"


def _fmt_secs(total: int) -> str:
    m, s = divmod(int(total), 60)
    return f"{m}:{s:02d}"


async def compute_board_data(chat_id: int, play_date: str) -> dict:
    """Structured leaderboard for the image renderer (and could feed text too).

    Ranked = players who solved BOTH holes, ordered by total delta then total
    play time (sum of both solves). Everyone else (still playing, one hole only,
    gave up, timed out) lands in `others`, failures last. Player state is derived
    from scores + progress by user_id, never per-chat.

    Membership is the chat's *all-time* roster (everyone who ever interacted
    from this chat), but only members with activity TODAY are shown — a roster
    member who hasn't touched today's puzzle is omitted, not listed blank.
    """
    members = await get_chat_roster(DB_PATH, chat_id)

    # progress + scores keyed so a cohort pass can filter by variant (hard?)
    progress: dict[tuple[int, int, bool], list] = {}
    for p in await get_progress_for_users(DB_PATH, list(members), play_date):
        progress[(p["user_id"], p["word_length"], p["hard_mode"])] = p["path"]

    user_scores: dict[int, list] = {}
    for uid in members:
        user_scores[uid] = await get_user_today_scores(DB_PATH, uid, play_date)

    normal = _cohort_board(members, user_scores, progress, hard=False)
    hard   = _cohort_board(members, user_scores, progress, hard=True)

    hard_pars = {l: (hp["optimal_steps"] if (hp := hard_puzzle_or_none(l)) else None)
                 for l in WORD_LENGTHS}

    return {
        "day":          _game_day_for_date(play_date),
        "date_label":   _date_label(play_date),
        "pars":         {l: today_puzzle(l)["optimal_steps"] for l in WORD_LENGTHS},
        "hard_pars":    hard_pars,
        "ranked":       normal["ranked"],
        "others":       normal["others"],
        "hard_ranked":  hard["ranked"],
        "hard_others":  hard["others"],
        "has_hard":     bool(hard["ranked"] or hard["others"]),
    }


def _cohort_board(members: dict, user_scores: dict, progress: dict, hard: bool) -> dict:
    """One board cohort (normal or hard) from already-fetched scores/progress.

    Identical ranking logic for both variants — a player appears in whichever
    cohort(s) they have plays for, so doing normal-4L and hard-5L puts them on
    both boards. Scores/progress are filtered to this variant by hard_mode.
    """
    ranked_tmp = []   # (total_delta, time_sort, name, holes, time_str)
    others_tmp = []   # (all_failed, name_lower, name, holes)

    for uid, raw_name in members.items():
        by_len = {s["word_length"]: s for s in user_scores[uid]
                  if bool(s["hard_mode"]) == hard}

        holes: dict[int, dict] = {}
        solved_both = True
        total_score = 0
        score_missing = False

        for length in WORD_LENGTHS:
            s = by_len.get(length)
            if s and not s["gave_up"] and not s["timed_out"]:
                # Hybrid score = solve seconds + 60 per move. delta + clean-clock
                # flag (ta) drive only the pill colour/superscript/⚡, not the rank.
                hole_score = (s["solve_seconds"] + s["moves"] * 60) \
                    if s.get("solve_seconds") is not None else None
                holes[length] = {"kind": "done", "delta": s["delta"],
                                 "ta": bool(s["time_attack"]), "score": hole_score}
                if hole_score is None:
                    score_missing = True
                else:
                    total_score += hole_score
            elif s and s["gave_up"]:
                holes[length] = {"kind": "gaveup"}; solved_both = False
            elif s and s["timed_out"]:
                holes[length] = {"kind": "timedout"}; solved_both = False
            else:
                path = progress.get((uid, length, hard))
                holes[length] = {"kind": "playing"} if (path and len(path) > 1) else {"kind": "none"}
                solved_both = False

        # No activity in this variant today — omit rather than a blank row.
        if all(holes[l]["kind"] == "none" for l in WORD_LENGTHS):
            continue

        if solved_both:
            sort_key  = 10**12 if score_missing else total_score
            score_str = "—" if score_missing else str(total_score)
            ranked_tmp.append((sort_key, raw_name, holes, score_str))
        else:
            all_failed = all(holes[l]["kind"] in ("gaveup", "timedout") for l in WORD_LENGTHS)
            others_tmp.append((all_failed, raw_name.lower(), raw_name, holes))

    ranked_tmp.sort(key=lambda x: (x[0], x[1].lower()))
    others_tmp.sort(key=lambda x: (x[0], x[1]))

    return {
        "ranked": [{"rank": i + 1, "name": n, "holes": h, "score": sc}
                   for i, (k, n, h, sc) in enumerate(ranked_tmp)],
        "others": [{"name": n, "holes": h} for (_, _, n, h) in others_tmp],
    }


def _text_cohort(members: dict, user_scores: dict, progress: dict, hard: bool) -> list[str]:
    """Formatted player lines for one variant (normal or hard) of the text
    board — the legacy fallback when Chromium is down. Filters scores/progress
    by hard_mode so the same logic builds both sections."""
    done_rows:    list[tuple[tuple, str]] = []   # (sort_key, line)
    playing_rows: list[str] = []
    failed_rows:  list[str] = []                 # all puzzles gave up / timed out

    for uid, raw_name in members.items():
        name   = html.escape(raw_name)
        scores = [s for s in user_scores[uid] if bool(s["hard_mode"]) == hard]
        by_len = {s["word_length"]: s for s in scores}

        segments = []
        for length in WORD_LENGTHS:
            s = by_len.get(length)
            if s:
                # ⚡ flags a clean time-attack solve (clock never lapsed)
                tt = "⚡" if (s["time_attack"] and not s["gave_up"] and not s["timed_out"]) else ""
                if s["gave_up"]:
                    segments.append(f"{length}L 💀")
                elif s["timed_out"]:
                    segments.append(f"{length}L ⌛")
                else:
                    sc = (s["solve_seconds"] + s["moves"] * 60) if s.get("solve_seconds") is not None else None
                    segments.append(f"{length}L {sc if sc is not None else '—'} {_delta_tag(s['delta'])}{tt}")
            else:
                path = progress.get((uid, length, hard))
                if path and len(path) > 1:
                    puz = today_puzzle(length, hard=hard)
                    segments.append(
                        f"{length}L {_lock_squares(path[-1], puz['end'])} "
                        f"{len(path) - 1}/{puz['optimal_steps']}"
                    )

        # Nothing in this variant today (no score, no live progress) — omit.
        if not segments:
            continue

        if len(by_len) == len(WORD_LENGTHS):
            # Finished everything (solved, gave up, or timed out)
            completed = [s for s in scores if not s["gave_up"] and not s["timed_out"]]
            if not completed:
                if all(s["timed_out"] for s in scores):
                    failed_rows.append(f"⌛ <b>{name}</b> — out of time")
                elif all(s["gave_up"] for s in scores):
                    failed_rows.append(f"💀 <b>{name}</b> — gave up")
                else:
                    failed_rows.append(f"💀 <b>{name}</b> — {' · '.join(segments)}")
            else:
                secs = [c["solve_seconds"] for c in completed]
                total_score = (sum(secs) + sum(c["moves"] for c in completed) * 60) \
                    if all(v is not None for v in secs) else None
                failures   = len(scores) - len(completed)
                sort_key   = (failures, total_score if total_score is not None else 10**12)
                total_delta = sum(c["delta"] for c in completed)
                tot = total_score if total_score is not None else "—"
                line = f"{_delta_emoji(total_delta)} <b>{name}</b> · {tot} — {' · '.join(segments)}"
                done_rows.append((sort_key, line))
        else:
            playing_rows.append(f"⏱️ <b>{name}</b> — {' · '.join(segments) or 'warming up…'}")

    done_rows.sort(key=lambda x: x[0])
    return [line for _, line in done_rows] + playing_rows + failed_rows


def _text_par_line(hard: bool) -> str:
    parts = []
    for l in WORD_LENGTHS:
        puz = hard_puzzle_or_none(l) if hard else today_puzzle(l)
        if puz:
            parts.append(f"{l}-letter par {puz['optimal_steps']}")
    return " · ".join(parts)


async def build_group_message(chat_id: int, play_date: str) -> str:
    """
    Live board (HTML parse mode). group_activity decides WHO is on the board
    for this chat; each player's state is derived from their scores and
    progress by user_id+date — never per-chat — so playing in a new chat
    after solving still shows the result. Normal and 😈 hard plays render as
    two stacked sections (the hard one only when someone played hard).
    """
    header   = f"🔤 <b>Diddle #{_game_day_for_date(play_date)}</b>"
    par_line = _text_par_line(hard=False)

    # All-time roster of this chat; today's activity decides who actually shows.
    members = await get_chat_roster(DB_PATH, chat_id)

    if not members:
        return f"{header}\n{par_line}\n\nNo one has played yet — be first!"

    # Live progress per (user, length, hard) — unique by PK, chat-agnostic
    progress: dict[tuple[int, int, bool], list] = {}
    for p in await get_progress_for_users(DB_PATH, list(members), play_date):
        progress[(p["user_id"], p["word_length"], p["hard_mode"])] = p["path"]

    user_scores: dict[int, list] = {}
    for uid in members:
        user_scores[uid] = await get_user_today_scores(DB_PATH, uid, play_date)

    normal_rows = _text_cohort(members, user_scores, progress, hard=False)
    hard_rows   = _text_cohort(members, user_scores, progress, hard=True)

    lines = [header, par_line, ""] + normal_rows
    if hard_rows:
        lines += ["", "😈 <b>HARD MODE</b>", _text_par_line(hard=True), ""] + hard_rows
    return "\n".join(lines)


def _play_keyboard(play_url: str | None) -> dict | None:
    if not play_url:
        return None
    return {"inline_keyboard": [[{"text": "Play Diddle 🎮", "url": play_url}]]}


async def _tg_send_photo(chat_id: int, png: bytes, keyboard: dict | None) -> int | None:
    """sendPhoto (multipart). Returns the new message_id, or None on failure."""
    data = {"chat_id": str(chat_id)}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data=data, files={"photo": ("board.png", png, "image/png")},
                timeout=20.0,
            )
        if r.is_success:
            return r.json()["result"]["message_id"]
        log.warning("sendPhoto %s failed: %s", chat_id, r.text)
    except Exception as exc:
        log.warning("sendPhoto network error: %s", exc)
    return None


async def _tg_edit_photo(chat_id: int, message_id: int, png: bytes, keyboard: dict | None) -> bool:
    """editMessageMedia replacing the photo (multipart). Returns success."""
    media = {"type": "photo", "media": "attach://board"}
    data = {"chat_id": str(chat_id), "message_id": str(message_id), "media": json.dumps(media)}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageMedia",
                data=data, files={"board": ("board.png", png, "image/png")},
                timeout=20.0,
            )
        if r.is_success:
            return True
        log.warning("editMessageMedia %s/%s failed: %s", chat_id, message_id, r.text)
    except Exception as exc:
        log.warning("editMessageMedia network error: %s", exc)
    return False


async def _render_board(chat_id: int, play_date: str) -> bytes | None:
    if not board_render.renderer_ready():
        return None
    data = await compute_board_data(chat_id, play_date)
    return await board_render.render_board_png(data)


async def edit_group_message(chat_id: int, play_date: str) -> None:
    """Refresh the board in place. Photo boards re-render via editMessageMedia;
    text boards (renderer was down at post time) keep using editMessageText."""
    row = await get_group_message_row(DB_PATH, chat_id, play_date)
    if not row:
        return
    keyboard = _play_keyboard(row.get("play_url"))

    if row.get("is_photo"):
        png = await _render_board(chat_id, play_date)
        if png:
            await _tg_edit_photo(chat_id, row["message_id"], png, keyboard)
        # render unavailable → leave the last good image rather than break the message
        return

    # text board fallback path
    text = await build_group_message(chat_id, play_date)
    payload: dict = {
        "chat_id": chat_id, "message_id": row["message_id"],
        "text": text, "parse_mode": "HTML",
    }
    if keyboard:
        payload["reply_markup"] = keyboard
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                json=payload, timeout=8.0,
            )
            if not r.is_success and "message is not modified" not in r.text:
                log.warning("editMessageText %s/%s failed: %s", chat_id, row["message_id"], r.text)
    except Exception as exc:
        log.warning("edit_group_message network error: %s", exc)


async def refresh_user_boards(user_id: int, play_date: str, ensure_chat_id: int | None = None) -> None:
    """Re-render every board that should reflect this user's play — i.e. all
    chats whose all-time roster includes them and that have a board today, not
    just the chat they played from. Renders are serialised by board_render's
    lock; finishes/joins are infrequent so the fan-out cost is fine."""
    chats = set(await get_user_board_chats(DB_PATH, user_id, play_date))
    if ensure_chat_id is not None:
        chats.add(ensure_chat_id)
    for cid in chats:
        await edit_group_message(cid, play_date)


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

    keyboard = _play_keyboard(req.play_url)

    # Prefer a rendered photo board; fall back to the text board if Chromium
    # is unavailable. is_photo records which, so edits use the right API.
    png = await _render_board(req.chat_id, play_date)
    if png:
        message_id = await _tg_send_photo(req.chat_id, png, keyboard)
        if message_id is not None:
            await upsert_group_message_row(DB_PATH, req.chat_id, message_id, play_date,
                                           req.play_url, is_photo=True)
            return {"message_id": message_id}
        # photo send failed unexpectedly — fall through to text

    text = await build_group_message(req.chat_id, play_date)
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": req.chat_id, "text": text, "parse_mode": "HTML",
                  "reply_markup": keyboard},
            timeout=10.0,
        )
    if not r.is_success:
        raise HTTPException(status_code=502, detail=f"Telegram sendMessage failed: {r.text}")

    new_message_id = r.json()["result"]["message_id"]
    await upsert_group_message_row(DB_PATH, req.chat_id, new_message_id, play_date,
                                   req.play_url, is_photo=False)
    return {"message_id": new_message_id}


# ── Public endpoints ───────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "day": game_day()}


_day_mode_logged: str | None = None   # last play_date written to day_modes


@app.get("/puzzle")
async def puzzle(length: int = Query(default=5)):
    if length not in WORD_LENGTHS:
        raise HTTPException(status_code=400, detail=f"length must be one of {WORD_LENGTHS}")
    # Record what kind of day this is, once per day (re-stamped after a
    # restart, so a mid-day config change updates the row too).
    global _day_mode_logged
    play_date = date.today().isoformat()
    if _day_mode_logged != play_date:
        # hard_mode is now a per-player toggle (available every day), so the
        # day-level "hard day" flag is always False; the column is vestigial.
        await upsert_day_mode(DB_PATH, play_date, False, GUESS_TIME_LIMIT)
        _day_mode_logged = play_date

    resp = {**today_puzzle(length), "time_limit": GUESS_TIME_LIMIT}
    hard = hard_puzzle_or_none(length)
    resp["hard_available"] = hard is not None
    if hard is not None:
        resp["hard"] = {
            "start":         hard["start"],
            "end":           hard["end"],
            "optimal_steps": hard["optimal_steps"],
        }
    return resp


@app.get("/words")
async def words(length: int = Query(default=5)):
    if length not in _valid_words:
        raise HTTPException(status_code=400, detail=f"length must be one of {WORD_LENGTHS}")
    # the full validation dictionary (what the client may type / BFS over)
    return PlainTextResponse("\n".join(sorted(_valid_words[length])))


# ── Authenticated endpoints ────────────────────────────────────────────────────

class ProgressRequest(BaseModel):
    init_data: str
    path: list[str]
    word_length: int
    chat_id: int | None = None
    hard_mode: bool = False


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
    await upsert_progress(DB_PATH, user_id, play_date, req.word_length, path, req.chat_id, req.hard_mode)
    # NOTE: no board refresh per move — the photo board updates on join (/playing)
    # and on each finish (/score), not on every guess (rendering + re-uploading a
    # PNG per keystroke would be far too heavy and hit Telegram edit limits).
    # The stored progress still feeds the next render's "playing" state.
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
        await refresh_user_boards(user_id, play_date, ensure_chat_id=req.chat_id)
    return {"ok": True}


class ScoreSubmission(BaseModel):
    init_data: str
    path: list[str]
    gave_up: bool
    timed_out: bool = False
    time_attack: bool = False
    word_length: int = 5
    invalid_attempts: int = 0
    chat_id: int | None = None
    hard_mode: bool = False


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
    hard_mode = submission.hard_mode
    # Validate against the variant the player actually played — hard runs have a
    # different start/end/par, so using the wrong one would reject every hard solve.
    puz = today_puzzle(word_length, hard=hard_mode)
    start, end, optimal = puz["start"], puz["end"], puz["optimal_steps"]

    # Normalise to lowercase — never trust client casing
    path = [w.strip().lower() for w in submission.path]

    # Re-validate the full submitted path before touching the database
    if not path:
        raise HTTPException(status_code=422, detail="Path cannot be empty")
    if path[0] != start:
        raise HTTPException(status_code=422, detail=f"Path must start with '{start}'")
    if not submission.gave_up and not submission.timed_out and path[-1] != end:
        raise HTTPException(status_code=422, detail=f"Path must end with '{end}'")
    for i in range(len(path) - 1):
        err = validate(path[i], path[i + 1], _valid_words[word_length])
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

    # Solve time: server clock from first reveal (progress.started_at, stamped
    # when the player first opened the puzzle) to now. Wall-clock on purpose —
    # leaving the app doesn't pause it.
    solve_seconds = None
    started_at = await get_progress_started_at(DB_PATH, user_id, play_date, word_length, hard_mode)
    if started_at:
        started = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        solve_seconds = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))

    # A timeout can only happen with the clock on, so it implies time attack.
    time_attack = submission.time_attack or submission.timed_out

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
        solve_seconds=solve_seconds,
        timed_out=submission.timed_out,
        time_attack=time_attack,
        hard_mode=hard_mode,
    )

    await delete_progress(DB_PATH, user_id, play_date, word_length, hard_mode)

    if chat_id:
        if submission.gave_up:
            status = "gaveup"
        elif submission.timed_out:
            status = "timedout"
        else:
            status = "done"
        await upsert_group_activity(DB_PATH, chat_id, user_id, display_name, play_date, status)
        await refresh_user_boards(user_id, play_date, ensure_chat_id=chat_id)

    # Rank within the same variant — a hard solve competes against hard solves.
    board = [r for r in await get_leaderboard(DB_PATH, play_date, word_length)
             if bool(r["hard_mode"]) == hard_mode]
    delta = stored["moves"] - optimal
    failed = stored["gave_up"] or stored["timed_out"]
    # Hybrid score = solve seconds + 60 per move (lower wins); None if untimed legacy.
    score = (stored["solve_seconds"] + stored["moves"] * 60) if (
        not failed and stored["solve_seconds"] is not None) else None
    ordinal = await get_ordinal_position(
        DB_PATH, play_date, word_length,
        stored["submitted_at"], None, hard_mode,
    )

    return {
        "moves":            stored["moves"],
        "optimal":          optimal,
        "delta":            delta,
        "gave_up":          stored["gave_up"],
        "timed_out":        stored["timed_out"],
        "time_attack":      stored["time_attack"],
        "hard_mode":        stored["hard_mode"],
        "path":             stored["path"],
        "solve_seconds":    stored["solve_seconds"],
        "score":            score,
        "rank":             _rank(board, score, failed),
        "ordinal_position": ordinal,
        "message":          _message(delta, stored["gave_up"], stored["timed_out"]),
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
