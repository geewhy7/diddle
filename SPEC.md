# Diddle — Product & Technical Spec

## The product in one paragraph
A daily word-ladder puzzle delivered as a Telegram Mini App. Every day the bot
posts the same puzzle — a start word and a target word. Players tap Play,
solve privately in a webview, and their score appears on the leaderboard.
The social hook: same puzzle for everyone, compare moves-over-par with friends.

---

## Pre-coding setup — already done

- ✅ Bot created via @BotFather → token in `.env`
- ✅ Game short name `diddle` registered (not used for Mini Apps — ignore)
- ✅ Domain: `diddle.retard.zone` via Cloudflare tunnel → `localhost:7113`
- ✅ Private GitHub repo created
- ✅ Cloudflare tunnel running on Pi

No further BotFather configuration needed. Mini Apps don't require
`/setgameurl` or `sendGame()`.

---

## User journeys

### Journey 1 — Playing in a group
1. User sends `/play` in a group where the bot lives
2. Bot replies with a message + **[Play Diddle 🎮]** inline button (Mini App)
3. User taps → Telegram opens `https://diddle.retard.zone` in a webview
4. User sees the puzzle: start word, target word, par steps
5. User types guesses one at a time, path builds up on screen
6. On completion: score screen — their path, moves vs par, share button
7. User taps **Share** → Telegram posts summary to the group chat
8. Other players can tap to open their own game or view leaderboard

### Journey 2 — Leaderboard
1. User sends `/scores` in group or DM
2. Bot fetches `/leaderboard` from backend
3. Bot replies with ranked list: moves, par delta, gave_up at bottom

### Journey 3 — Giving up
1. User taps **Give Up** in the webview
2. App shows the optimal path
3. Score recorded as `gave_up: true`, appears at bottom of leaderboard

---

## Telegram Mini App integration

### SDK init (game.js)
```js
const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();
tg.disableVerticalSwipes();

const initData = tg.initData;           // sent with every backend request
const user     = tg.initDataUnsafe.user; // display only, not trusted
```

### MainButton
```js
// When puzzle is solved:
tg.MainButton.setText("Share Score 🔤");
tg.MainButton.show();
tg.MainButton.onClick(() => {
    tg.close(); // or implement share via tg.showPopup
});
```

### Bot sends Mini App button (NOT sendGame)
```python
InlineKeyboardButton("Play Diddle 🎮", web_app=WebAppInfo(url=GAME_URL))
```

---

## API spec

### GET /health
```json
{ "status": "ok", "day": 9774 }
```

### GET /puzzle
No auth. Same response for everyone.
```json
{
  "start":         "bakes",
  "end":           "jokes",
  "optimal_steps": 4,
  "day":           9774,
  "word_length":   5
}
```

### GET /words
No auth. Returns plain text, one valid word per line.
Used by the frontend for client-side validation (no round-trip per guess).

### POST /score
```json
// Request body
{
  "init_data": "...",
  "path":      ["bakes", "bikes", "pikes", "pokes", "jokes"],
  "gave_up":   false
}

// Response
{
  "moves":   4,
  "optimal": 4,
  "delta":   0,
  "rank":    1,
  "message": "Perfect!"
}
```
Backend re-validates the full path before recording. Client is not trusted.

### GET /leaderboard
Auth: `Authorization: tma <init_data>` header.
```json
[
  { "name": "Alice", "moves": 4, "optimal": 4, "gave_up": false },
  { "name": "Bob",   "moves": 6, "optimal": 4, "gave_up": false },
  { "name": "Dave",  "moves": 2, "optimal": 4, "gave_up": true  }
]
```

---

## Database schema

```sql
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
```

---

## Frontend states

Single `index.html`, CSS classes toggle between:

```
loading   → spinner while fetching puzzle
playing   → word input, path display, target, move counter
finished  → score summary, path, share button
gave-up   → gave up screen, shows optimal path
error     → network/auth failure message
```

### Playing state (mobile layout)
```
┌─────────────────────────┐
│  🔤 Diddle  ·  Day 157  │
│  Par: 4 steps           │
├─────────────────────────┤
│  BAKES                  │  ← scrollable path
│    ↓ B[I]KES            │
│    ↓ [P]IKES            │
├─────────────────────────┤
│  Target: JOKES          │  ← sticky
│  Moves: 2  |  Best: 2   │
├─────────────────────────┤
│  [___________]  [→]     │  ← input + submit
└─────────────────────────┘
```

---

## Word validation

- **Client-side first**: check length, alpha-only, one letter diff, word in
  locally-fetched `/words` list. Fast, no round-trip per guess.
- **Server-side on submit**: backend re-validates entire path before saving.
  Never trust the client's claimed path.

---

## Deployment (Pi + Cloudflare)

- [ ] `.env` created on Pi with all values filled in
- [ ] `pip install -r backend/requirements.txt` and `bot/requirements.txt`
- [ ] systemd units installed: `deploy/diddle-backend.service` and `deploy/diddle-bot.service`
- [ ] Both services enabled and started
- [ ] `curl http://localhost:7113/health` returns `{"status":"ok",...}`
- [ ] Open `https://diddle.retard.zone` in browser — game loads
- [ ] Send `/play` in Telegram — Mini App button appears
- [ ] Tap button — webview opens correctly
- [ ] Complete a puzzle — score appears in `/scores`

---

## Out of scope for now

- Multiple word lengths in-app (hardcode 5)
- User history across days
- Push notifications
- Custom/practice puzzles
