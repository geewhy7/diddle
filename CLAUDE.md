# Diddle — Claude Code Instructions

## What this project is
A Telegram Mini App word ladder game called Diddle. Daily puzzle, same word
pair for all players seeded by date. Gameplay happens privately inside a
Telegram webview (no chat spam). A live leaderboard message in the group chat
updates in real time as friends play.

---

## Setup context — read this before touching anything
- **Hardware**: Dell mini PC, 24GB RAM (user: pi, hostname: mini — not a Pi)
- **Tunnel**: Cloudflare tunnel → `https://diddle.retard.zone` → `localhost:7113`
  - Cloudflare handles HTTPS. No certbot, no nginx, no SSL config needed.
  - FastAPI runs on port 7113. The tunnel points there. That's it.
- **Bot token**: in `.env` as `TELEGRAM_TOKEN`
- **Frontend**: served as static files by FastAPI at `/` — not a separate server
- **Bot process**: runs in tmux session `clown` on the machine.
  Kill/restart with: `tmux send-keys -t clown C-c ENTER && tmux send-keys -t clown "/usr/bin/python3 bot.py" ENTER`
- **Backend service**: `sudo systemctl restart diddle-backend`
- **DB_PATH** in `.env` is a relative path (`./diddle.db`) — it resolves to
  `backend/diddle.db` because the service runs with `WorkingDirectory=backend/`.
  The bot resolves this correctly via `os.path.abspath(__file__)`.

---

## Current state — what is already fully built and working

The project is **feature-complete and in production**. Do not restructure or
rewrite working code. Read carefully before changing anything.

### backend/game.py — DO NOT MODIFY
Complete word-ladder engine:
- `load_words(length)` — fetches and frequency-filters word list.
  4L uses ENABLE dictionary intersected with wordfreq top-30k.
  5L uses Wordle list intersected with wordfreq top-50k.
- `build_graph(words)` — pattern-grouped adjacency graph, O(n×L)
- `largest_component(graph)` — filters to solvable words only
- `bfs(graph, start, end)` — shortest path
- `pick_puzzle(words, graph)` — daily deterministic puzzle (date-seeded, EPOCH = 2026-06-07)
- `validate(current, guess, words)` — move validation

### backend/db.py — database layer
Tables:
- `scores` — one row per user per puzzle per day; UNIQUE(user_id, play_date, word_length)
- `group_messages` — maps (chat_id, play_date) → message_id for live board
- `group_activity` — tracks each user's status per group per day: 'playing' | 'done' | 'gaveup'

Key functions: `init_db`, `save_score`, `get_leaderboard`, `get_user_stats`,
`get_ordinal_position`, `get_alltime_stats`, `get_user_today_scores`,
`get_group_message_row`, `save_group_message_row`, `upsert_group_activity`,
`get_group_activity`.

### backend/main.py — FastAPI app
Endpoints: `/health`, `/puzzle`, `/words`, `/score`, `/playing`, `/leaderboard`,
`/stats`, `/stats/alltime`, `/me`

Group message helpers: `build_group_message(chat_id, play_date)` and
`edit_group_message(chat_id, play_date)`. The latter calls Telegram's
`editMessageText` API directly via httpx (not through the bot process).

EPOCH = date(2026, 6, 7) for day 1. The current `game_day()` returns day number.

### frontend/ — React app, no build step
- `engine.js` — plain JS: BFS, graph building, puzzle loading from API,
  `Diddle.loadFromAPI(length)`, `Diddle.changedIndex(prev, word)`
- `components.jsx` — `Tiles`, `ChainRow`, `Replay`, `Mark`, `Wordmark`
- `screens.jsx` — `LobbyScreen`, `PlayingScreen`, `FinishedScreen`,
  `GaveUpScreen`, `ResultScreen`, `LeaderboardScreen`, `LoadingScreen`, `ErrorScreen`
- `app.jsx` — root `App` component, all state, API calls
- `diddle.css` — mobile-first, Telegram CSS vars, all layout/animation

Babel compiles JSX at runtime (no build step). Load order in `index.html` matters:
`engine.js` → `components.jsx` → `screens.jsx` → `app.jsx`

### bot/bot.py — slim bot
Commands: `/play`, `/scores`, `/alltime`, `/help`

`/play` in a group: checks DB for an existing daily message; if none, sends the
initial board message and records the message_id. If already sent today, does nothing.
Uses `url=_mini_app_url` (t.me link) — NOT `web_app=WebAppInfo` which is
**private-chat only and will throw `Button_type_invalid` in groups**.

`/play` in a DM: sends a plain button with the t.me link, no group tracking.

---

## Architecture

