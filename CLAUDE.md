# Word Ladder — Claude Code Instructions

## What this project is
A Telegram Mini App word ladder game. Daily puzzle, same word pair for all
players seeded by date. Gameplay happens privately inside a Telegram webview
(no chat spam). Leaderboard posts to the group. Friends compete on par score.

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
    ├─ /play command → bot sends Game message with [Play] button
    │
    └─ [Play] tapped → Telegram opens GAME_URL in webview
                            │
                            ▼
                    frontend/index.html   (vanilla JS, no framework)
                            │  fetch()
                            ▼
                    backend/main.py       (FastAPI + uvicorn)
                            │
                            ├─ game.py   (word logic, already done)
                            ├─ db.py     (aiosqlite)
                            └─ tg.py     (initData verification, score API)
                            │
                    bot/bot.py            (python-telegram-bot, slim)
                            │
                            └─ sends Game, handles /scores leaderboard
```


---

## Tech stack — decided, do not change

| Layer       | Choice              | Reason                                      |
|-------------|---------------------|---------------------------------------------|
| Backend     | FastAPI + uvicorn   | Async, fast, auto-docs, works with game.py  |
| Frontend    | Vanilla HTML/CSS/JS | No build step, simpler deploy, works in TG  |
| Database    | SQLite + aiosqlite  | File-based, no separate service, upgradeable|
| Bot         | python-telegram-bot | Already used, async v20                     |
| Web server  | cloudflared to localhost port 7113               | Reverse proxy + serve frontend static files |
| Process mgr | systemd             | Reliable, built into Linux                  |

Do **not** introduce React, Vue, SQLAlchemy, Docker, Redis, or any other
dependencies not listed here without flagging it first.

---

## File structure — follow exactly

```
word-ladder/
├── CLAUDE.md                  ← this file
├── SPEC.md                    ← full product spec
├── README.md                  ← setup instructions
├── .gitignore
├── .env.example               ← committed, documents all vars
├── .env                       ← NEVER committed (see rules)
├── .git/hooks/pre-commit      ← blocks .env commits
│
├── backend/
│   ├── main.py                ← FastAPI app, API routes
│   ├── game.py                ← word ladder engine (DO NOT MODIFY)
│   ├── db.py                  ← all database operations
│   ├── tg.py                  ← initData verification + Telegram API calls
│   └── requirements.txt
│
├── frontend/
│   ├── index.html             ← single page, all game states
│   ├── style.css              ← mobile-first, Telegram theme vars
│   └── game.js                ← game state machine, fetch calls
│
├── bot/
│   ├── bot.py                 ← sendGame, /scores, /help only
│   └── requirements.txt
│
└── deploy/
    ├── nginx.conf             ← server block config
    ├── word-ladder-backend.service   ← systemd unit
    └── word-ladder-bot.service       ← systemd unit
```

---

## Environment variables

**Always read from `os.environ` or python-dotenv. Never hardcode.**
See `.env.example` for all variables and their purpose.

```python
# Correct
TOKEN = os.environ["TELEGRAM_TOKEN"]

# Wrong
TOKEN = "123456:ABC..."
```

Load dotenv at the top of every entry-point file:
```python
from dotenv import load_dotenv
load_dotenv()
```

---

## Security — non-negotiable

### initData verification (CRITICAL)
Every request from the frontend that identifies a user MUST verify Telegram's
`initData` signature. A user's identity is only trusted after this check passes.

Verification algorithm (implement in `backend/tg.py`):
```python
import hashlib, hmac
from urllib.parse import parse_qsl, unquote

def verify_init_data(init_data: str, bot_token: str) -> dict:
    """
    Returns parsed user data if valid, raises ValueError if not.
    See: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("Missing hash")

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected   = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        raise ValueError("Invalid hash — reject request")

    import json
    user_data = json.loads(unquote(parsed.get("user", "{}")))
    return user_data
```

Write a unit test for this before building anything that depends on it.

### CORS
Backend must only accept requests from the frontend origin and Telegram's CDN.
Set `allow_origins` in FastAPI's `CORSMiddleware` to `[GAME_URL]` — not `"*"`.

---

## Git rules

1. **Commit after every logical unit of work.** Don't batch unrelated changes.
2. **Commit messages**: imperative, specific. `Add initData verification` not `updates`
3. **Branch strategy**: `main` is always deployable. Work in feature branches.
   ```
   git checkout -b feature/backend-api
   git checkout -b feature/frontend-game-ui
   git checkout -b feature/bot-slim
   ```
4. **Before any commit**: run `git status` and visually confirm `.env` is not staged.
5. **Never force-push to main.**
6. **Tag releases**: when something is deployed, `git tag v0.1.0`

Suggested first commits in order:
```
feat: initial project structure and .gitignore
feat: backend game.py word engine
feat: backend FastAPI skeleton with /puzzle and /health
feat: backend initData verification and /score endpoint
feat: frontend index.html game UI
feat: frontend game.js state machine and API integration
feat: bot sendGame and /scores leaderboard
docs: README deployment guide
```

---

## API contract (backend/main.py)

```
GET  /health              → {status: "ok", day: int}
GET  /puzzle              → {start, end, optimal_steps, day}
POST /score               → records score, returns leaderboard position
     body: {init_data: str, path: [str], gave_up: bool}
GET  /leaderboard         → today's scores [{name, moves, optimal, gave_up}]
```

`/score` and `/leaderboard` require valid `init_data` in the request body or
Authorization header. Reject anything that doesn't verify.

---

## Frontend rules

- **Mobile-first.** Telegram users are overwhelmingly on mobile.
- **Use Telegram theme variables** for colours so the game matches the user's
  Telegram theme (light/dark/custom):
  ```css
  background-color: var(--tg-theme-bg-color);
  color: var(--tg-theme-text-color);
  ```
- **No external fonts or icon libraries.** Keep it fast and offline-resilient.
- **One HTML file.** Use CSS classes to show/hide game states (loading,
  playing, finished) rather than multiple pages.
- **The MainButton** (Telegram's bottom action button) should be used for the
  primary CTA (e.g., Share Score when done).
- Initialise the Telegram WebApp SDK immediately:
  ```js
  const tg = window.Telegram.WebApp;
  tg.ready();
  tg.expand();
  const initData = tg.initData;  // send this with every API call
  ```

---

## What NOT to do

- Do not read `.env` file contents directly (use `os.environ` / `load_dotenv`)
- Do not use `SELECT *` in SQL queries
- Do not store plaintext tokens or secrets anywhere in code
- Do not skip initData verification for "testing convenience"
- Do not use `allow_origins=["*"]` in production CORS config
- Do not rewrite `game.py`
- Do not introduce a build step for the frontend without flagging it
- Do not commit `*.db` files
- Do not `print()` sensitive data (use `logging`)
- Do not leave TODO comments in committed code — either implement or file an issue

---

## Running locally

```bash
# Backend
cd backend && uvicorn main:app --reload --port 8000

# Bot (separate terminal)
cd bot && python3 bot.py

# Frontend
# Serve frontend/ with any static server, e.g.:
python3 -m http.server 3000 --directory frontend/
# Then use ngrok or cloudflared for HTTPS (required by Telegram)
```

---

## Definition of done (per feature)

- [ ] Code written and manually tested
- [ ] Edge cases handled (invalid input, network errors, missing env vars)
- [ ] No secrets in code
- [ ] Committed to feature branch with descriptive message
- [ ] PR merged to main (or pushed directly if solo)
