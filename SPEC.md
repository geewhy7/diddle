# Diddle — Product & Technical Spec

## The product in one paragraph
A daily word-ladder puzzle delivered as a Telegram Mini App. Every day the bot
posts the same two puzzles (4-letter and 5-letter) — a start word and a target
word for each. Players tap Play, solve privately in a webview, and the group
leaderboard message updates live as friends play. The social hook: same puzzle
for everyone, compare moves-over-par with friends.

---

## Infrastructure — already running, do not touch

- ✅ Bot: `@ClownCasinoBot` — token in `.env`
- ✅ Mini App: `https://diddle.retard.zone` via Cloudflare tunnel → `localhost:7113`
- ✅ Mini App registered with BotFather under short name `diddle`
- ✅ FastAPI backend serves both the API and the frontend static files
- ✅ Backend runs as systemd service `diddle-backend`
- ✅ Bot runs in tmux session `clown` (not systemd — restart manually)
- ✅ SQLite DB at `backend/diddle.db`
- ✅ Private GitHub repo at `git@github.com:gwisawesome/diddle.git`
- ✅ EPOCH = 2026-06-07 (day 1)

---

## User journeys

### Journey 1 — Playing in a group
1. User (or anyone) sends `/play` in a group
2. Bot calls backend `POST /group_message/post`; backend deletes any existing
   board for today and sends a fresh one with the **[Play Diddle 🎮]** button
   (`t.me/<bot>/diddle?startapp=g<abs(chat_id)>`), then deletes the `/play` command message
3. User taps → Telegram opens `https://diddle.retard.zone` in a webview
4. Lobby shows two cards: **4L** and **5L** puzzles (par steps, start → target)
5. User taps a card → zooms into the game for that length
6. User types guesses; path builds up; changed letter is highlighted
7. On completion: score screen with path replay, then back to lobby
8. Share button on lobby → copies text summary to clipboard
9. Group board message updates in real time (⏱️ playing → 🎯/⭐/😂/🤡/💀)

### Journey 2 — Bot commands
- `/scores` → today's leaderboard (all players, both lengths)
- `/alltime` → all-time handicap standings (avg delta per player)
- `/help` → command list

### Journey 3 — Giving up
1. User taps **Give Up** in the webview
2. App shows the optimal path via BFS on the client-side graph
3. Score recorded as `gave_up: true`, appears with 💀 on the board

### Journey 4 — Already played (cross-device or revisit)
1. `GET /me` at startup syncs server scores into `localStorage`
2. Lobby shows completed cards with their result
3. Tapping a completed card shows replay (no re-play option)

---

## Day / puzzle seeding

```python
EPOCH = date(2026, 6, 7)  # day 1
game_day = (date.today() - EPOCH).days + 1
```

`pick_puzzle` in `game.py` uses `date.today()` as the random seed — deterministic
and identical for all players. **Do not modify this function.**

---

## Challenge mode (Wicked Wednesday)

Every Wednesday — or any day when `FORCE_CHALLENGE=true` is set in `.env` —
the backend serves harder puzzles:

- **Word set**: regular connected component ∩ wordfreq top-20k
  (~1,370 4L / ~1,200 5L words), built at startup alongside the regular sets
- **Difficulty**: `min_steps=9, max_steps=15` (vs 4–7 normal);
  falls back to 6–12 if no qualifying pair exists
- **Seed**: `date.toordinal() + 100_000` — independent of the regular seed
- **Validation**: still uses the full regular word set, so players can step
  through any valid word
- `GET /puzzle` includes `is_challenge: bool` for the frontend
- **Frontend treatment**: a red "Wicked Wednesday" rubber stamp slams onto the
  lobby (tilted, double-ring border, `--stamp` ink color per theme), puzzle
  cards get a red-tinted border + red par, and the header shows 😈 in-game

---

## Word lists

- **5L**: Wordle list ∩ wordfreq top-50k English words
- **4L**: ENABLE Scrabble dictionary ∩ wordfreq top-30k English words
  (ENABLE filters out rare/obscure words better than raw wordfreq alone)

The word graph is built at backend startup and reused all day.

---

## Emoji mapping (everywhere — share text AND group board)

| Delta       | Emoji |
|-------------|-------|
| 0 (par)     | 🎯    |
| +1 or +2    | ⭐    |
| +3 or +4    | 😂    |
| +5 or more  | 🤡    |
| Gave up     | 💀    |
| In progress | ⏱️   |

---

## Database schema

