# Diddle — Claude Code Instructions

## What this project is
A Telegram Mini App word ladder game called Diddle. Daily puzzle, same word
pair for all players seeded by date. Gameplay happens privately inside a
Telegram webview (no chat spam). Leaderboard posts to the group.
Friends compete on par score.

## Setup context — read this before touching anything
- **Hardware**: Dell mini PC, 24GB RAM (user: pi, hostname: mini — not a Pi despite the name)
- **Tunnel**: Cloudflare tunnel → `https://diddle.retard.zone` → `localhost:7113`
  - Cloudflare handles HTTPS. No certbot, no nginx, no SSL config needed.
  - FastAPI runs on port 7113. The tunnel points there. That's it.
- **Bot token**: in `.env` as `TELEGRAM_TOKEN`
- **Game short name**: `diddle` (registered with BotFather but NOT used —
  we use Mini Apps, not the old Games API)
- **Frontend**: served as static files by FastAPI at `/` — not a separate server

## Current state — what already exists
`backend/game.py` contains the complete, tested word-ladder engine:
- `load_words(length)` — fetches and frequency-filters the word list
- `build_graph(words)` — pattern-grouped adjacency graph, O(n×L)
- `largest_component(graph)` — filters to solvable words only
- `bfs(graph, start, end)` — shortest path
- `pick_puzzle(words, graph)` — daily deterministic puzzle (date-seeded)
- `validate(current, guess, words)` — move validation

**Do not rewrite or restructure this file.** Wrap it; don't replace it.

---

## Architecture — decided, do not relitigate

```
Telegram group chat
    │
    ├─ /play command
    │       │
    │       └─ bot sends message with inline [Play Diddle 🎮] button
    │               (WebAppInfo, url = https://diddle.retard.zone)
    │
    └─ button tapped → Telegram opens Mini App webview
                            │
                            ▼
                    frontend/index.html       (served by FastAPI)
                            │  fetch()
                            ▼
                    backend/main.py           (FastAPI on port 7113)
                            │
                            ├─ game.py        (word logic, already done)
                            ├─ db.py          (aiosqlite)
                            └─ tg.py          (initData verification)
                            │
                    bot/bot.py                (python-telegram-bot, slim)
                            └─ sends Mini App button, handles /scores
```

The Cloudflare tunnel routes `diddle.retard.zone` → `localhost:7113`.
FastAPI handles everything on that port: API routes AND static frontend files.
No nginx. No separate static file server.

---

## Tech stack — decided, do not change

| Layer       | Choice                       | Reason                                     |
|-------------|------------------------------|--------------------------------------------|
| Backend     | FastAPI + uvicorn            | Async, serves static files, works with game.py |
| Frontend    | Vanilla HTML/CSS/JS          | No build step, 3 files, works in Telegram  |
| Database    | SQLite + aiosqlite           | File-based, no separate service            |
| Bot         | python-telegram-bot v20      | Async, already used                        |
| Tunnel      | Cloudflare (already running) | HTTPS handled externally, no config needed |
| Process mgr | systemd                      | Reliable, built into Linux                 |

Do **not** introduce React, Vue, SQLAlchemy, Docker, Redis, nginx, or any
other dependencies not listed here without flagging it first.

---

## File structure — follow exactly

```
diddle/
├── CLAUDE.md
├── SPEC.md
├── README.md
├── .gitignore
├── .env.example               ← committed, documents all vars
├── .env                       ← NEVER committed
├── .git/hooks/pre-commit      ← blocks .env commits
│
├── backend/
│   ├── main.py                ← FastAPI app + static file serving
│   ├── game.py                ← word ladder engine (DO NOT MODIFY)
│   ├── db.py                  ← all database operations
│   ├── tg.py                  ← initData verification
│   └── requirements.txt
│
├── frontend/
│   ├── index.html             ← single page, all game states
│   ├── style.css              ← mobile-first, Telegram theme vars
│   └── game.js                ← game state machine, fetch calls
│
├── bot/
│   ├── bot.py                 ← /play (Mini App button), /scores, /help
│   └── requirements.txt
│
└── deploy/
    ├── diddle-backend.service ← systemd unit for uvicorn
    └── diddle-bot.service     ← systemd unit for bot
```

