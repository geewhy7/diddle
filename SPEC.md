# Word Ladder — Product & Technical Spec

## The product in one paragraph
A daily word-ladder puzzle delivered as a Telegram Mini App. Every day the bot
posts (or users request) the same puzzle — a start word and a target word.
Players tap Play, solve the puzzle privately in a webview, and their score is
posted to the leaderboard. The social hook is the group leaderboard: same
puzzle, everyone's score, compare how many moves over par you were.

---

## Manual setup required BEFORE writing code
Claude Code cannot do these steps. Do them first and put the values in `.env`.

### 1. Register the Telegram bot (if not already done)
- Message `@BotFather` → `/newbot`
- Name: whatever you want (e.g. "Word Ladder")
- Username: must end in `bot` (e.g. `wordladderbot`)
- Copy the token → `TELEGRAM_TOKEN`

### 2. Register the game
- Message `@BotFather` → `/newgame`
- Choose your bot
- Game short name: `wordladder` (no spaces, lowercase) → `GAME_SHORT_NAME`
- Title: Word Ladder
- Description: Daily word puzzle. Turn one word into another, one letter at a time.
- Photo: upload something (can be a placeholder for now)
- **No GIF needed**

### 3. Decide your domain and hosting
- Domain: whatever cheap domain you buy → `diddle.retard.zone` (e.g. `wordladder.xyz`)
- The game URL will be: `https://diddle.retard.zone` → `GAME_URL`

---

## User journeys

### Journey 1 — Playing in a group
1. User sends `/play` in a group where the bot lives
2. Bot replies with a Game message: puzzle preview + **[Play Word Ladder]** button
3. User taps the button → Telegram opens `GAME_URL` in a webview
4. User sees the puzzle: start word, target word, par
5. User types guesses one at a time
6. Each guess: validated, added to the path, remaining optimal steps shown
7. On completion: score screen with their path, moves vs par, share button
8. User taps **Share** → Telegram posts score summary to the group
9. Anyone in the group can tap the score post to view the leaderboard in context

### Journey 2 — Checking the leaderboard
1. User sends `/scores` in the group (or DM)
2. Bot queries `/leaderboard` from backend
3. Bot replies with a formatted leaderboard: ranked by moves, gave_up at bottom

### Journey 3 — Giving up
1. User taps **Give Up** in the webview
2. App shows the optimal path
3. Score recorded as `gave_up: true`
4. User appears on leaderboard below all finishers

---

## Telegram Mini App integration

### Frontend initialisation
```js
const tg = window.Telegram.WebApp;
tg.ready();           // tell Telegram the app is ready
tg.expand();          // use full screen height
tg.disableVerticalSwipes();  // prevent accidental swipe-to-close while typing

const initData = tg.initData;      // validated on every backend request
const user     = tg.initDataUnsafe.user;  // for display only — not trusted
```

### MainButton (Telegram's native bottom button)
```js
// When puzzle is solved:
tg.MainButton.setText("Share Score");
tg.MainButton.show();
tg.MainButton.onClick(() => {
    // tg.shareScore() for Game API
    // or tg.showPopup() + manual share for Mini App API
});
```

### Score submission (Game API)
After completion, call backend `/score` which then calls Telegram's
`setGameScore` API to register the score in the Telegram leaderboard system.
This is what powers the native in-chat leaderboard when a user taps a score.

---

## API spec

### `GET /health`
```json
{ "status": "ok", "day": 9774 }
```

### `GET /puzzle`
No auth required (puzzle is public, same for everyone).
```json
{
  "start":         "bakes",
  "end":           "jokes",
  "optimal_steps": 4,
  "day":           9774,
  "word_length":   5
}
```

### `POST /score`
Auth: `init_data` in request body (verified server-side).
```json
// Request
{
  "init_data": "...",
  "path":      ["bakes", "bikes", "pikes", "pokes", "jokes"],
  "gave_up":   false,
  "inline_message_id": "optional — from tg.initDataUnsafe"
}

// Response
{
  "moves":     4,
  "optimal":   4,
  "delta":     0,
  "rank":      1,
  "message":   "Perfect!"
}
```

### `GET /leaderboard`
Auth: `init_data` in `Authorization: tma <init_data>` header.
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
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    username        TEXT,
    display_name    TEXT NOT NULL,
    play_date       TEXT NOT NULL,          -- ISO date, e.g. "2026-06-07"
    word_length     INTEGER NOT NULL DEFAULT 5,
    moves           INTEGER NOT NULL,
    optimal         INTEGER NOT NULL,
    gave_up         INTEGER NOT NULL DEFAULT 0,  -- boolean 0/1
    path            TEXT NOT NULL,              -- JSON array
    submitted_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, play_date, word_length)
);

CREATE INDEX IF NOT EXISTS idx_scores_date ON scores(play_date);
```

---

## Bot commands (slim — bot does very little)

| Command       | Response                                              |
|---------------|-------------------------------------------------------|
| `/play`       | Sends Game message with [Play] button                 |
| `/scores`     | Fetches leaderboard from backend, formats, replies    |
| `/help`       | Brief explanation + /play and /scores                 |

The bot does **not** handle gameplay. All gameplay is in the Mini App.

---

## Frontend states

The single `index.html` has these CSS-class-toggled states:

```
[loading]   → spinner, fetching puzzle from backend
[playing]   → word input, current path, target, par, moves counter
[finished]  → score summary, path replay, share button
[gave-up]   → gave up screen, shows optimal path
[error]     → something went wrong (network, invalid initData, etc.)
```

### Playing state layout (mobile)
```
┌─────────────────────────┐
│  🔤 Word Ladder  Day 157│  ← header
│  Par: 4 steps           │
├─────────────────────────┤
│  BAKES                  │  ← path so far (scrollable)
│    ↓ B[I]KES            │
│    ↓ [P]IKES            │
├─────────────────────────┤
│  Target: JOKES          │  ← sticky target reminder
│  Moves: 2  Best: 2      │
├─────────────────────────┤
│  [____________] [→]     │  ← input + submit
└─────────────────────────┘
```

---

## Word validation strategy

Validate **client-side first** (fast, no round-trip) then **trust the server**:
- Client: check length, alpha, one letter diff, word in a locally-fetched word list
- Server `/score`: re-validates the entire submitted path before recording it

Don't trust the client's path — always revalidate on the backend before saving.

---

## Deployment checklist

- [ ] VPS provisioned (Hetzner CAX11 or similar, Ubuntu 24.04)
- [ ] Domain pointing at VPS IP
- [ ] `.env` created on server (never synced via git)
- [ ] `systemd` units installed and enabled
- [ ] Backend running: `curl https://diddle.retard.zone/health`
- [ ] Frontend loading: open `https://diddle.retard.zone` in browser
- [ ] Bot responding: send `/play` in Telegram
- [ ] Game opening: tap [Play] — webview loads
- [ ] Score submitting: complete a puzzle, check `/leaderboard`
- [ ] BotFather `/setgameurl` set to `https://diddle.retard.zone`

---

## What's out of scope (for now)

- Multiple word lengths selectable in-app (hardcode 5 for launch)
- User accounts / history across days
- Custom puzzles
- Push notifications
- Anything requiring a paid Telegram tier