```sql
CREATE TABLE scores (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    username          TEXT,
    display_name      TEXT NOT NULL,
    play_date         TEXT NOT NULL,
    word_length       INTEGER NOT NULL DEFAULT 5,
    moves             INTEGER NOT NULL,
    optimal           INTEGER NOT NULL,
    gave_up           INTEGER NOT NULL DEFAULT 0,
    path              TEXT NOT NULL,          -- JSON array of words
    invalid_attempts  INTEGER NOT NULL DEFAULT 0,
    chat_id           INTEGER,                -- group chat if known
    submitted_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, play_date, word_length)
);

CREATE TABLE group_messages (
    chat_id     INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    play_date   TEXT NOT NULL,
    play_url    TEXT,                -- t.me link incl. ?startapp= chat encoding
    PRIMARY KEY (chat_id, play_date)
);

CREATE TABLE progress (
    user_id      INTEGER NOT NULL,
    play_date    TEXT NOT NULL,
    word_length  INTEGER NOT NULL,
    path         TEXT NOT NULL DEFAULT '[]',  -- JSON array of words so far
    chat_id      INTEGER,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, play_date, word_length)
);

CREATE TABLE group_activity (
    chat_id      INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    play_date    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'playing',  -- 'playing'|'done'|'gaveup'
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (chat_id, user_id, play_date)
);
```

Status progression rules in `upsert_group_activity`:
- `'playing'`: insert or update display_name (never downgrade existing status)
- `'done'`: always wins, overwrites any status
- `'gaveup'`: only applied if current status is `'playing'`

---

## API spec (complete, current)

### GET /health
```json
{ "status": "ok", "day": 2 }
```

### GET /puzzle?length=4|5
No auth. Same response for everyone on a given day.
```json
{ "start": "bale", "end": "core", "optimal_steps": 4, "day": 2,
  "word_length": 4, "is_challenge": false }
```

### GET /words?length=4|5
No auth. Plain text, one valid word per line. Used for client-side validation.

### POST /playing
Auth: initData in body. Fire-and-forget from frontend.
```json
// Request
{ "init_data": "...", "chat_id": -1001234567890 }
// Response
{ "ok": true }
```
Upserts `group_activity` as 'playing', then calls `edit_group_message`.
chat_id is optional; if null, does nothing for group tracking.

### POST /progress
Auth: initData in body. Fired after every valid (non-winning) move.
```json
// Request
{ "init_data": "...", "path": ["bale", "bare"], "word_length": 4,
  "chat_id": -1001234567890 }
// Response
{ "ok": true }
```
Upserts the `progress` row; if chat_id is set, refreshes the group board so
playing users show live `moves/optimal`. The row is deleted on score submit.

### POST /score
Auth: initData in body. Backend re-validates the entire path.
Duplicate submissions return the existing row silently.
```json
// Request
{
  "init_data": "...",
  "path": ["bale", "bare", "care", "core"],
  "gave_up": false,
  "word_length": 4,
  "invalid_attempts": 1,
  "chat_id": -1001234567890
}
// Response
{
  "moves": 3, "optimal": 3, "delta": 0,
  "gave_up": false,
  "path": ["bale", "bare", "care", "core"],
  "rank": 1,
  "ordinal_position": 1,
  "message": "Perfect!"
}
```
After saving: upserts `group_activity` ('done' or 'gaveup'), calls `edit_group_message`.

### GET /me
Auth: `Authorization: tma <init_data>`
Returns today's scores + in-progress paths for the authenticated user
(startup sync; progress lets a reload resume mid-puzzle).
```json
{
  "scores": [
    { "word_length": 4, "moves": 3, "optimal": 3, "gave_up": false, "delta": 0,
      "path": ["bale","bare","care","core"] }
  ],
  "progress": [
    { "word_length": 5, "path": ["bakes","bikes"], "chat_id": null }
  ]
}
```

### GET /leaderboard?length=4|5
Auth: `Authorization: tma <init_data>` OR `bot <token>`
```json
[
  { "name": "Alice", "moves": 3, "optimal": 3, "gave_up": false, "word_length": 4, "avg": 0.5 },
  { "name": "Bob",   "moves": 5, "optimal": 3, "gave_up": false, "word_length": 4, "avg": null }
]
```
`avg` = all-time avg delta for completions; `null` if fewer than 3 days played.

### GET /stats?length=4|5
Auth: `Authorization: tma <init_data>`
```json
{
  "current_streak": 2, "longest_streak": 5,
  "total_played": 10, "total_won": 9, "total_extra": 12,
  "distribution": { "0": 3, "1": 4, "2": 2 }
}
```

### GET /stats/alltime?length=4|5
Auth: `Authorization: tma <init_data>` OR `bot <token>`
```json
{
  "puzzle_difficulty": 1.8,
  "players": [
    { "name": "Alice", "handicap": 0.5, "days_played": 8 }
  ]
}
```