```
Telegram group chat
    │
    ├─ /play command
    │       │
    │       └─ bot checks group_messages for today
    │               ├─ exists → silent (one board per day)
    │               └─ new → send board message + store message_id
    │                         (url button → t.me/ClownCasinoBot/diddle)
    │
    └─ user taps button → Telegram opens Mini App webview
                            │
                            ▼
                    frontend/index.html       (served by FastAPI)
                            │  fetch()
                            ▼
                    backend/main.py           (FastAPI on port 7113)
                            │  POST /playing → upsert group_activity
                            │  POST /score   → save score, upsert group_activity
                            │  both call edit_group_message (httpx → Telegram API)
                            │
                            ├─ game.py        (word logic, DO NOT MODIFY)
                            ├─ db.py          (aiosqlite)
                            └─ tg.py          (initData verification)

bot/bot.py — separate process (tmux "clown")
    └─ /play, /scores, /alltime, /help
    └─ reads/writes group_messages table directly (same DB)
```

---

## Tech stack — do not change

| Layer       | Choice                         | Reason                                      |
|-------------|--------------------------------|---------------------------------------------|
| Backend     | FastAPI + uvicorn              | Async, serves static files, works with game.py |
| Frontend    | React (Babel, no build step)   | No toolchain, works in Telegram webview     |
| Database    | SQLite + aiosqlite             | File-based, no separate service             |
| Bot         | python-telegram-bot v20        | Async                                       |
| HTTP client | httpx                          | Used by backend to call Telegram editMessageText |
| Tunnel      | Cloudflare (already running)   | HTTPS handled externally                    |
| Process mgr | systemd (backend) + tmux (bot) | Backend is a service; bot runs in tmux      |

Do **not** introduce SQLAlchemy, Docker, Redis, nginx, separate static server,
or a React build step without flagging it first.

---

## File structure (actual)

```
diddle/
├── CLAUDE.md
├── SPEC.md
├── README.md
├── .gitignore
├── .env.example
├── .env                       ← NEVER committed
│
├── backend/
│   ├── main.py                ← FastAPI app, group message helpers
│   ├── game.py                ← word ladder engine (DO NOT MODIFY)
│   ├── db.py                  ← all database operations
│   ├── tg.py                  ← initData HMAC-SHA256 verification
│   ├── test_tg.py             ← unit tests for tg.py
│   ├── requirements.txt
│   └── diddle.db              ← SQLite DB (not committed)
│
├── frontend/
│   ├── index.html             ← single page, loads all scripts
│   ├── diddle.css             ← mobile-first, Telegram theme vars
│   ├── engine.js              ← BFS engine, puzzle loader (plain JS)
│   ├── components.jsx         ← Tiles, ChainRow, Replay (Babel JSX)
│   ├── screens.jsx            ← all screen components (Babel JSX)
│   └── app.jsx                ← App root, all state machine (Babel JSX)
│
├── bot/
│   ├── bot.py                 ← bot commands
│   └── requirements.txt
│
└── deploy/
    ├── diddle-backend.service ← systemd unit for uvicorn
    └── diddle-bot.service     ← systemd unit for bot
```

---

## Environment variables

```
TELEGRAM_TOKEN=          # bot token from BotFather
GAME_URL=https://diddle.retard.zone
BOT_APP_NAME=diddle      # mini app short name (registered with BotFather)
DB_PATH=./diddle.db      # relative to backend/ working dir
BACKEND_URL=http://localhost:7113
CORS_ORIGIN=https://diddle.retard.zone
DEV_SKIP_AUTH=false      # set true only for local testing (no Telegram)
```

See `.env.example` for the full template.

---

## Security — non-negotiable

### initData verification (CRITICAL)
Every authenticated endpoint verifies Telegram's `initData` HMAC-SHA256
signature in `backend/tg.py`. The function `verify_init_data(init_data, bot_token)`
returns the verified user dict or raises `ValueError`.

`_auth_user(init_data)` in main.py wraps this. When `DEV_SKIP_AUTH=true` and
`init_data` is empty, it returns a fake dev user. This must be `false` in prod.

### CORS
`allow_origins` is set to `[CORS_ORIGIN]` from `.env`. Never `"*"`.

---

## Bot button rules (IMPORTANT — learned the hard way)

**`web_app=WebAppInfo(url=...)` buttons only work in private chats.**
Using them in group messages raises `BadRequest: Button_type_invalid`.

For group messages, always use `url=` with the t.me Mini App link:
```python
keyboard = [[InlineKeyboardButton("Play Diddle 🎮", url=_mini_app_url)]]
# where _mini_app_url = f"https://t.me/{bot_username}/{BOT_APP_NAME}"
```