---

## Environment variables

Always read from `os.environ` or python-dotenv. Never hardcode.

```python
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.environ["TELEGRAM_TOKEN"]
```

See `.env.example` for all variables.

---

## Security — non-negotiable

### initData verification (CRITICAL)
Every request from the frontend that identifies a user MUST verify Telegram's
`initData` signature. Implement in `backend/tg.py`:

```python
import hashlib, hmac, json
from urllib.parse import parse_qsl, unquote

def verify_init_data(init_data: str, bot_token: str) -> dict:
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("Missing hash")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected   = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise ValueError("Invalid hash — reject request")
    return json.loads(unquote(parsed.get("user", "{}")))
```

Write a unit test for this before building anything that depends on it.

### CORS
Set `allow_origins` to `["https://diddle.retard.zone"]` — never `"*"`.

---

## Bot — how /play works (Mini Apps, not Games API)

```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

keyboard = [[
    InlineKeyboardButton(
        "Play Diddle 🎮",
        web_app=WebAppInfo(url="https://diddle.retard.zone")
    )
]]
await update.message.reply_text(
    "Today's puzzle is ready 🔤",
    reply_markup=InlineKeyboardMarkup(keyboard)
)
```

The bot does NOT use `sendGame()` or the Games API. Do not reference
`GAME_SHORT_NAME` in bot code.

---

## Frontend rules

- **Mobile-first.** Telegram users are overwhelmingly on mobile.
- **Telegram theme variables** — the app must match the user's Telegram theme:
  ```css
  background-color: var(--tg-theme-bg-color);
  color: var(--tg-theme-text-color);
  button-color: var(--tg-theme-button-color);
  ```
- **No external fonts or icon libraries.**
- **One HTML file.** CSS classes toggle between states: loading, playing,
  finished, gave-up, error.
- **MainButton** for primary CTA (Share Score when done).
- Initialise SDK immediately on load:
  ```js
  const tg = window.Telegram.WebApp;
  tg.ready();
  tg.expand();
  tg.disableVerticalSwipes();
  const initData = tg.initData; // send with every API call
  ```

---

## API contract

```
GET  /health        → {status: "ok", day: int}
GET  /puzzle        → {start, end, optimal_steps, day, word_length}
GET  /words         → plain text list, one word per line (for client validation)
POST /score         → records score, returns leaderboard position
     body: {init_data, path: [str], gave_up: bool}
GET  /leaderboard   → today's scores, auth via Authorization: tma <init_data>
```

`/score` and `/leaderboard` reject requests with invalid or missing initData.

---

## Git rules

1. Commit after every logical unit of work.
2. Commit messages: imperative, specific. `Add initData verification` not `wip`
3. Work in feature branches, merge to main when done.
4. Before any commit: `git status` — confirm `.env` is not staged.
5. Never force-push to main.

Suggested commit order:
```
feat: backend directory structure and requirements
feat: FastAPI skeleton with /health /puzzle /words endpoints
feat: initData verification with unit test
feat: database schema and /score /leaderboard endpoints
feat: frontend game UI (index.html, style.css, game.js)
feat: bot /play Mini App button and /scores leaderboard
feat: systemd service units
docs: README with run instructions
```

---

## Running locally

```bash
# Backend (serves API + frontend static files)
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 7113

# Bot (separate terminal)
cd bot
pip install -r requirements.txt
python3 bot.py

# The Cloudflare tunnel is already configured and running separately.
# https://diddle.retard.zone → localhost:7113
```

---

## Definition of done (per feature)

- [ ] Code written and manually tested
- [ ] Edge cases handled (invalid input, network errors, missing env vars)
- [ ] No secrets in code
- [ ] Committed with descriptive message
