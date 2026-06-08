# Diddle

Daily word ladder puzzle delivered as a Telegram Mini App. Everyone gets the same start word and target word each day. Solve it in as few steps as possible, compare scores with friends.

## How it works

1. Someone sends `/play` in a group chat
2. Bot replies with a **Play Diddle 🎮** button
3. Tapping opens the puzzle in a Telegram webview (private — no chat spam)
4. On completion, score is recorded server-side
5. `/scores` shows today's ranked leaderboard at any time

## Stack

| Layer    | Tech                              |
|----------|-----------------------------------|
| Backend  | FastAPI + uvicorn (port 7113)     |
| Frontend | React — static files via FastAPI  |
| Database | SQLite + aiosqlite                |
| Bot      | python-telegram-bot v20           |
| Tunnel   | Cloudflare → `diddle.retard.zone` |

## Setup

### 1. Environment

```bash
cp .env.example .env
# Fill in TELEGRAM_TOKEN (from @BotFather → /newbot)
```

### 2. Dependencies

```bash
pip install -r backend/requirements.txt
pip install -r bot/requirements.txt
```

### 3. Register the Mini App with BotFather

This step is required for the `/play` button to open the app with Telegram initData.

Open @BotFather in Telegram:
```
/newapp
→ select your bot
→ Title:      Diddle
→ Short name: diddle
→ URL:        https://diddle.retard.zone
```

The bot's `/play` button sends `https://t.me/<botusername>/diddle` — this only works once BotFather has registered the app.

### 4. Run

**Backend** — systemd service:
```bash
sudo cp deploy/diddle-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now diddle-backend
```

**Bot** — tmux (keeps logs visible):
```bash
tmux new -s bot
cd bot && python3 bot.py
# Ctrl-B D to detach; tmux attach -t bot to reattach
```

### 5. Verify

```bash
curl http://localhost:7113/health
# {"status":"ok","day":1}
```

Then open `https://diddle.retard.zone` in a browser and send `/play` in Telegram.

## Development

Set `DEV_SKIP_AUTH=true` in `.env` to bypass Telegram initData verification — empty `init_data` is accepted as a test user. **Must be `false` in production.**

```bash
cd backend
uvicorn main:app --reload --port 7113
```

The Cloudflare tunnel handles HTTPS externally. FastAPI serves both the API and the static frontend on port 7113 — no nginx, no separate static server.

## Bot commands

| Command   | Description                    |
|-----------|--------------------------------|
| `/play`   | Open today's puzzle (Mini App) |
| `/scores` | Show today's leaderboard       |
| `/help`   | Game rules                     |

## Project layout

```
backend/
  main.py     FastAPI app, API routes, static file serving
  game.py     Word ladder engine (do not modify)
  db.py       Database operations
  tg.py       Telegram initData HMAC verification

frontend/
  index.html  Entry point
  (React component and style files)

bot/
  bot.py      /play /scores /help

deploy/
  diddle-backend.service  systemd unit for uvicorn
  diddle-bot.service      systemd unit (reference only — bot runs in tmux)
```