The `edit_group_message` helper in main.py does NOT pass `reply_markup` when
calling `editMessageText` — Telegram preserves the existing button from the
original message. Do not add a `web_app` button to `editMessageText` calls.

---

## API contract (current, complete)

```
GET  /health          → {status: "ok", day: int}
GET  /puzzle?length=N → {start, end, optimal_steps, day, word_length}
GET  /words?length=N  → plain text word list, one per line

POST /playing         → {ok: true}
     body: {init_data, chat_id: int|null}
     Auth: initData. Registers user as 'playing' in group_activity.
     Fire-and-forget from frontend.

POST /score           → {moves, optimal, delta, gave_up, path, rank,
                          ordinal_position, message}
     body: {init_data, path: [str], gave_up: bool,
            word_length: int, invalid_attempts: int, chat_id: int|null}
     Auth: initData. Re-validates full path. Duplicate submissions return
     existing row silently. Updates group_activity and edits group message.

GET  /leaderboard?length=N  → [{name, moves, optimal, gave_up, word_length, avg}]
     Auth: Authorization: tma <init_data>  OR  bot <token>
     avg is the player's all-time avg delta (None if < 3 days played).

GET  /me              → [{word_length, moves, optimal, gave_up, delta, path}]
     Auth: Authorization: tma <init_data>
     Today's scores for the authenticated user. Used for startup sync.

GET  /stats?length=N  → {current_streak, longest_streak, total_played,
                           total_won, total_extra, distribution}
     Auth: Authorization: tma <init_data>

GET  /stats/alltime?length=N → {puzzle_difficulty, players: [{name, handicap, days_played}]}
     Auth: Authorization: tma <init_data>  OR  bot <token>
```

---

## Live group message board — how it works

When `/play` is called in a group:
1. Bot checks `group_messages` table for today's entry. If found → silent.
2. If not found → sends initial message: `"Diddle — Day N 🔤\n\nNo one has played yet — be first!"`
   Records `(chat_id, message_id, today)` in `group_messages`.

When a user opens the Mini App:
3. Frontend fires `POST /playing` (fire-and-forget).
4. Backend upserts `group_activity` with status='playing'.
5. `edit_group_message` calls `editMessageText` via httpx with updated board text.

When a user submits a score:
6. `POST /score` upserts `group_activity` status='done' (win) or 'gaveup'.
7. `edit_group_message` updates the board.

Board format:
```
Diddle — Day 2 🔤

🎯 Karl — 4L perfect · 5L +2
😂 Dan — 4L +3
⏱️ Greg — playing...
💀 Thomas — gave up
```

Emoji mapping: 🎯 par · ⭐ +1/+2 · 😂 +3/+4 · 🤡 +5+ · ⏱️ playing · 💀 gave up

**Known limitation**: When the Mini App is opened via a t.me URL button in a
group, `tg.initDataUnsafe.chat` is null (Telegram does not populate it for
t.me links, only for attachment-menu opens). So `CHAT_ID` is null from the
frontend and group_activity tracking does not fire for group plays. The board
only updates if chat_id somehow reaches the frontend — this is an open problem.
The likely solution is to encode chat_id via the `?startapp=` parameter in the
t.me link and decode it via `tg.initDataUnsafe.start_param` on the frontend.

---

## Emoji / share format

Share text (copy-paste):
```
Diddle #2 (4L) ⭐
6 moves (+2)
```
or both puzzles:
```
Diddle #2
4L · 6 moves +2 ⭐
5L · 7 moves +3 😂
```

---

## Lobby (startup screen)

Two side-by-side puzzle cards (4L and 5L). Tapping an unplayed card zooms
into the game. Tapping a completed card shows their replay path. A Share
button on the lobby copies the day's result to clipboard. A Leaderboard
button opens the leaderboard overlay.

Server is authoritative: `GET /me` at startup syncs `localStorage` with the
DB, so cross-device and corrupted-state scenarios resolve correctly.

---

## Git rules

1. Commit after every logical unit of work.
2. Imperative commit messages: `feat:`, `fix:`, `docs:` prefixes.
3. Before any commit: confirm `.env` is NOT staged.
4. Never force-push to main.
5. The bot is in tmux "clown" — restart manually after bot.py changes.
6. The backend is a systemd service — `sudo systemctl restart diddle-backend`.

---

## Running

```bash
# Backend (systemd service in production)
sudo systemctl restart diddle-backend
sudo systemctl status diddle-backend

# Backend manually (dev)
cd backend
uvicorn main:app --reload --port 7113

# Bot (tmux session "clown")
tmux attach -t clown
# Ctrl-C to stop, then:
/usr/bin/python3 bot.py

# Health check
curl https://diddle.retard.zone/health
```
