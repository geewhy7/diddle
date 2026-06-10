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
- `pick_puzzle(words, graph, min_steps, max_steps, seed)` — daily deterministic puzzle.
  Accepts optional step range and seed — used by challenge mode without modifying this file.
- `validate(current, guess, words)` — move validation

### Challenge mode (Wicked Wednesday)
Every Wednesday (or when `FORCE_CHALLENGE=true` in `.env`) the backend serves harder puzzles:
- **Word set**: regular connected component ∩ wordfreq top-N (`CHALLENGE_FREQ_TOP_N`,
  default 20k — avoids obscure words in long chains)
  → ~1,370 4L words, ~1,200 5L words (built at startup alongside regular sets)
- **Difficulty**: `CHALLENGE_MIN_STEPS`–`CHALLENGE_MAX_STEPS` in .env (default 9–15,
  vs `PUZZLE_MIN_STEPS`–`PUZZLE_MAX_STEPS` default 4–7 normal); falls back to 6–12 if no pair found
- **Seed**: `date.toordinal() + 100_000` (separate from regular puzzle seed)
- **Validation**: still uses the full word set — players can step through any valid word
- **Response**: `/puzzle` includes `is_challenge: bool` — frontend shows a red
  "Wicked Wednesday" stamp on the lobby, red-tinted cards, and 😈 in the header
- `FORCE_CHALLENGE=true` in `.env` lets you test on any day (backend restart required)

### backend/db.py — database layer
Tables:
- `scores` — one row per user per puzzle per day; UNIQUE(user_id, play_date, word_length)
- `group_messages` — maps (chat_id, play_date) → message_id + play_url for live board
- `group_activity` — tracks each user's status per group per day: 'playing' | 'done' | 'gaveup'
- `progress` — in-progress paths; upserted on each valid word, deleted on score submit

Key functions: `init_db`, `save_score`, `get_leaderboard`, `get_user_stats`,
`get_ordinal_position`, `get_alltime_stats`, `get_user_today_scores`,
`get_group_message_row`, `upsert_group_message_row`, `upsert_group_activity`,
`get_group_activity`, `get_group_scores`, `upsert_progress`, `delete_progress`,
`get_user_progress`, `get_progress_for_users`.

### backend/main.py — FastAPI app
Endpoints: `/health`, `/puzzle`, `/words`, `/progress`, `/score`, `/playing`,
`/leaderboard`, `/stats`, `/stats/alltime`, `/me`, `/group_message/post`

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

`/play` in a group: calls backend `POST /group_message/post` with
`play_url = {_mini_app_url}?startapp=g{abs(chat_id)}`. The backend deletes any
existing board for today, sends a fresh one, and records (message_id, play_url).
The bot then deletes the user's `/play` message. Buttons use `url=` (t.me link)
— NOT `web_app=WebAppInfo` which is **private-chat only and will throw
`Button_type_invalid` in groups**.

`/play` in a DM: sends a plain button with the t.me link, no group tracking.

---

## Architecture

```
Telegram group chat
    │
    ├─ /play command
    │       │
    │       └─ bot → POST /group_message/post (backend deletes old board,
    │                 sends fresh one, stores message_id + play_url)
    │                 url button → t.me/ClownCasinoBot/diddle?startapp=g<chat_id>
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
PUZZLE_MIN_STEPS=4       # regular puzzle difficulty range
PUZZLE_MAX_STEPS=7
CHALLENGE_MIN_STEPS=9    # Wicked Wednesday difficulty range
CHALLENGE_MAX_STEPS=15
CHALLENGE_FREQ_TOP_N=20000  # challenge word pool = regular ∩ wordfreq top-N
DEV_SKIP_AUTH=false      # set true only for local testing (no Telegram)
FORCE_CHALLENGE=false    # set true to serve challenge mode on any day
```

See `.env.example` for the full template. `.env` is the single config source
for both processes: systemd injects it into the backend via `EnvironmentFile`,
and the bot loads it via `load_dotenv()`. **No change takes effect until the
relevant process restarts** (backend: `sudo systemctl restart diddle-backend`;
bot: restart in tmux). Changing a difficulty range mid-day re-rolls that day's
puzzle — late submitters against the old puzzle get rejected — so prefer
changing ranges after midnight.

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

The `edit_group_message` helper in main.py re-sends the stored `play_url` as a
url button in `reply_markup` on every `editMessageText` call (editing text
without reply_markup drops the button). Never use a `web_app` button here.

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

GET  /me              → {scores: [{word_length, moves, optimal, gave_up,
                          delta, path}], progress: [{word_length, path, chat_id}]}
     Auth: Authorization: tma <init_data>
     Today's scores + in-progress paths for the user. Used for startup sync
     and mid-puzzle resume.

POST /progress        → {ok: true}
     body: {init_data, path: [str], word_length: int, chat_id: int|null}
     Auth: initData. Upserts in-progress path after each valid move;
     refreshes the group board (live moves/optimal). Fire-and-forget.

POST /group_message/post → {message_id}
     body: {chat_id: int, play_url: str}
     Auth: Authorization: bot <token>. Deletes today's old board message,
     sends a fresh one with the Play button, records it in group_messages.

GET  /stats?length=N  → {current_streak, longest_streak, total_played,
                           total_won, total_extra, distribution}
     Auth: Authorization: tma <init_data>

GET  /stats/alltime?length=N → {puzzle_difficulty, players: [{name, handicap, days_played}]}
     Auth: Authorization: tma <init_data>  OR  bot <token>
```

---

## Live group message board — how it works

When `/play` is called in a group:
1. Bot calls `POST /group_message/post` with the startapp-encoded play_url.
2. Backend deletes today's old board (if any), sends a fresh board message,
   and records `(chat_id, message_id, today, play_url)` in `group_messages`.

When a user opens the Mini App:
3. Frontend fires `POST /playing` (fire-and-forget).
4. Backend upserts `group_activity` with status='playing'.
5. `edit_group_message` calls `editMessageText` via httpx with updated board text.

When a user submits a score:
6. `POST /score` upserts `group_activity` status='done' (win) or 'gaveup'.
7. `edit_group_message` updates the board.

Board format (HTML parse mode, names bolded + escaped; puzzle words never shown):
```
🔤 Diddle #3
4-letter par 4 · 5-letter par 6

🎯 Karl — 4L par · 5L +2
⏱️ Greg — 4L 🟩🟩⬜⬜ 2/4 · 5L +1
💀 Thomas — gave up
```

One segment per puzzle length per player: solved `4L par`/`4L +2`, gave up
`4L 💀`, in progress = lock-squares of current word vs target + `moves/par`,
not started = omitted ("warming up…" if no segments at all). Player state is
**chat-agnostic** (derived from scores/progress by user_id+date); group_activity
only decides who appears on this chat's board.

Emoji mapping: 🎯 par · ⭐ +1/+2 · 😂 +3/+4 · 🤡 +5+ · ⏱️ unfinished · 💀 gave up

**chat_id sourcing**: Telegram does not populate `tg.initDataUnsafe.chat` for
t.me-link opens, so the chat_id rides in as `?startapp=g{abs(chat_id)}` and
`app.jsx` decodes `tg.initDataUnsafe.start_param` back to the negative group
id. Fallbacks: `?chat_id=` URL param (dev), then `initDataUnsafe.chat.id`.

Playing users with a `progress` row show live as `⏱️ Name — 3/7`
(moves so far / optimal) instead of just "playing...".

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
