# Word Ladder

Daily word-ladder puzzle as a Telegram Mini App.
Same puzzle for everyone. Private gameplay. Group leaderboard.

## Before you start coding

Three manual steps in Telegram that can't be scripted:

1. **Create a bot** — message `@BotFather` → `/newbot` → copy token into `.env`
2. **Register the game** — `@BotFather` → `/newgame` → short name: `wordladder`
3. **Set the game URL** — `@BotFather` → `/setgameurl` → `https://yourdomain.com`

## Local setup

```bash
git clone https://github.com/YOURNAME/word-ladder
cd word-ladder

cp .env.example .env
# edit .env with your values

# Install the pre-commit hook
cp deploy/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit

# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Bot (separate terminal)
cd bot
pip install -r requirements.txt
python3 bot.py

# Frontend — needs HTTPS for Telegram, use cloudflared or ngrok locally
cloudflared tunnel --url http://localhost:3000
python3 -m http.server 3000 --directory ../frontend/
```

## Deployment

See `SPEC.md` → Deployment checklist.
Short version: Ubuntu VPS + Nginx + certbot + systemd.

## Architecture

```
Telegram → Bot → sendGame → [Play button]
                               ↓
                         Mini App webview
                               ↓ fetch
                         FastAPI backend
                               ↓
                           SQLite DB
```

See `CLAUDE.md` for full architecture and coding rules.
See `SPEC.md` for product spec and API contract.