### POST /group_message/post
Auth: `Authorization: bot <token>` only — called by the bot on group `/play`.
```json
// Request
{ "chat_id": -1001234567890, "play_url": "https://t.me/ClownCasinoBot/diddle?startapp=g1001234567890" }
// Response
{ "message_id": 123 }
```
Deletes today's existing board message (if any), sends a fresh one with the
Play button, and records `(chat_id, message_id, play_date, play_url)`.

---

## Frontend architecture

Single `index.html` loading scripts in dependency order. No build step.
Babel compiles JSX at runtime in the browser.

```
index.html
  └─ engine.js         (plain JS — no Babel)
  └─ components.jsx    (Tiles, ChainRow, Replay, Mark, Wordmark)
  └─ screens.jsx       (LobbyScreen, PlayingScreen, FinishedScreen,
                         GaveUpScreen, ResultScreen, LeaderboardScreen, etc.)
  └─ app.jsx           (App root, all state, API calls)
```

### App states (screen machine)
```
loading  → spinner while fetching puzzle + /me sync
lobby    → two puzzle cards (4L + 5L), share btn, leaderboard btn
zooming  → CSS zoom animation from card to fullscreen (340 ms)
playing  → word input, path display, give-up button
finished → score summary, back to lobby
gaveup   → gave-up screen with optimal path
result   → replay of already-completed puzzle (read-only)
error    → network/auth failure
```

### Played state persistence
`localStorage` key `diddle.played.v1` — object keyed by `"${day}-${length}"`:
```js
{ delta, moves, gaveUp, path: [UPPERCASE words], rank }
```
Server is authoritative: `GET /me` at startup overwrites `localStorage` with
DB truth. This handles cross-device, corrupted state, and duplicate submissions.

### CHAT_ID sourcing
Telegram does not populate `initDataUnsafe.chat` for t.me-link opens, so the
group's chat_id is smuggled through the `startapp` parameter instead. The bot
builds the Play button URL as `t.me/<bot>/diddle?startapp=g{abs(chat_id)}`
(groups/supergroups always have negative ids, so the sign is restored on
decode). Resolution order in `app.jsx`:
```js
const _urlChatId   = new URLSearchParams(window.location.search).get('chat_id');
const _startParam  = tg?.initDataUnsafe?.start_param ?? '';
const _startChatId = _startParam.startsWith('g') ? -(parseInt(_startParam.slice(1), 10)) : null;
const CHAT_ID      = _urlChatId   ? parseInt(_urlChatId, 10)
                   : _startChatId ? _startChatId
                   : (tg?.initDataUnsafe?.chat?.id ?? null);
```

---

## Live group board — message format

```
Diddle — Day 2 🔤

🎯 Karl — 4L perfect · 5L +2
⭐ Dan — 4L +1
⏱️ Greg — playing...
💀 Thomas — gave up
```

Order: done (sorted by total moves-over-par ascending), then playing, then gaveup.
`build_group_message(chat_id, play_date)` in `main.py` assembles this.
`edit_group_message(chat_id, play_date)` calls Telegram's API via httpx.
Edits re-send the stored `play_url` button as `reply_markup` (a url button —
never `web_app`, which is invalid in groups). Playing users with live progress
show as `⏱️ Name — 3/7` (moves so far / optimal).

---

## Bot commands

| Command    | Behaviour |
|------------|-----------|
| `/play`    | Group: re-post today's board via backend (deletes the old board + the `/play` message). DM: send Mini App link. |
| `/scores`  | Today's leaderboard across all players, both puzzle lengths |
| `/alltime` | All-time handicap standings (avg delta, completions only) |
| `/help`    | Command list |

Bot uses `python-telegram-bot v20` (async). Bot token verified via HMAC on every
initData request. Bot calls backend API for `/leaderboard` and `/stats/alltime`;
reads `group_messages` and `group_activity` tables directly via `db.py` import.

---

## Changed-letter highlight

When a word is committed to the path, the tile whose letter changed from the
previous word gets a slightly thicker coloured border. Computed by
`Diddle.changedIndex(prev, word)` in `engine.js`, used in both `ChainRow`
(while playing) and `Replay` (result screen). CSS: `.tile.changed` with
`border-width: 2px; border-color: var(--tg-theme-hint-color)`.
`.tile.changed.hit` uses `--acc-green` instead.

---

## Open problems / next steps

1. **Bot runs in tmux** — convert to systemd service for reliability. The service
   file `deploy/diddle-bot.service` exists but the bot currently runs in tmux.
   Needs a venv or system-python dependencies resolved first (aiosqlite, httpx).

2. **Stats distribution buckets cap at 6+ extra moves** — challenge-day
   deltas can blow well past that, so they all pile into the 6+ bucket.
