/* Diddle — production app shell.
   Wired to the real API and Telegram Mini App SDK.
   No design-tool wrapper (IOSDevice / TweaksPanel). */

// ---- Telegram SDK init (runs before React, at script parse time) ------------
const tg          = window.Telegram?.WebApp ?? null;
const INIT_DATA   = tg?.initData   ?? '';
const COLOR_SCHEME = tg?.colorScheme ?? 'light';
// Resolve chat_id: ?chat_id= param → startapp=gXXX → initDataUnsafe.chat.id
const _urlChatId   = new URLSearchParams(window.location.search).get('chat_id');
const _startParam  = tg?.initDataUnsafe?.start_param ?? '';
const _startChatId = _startParam.startsWith('g') ? -(parseInt(_startParam.slice(1), 10)) : null;
const CHAT_ID      = _urlChatId   ? parseInt(_urlChatId, 10)
                   : _startChatId ? _startChatId
                   : (tg?.initDataUnsafe?.chat?.id ?? null);

if (tg) {
  tg.ready();
  tg.expand();
  tg.disableVerticalSwipes();
}

// ---- Globals set by components.jsx, screens.jsx ----------------------------
const {
  LoadingScreen, ErrorScreen, PlayingScreen, FinishedScreen, GaveUpScreen,
  LobbyScreen, LeaderboardScreen, ResultScreen,
  Wordmark, Mark, Confetti, DiddleSound,
} = window;

// ---- Per-puzzle state keys (mode-aware) ------------------------------------
// A player can play all four puzzles a day — normal/hard × 4L/5L — so every
// localStorage key (played, start, deadline, ta-mode) is keyed by day-length-
// mode. modeChar: 'h' for the hard variant, 'n' for normal. Function (not
// const) declarations so screens.jsx can call them at render time.
function modeChar(hard) { return hard ? 'h' : 'n'; }
function pkey(num, len, hard) { return `${num}-${len}-${modeChar(hard)}`; }
function puzKey(p) { return pkey(p.num, p.length, p.hardMode); }

// ---- Win stats (localStorage) — per-puzzle-length counters -----------------
// Version-suffixed so a bump abandons stale local state (the /me sync only
// OVERWRITES entries the server still has scores for; it never deletes, so a
// cleared puzzle otherwise keeps showing "already played" — hence the bump).
// History: .v2 = 2026-06-12 reset; .v3 = 2026-06-17 era reset (seed 1776→1984).
// The per-puzzle keys are at .v4 (2026-06-21: 5L pool fix re-rolled that day's
// 5-letter puzzle, so its stale played/timer state had to be dropped). STATS is
// deliberately left at .v3 — that was a single-puzzle re-roll, not an era reset,
// and no 5L was finished that day, so win streaks are preserved.
const STATS_KEY  = 'diddle.stats.v3';
const ZERO_STATS = { wins: 0, totalExtra: 0, currentStreak: 0, bestStreak: 0,
                     dist: [0,0,0,0,0,0,0], counted: {} };
function loadStats() {
  try {
    const s = JSON.parse(localStorage.getItem(STATS_KEY));
    if (s && Array.isArray(s.dist)) return s;
  } catch (_) {}
  return JSON.parse(JSON.stringify(ZERO_STATS));
}
function recordWin(stats, num, length, extra, hard = false) {
  const key = pkey(num, length, hard);
  if (stats.counted?.[key]) return stats;
  const s         = JSON.parse(JSON.stringify(stats));
  s.counted       = s.counted || {};
  s.counted[key]  = true;
  s.wins         += 1;
  s.totalExtra   += Math.max(0, extra);
  s.dist[Math.min(Math.max(0, extra), 6)] += 1;
  s.currentStreak += 1;
  s.bestStreak    = Math.max(s.bestStreak, s.currentStreak);
  try { localStorage.setItem(STATS_KEY, JSON.stringify(s)); } catch (_) {}
  return s;
}

// ---- Played results (localStorage) — lobby card state ----------------------
const PLAYED_KEY = 'diddle.played.v4';
function loadPlayed() {
  try {
    const s = JSON.parse(localStorage.getItem(PLAYED_KEY));
    if (s && typeof s === 'object') return s;
  } catch (_) {}
  return {};
}

// ---- Solve-timer starts (localStorage) — epoch ms keyed "${day}-${length}" --
// Display only; the recorded time is computed server-side from progress.started_at.
const START_KEY = 'diddle.start.v4';
function loadStarts() {
  try {
    const s = JSON.parse(localStorage.getItem(START_KEY));
    if (s && typeof s === 'object') return s;
  } catch (_) {}
  return {};
}

// ---- Shot-clock deadlines (localStorage) — epoch ms keyed "${day}-${length}" -
// Strict wall clock: persisted so closing and reopening the app can't dodge a
// running guess timer. A stored deadline in the past = the game was lost away.
const DEADLINE_KEY = 'diddle.deadline.v4';
function loadDeadlines() {
  try {
    const s = JSON.parse(localStorage.getItem(DEADLINE_KEY));
    if (s && typeof s === 'object') return s;
  } catch (_) {}
  return {};
}

// ---- Time-attack "fail" flags (localStorage) -------------------------------
// Time Attack is now ALWAYS ON (no toggle). The per-guess clock runs from the
// first reveal; the FIRST time a guess window lapses the clock is dropped and
// play continues untimed — not a loss. TA_FAIL records, per "${day}-${length}-
// ${mode}", that the clock lapsed, so reopening doesn't re-arm it and the solve
// is flagged "not clean" (no ⚡). A solve with no fail flag = clean ⚡ TA win.
const TA_FAIL_KEY = 'diddle.tafail.v4';
function loadTaFail() {
  try {
    const s = JSON.parse(localStorage.getItem(TA_FAIL_KEY));
    if (s && typeof s === 'object') return s;
  } catch (_) {}
  return {};
}

// ---- Hard Mode preference (localStorage) -----------------------------------
// Day-level toggle that swaps the lobby between the normal and hard puzzles.
// Unlike Time Attack it does NOT lock — you can flip back and forth to play
// all four. Just the remembered switch position.
const HARD_PREF_KEY = 'diddle.hardpref.v1';
function loadHardPref() {
  try { return localStorage.getItem(HARD_PREF_KEY) === '1'; } catch (_) { return false; }
}

// ---- Share text ------------------------------------------------------------
function deltaEmoji(delta) {
  if (delta < 0)  return '🦅';   // under par — golf!
  if (delta === 0) return '🎯';
  if (delta <= 2) return '⭐';
  if (delta <= 4) return '😂';
  return '🤡';
}

function buildShareText(played, dayNum, hard = false) {
  const completions = [4, 5]
    .map(len => ({ len, entry: played[pkey(dayNum, len, hard)] }))
    .filter(x => x.entry && !x.entry.gaveUp && !x.entry.timedOut);

  if (completions.length === 0) return '';

  const tag = hard ? ' 😈' : '';
  const scoreOf = (e) => e.score ?? holeScore(e.solveSeconds, e.moves);
  const bolt = (e) => (e.timeAttack ? ' ⚡' : '');

  if (completions.length === 1) {
    const { len, entry } = completions[0];
    return [
      `Diddle #${dayNum} (${len}L)${tag} ${deltaEmoji(entry.delta)}`,
      `Score ${scoreOf(entry) ?? '—'} · ${deltaTag(entry.delta)}${bolt(entry)}`,
    ].join('\n');
  }

  // Both completed
  const lines = [`Diddle #${dayNum}${tag}`];
  let total = 0, haveTotal = true;
  for (const { len, entry } of completions) {
    const sc = scoreOf(entry);
    if (sc == null) haveTotal = false; else total += sc;
    lines.push(`${len}L · ${sc ?? '—'} ${deltaTag(entry.delta)} ${deltaEmoji(entry.delta)}${bolt(entry)}`);
  }
  if (haveTotal) lines.push(`Total ${total}`);
  return lines.join('\n');
}

// ---- Fetch today's scores + in-progress paths (startup sync) ---------------
async function fetchMyData() {
  if (!INIT_DATA) return { scores: [], progress: [] };
  try {
    const res = await fetch('/me', { headers: { Authorization: `tma ${INIT_DATA}` } });
    if (!res.ok) return { scores: [], progress: [] };
    return await res.json();
  } catch (_) {
    return { scores: [], progress: [] };
  }
}

// ---- Stats from server -----------------------------------------------------
async function fetchStats(wordLength = 5) {
  if (!INIT_DATA) return null;
  try {
    const res = await fetch(`/stats?length=${wordLength}`, {
      headers: { Authorization: `tma ${INIT_DATA}` },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

// Transform server stats shape → FinishedScreen shape.
function apiStatsToLocal(s) {
  const dist = Array(7).fill(0);
  for (const [k, v] of Object.entries(s.distribution || {})) {
    const i = Math.min(parseInt(k, 10), 6);
    if (!isNaN(i) && i >= 0) dist[i] = v;
  }
  return {
    wins:          s.total_won      ?? 0,
    totalExtra:    s.total_extra    ?? 0,
    currentStreak: s.current_streak ?? 0,
    bestStreak:    s.longest_streak ?? 0,
    dist,
    counted:       {},
  };
}

// ---- Score submission ------------------------------------------------------
async function postScore(path, gaveUp, invalidAttempts, wordLength = 5, timedOut = false, timeAttack = false, hardMode = false) {
  if (!INIT_DATA) return null;
  try {
    const res = await fetch('/score', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        init_data:        INIT_DATA,
        path:             path.map(w => w.toLowerCase()),
        gave_up:          gaveUp,
        timed_out:        timedOut,
        time_attack:      timeAttack,
        word_length:      wordLength,
        invalid_attempts: invalidAttempts ?? 0,
        chat_id:          CHAT_ID,
        hard_mode:        hardMode,
      }),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

// ---- App -------------------------------------------------------------------
function App() {
  const [screen,       setScreen]       = React.useState('loading');
  // puzzles loaded from API: { 4: puzzleObj, 5: puzzleObj }
  const [puzzles,      setPuzzles]      = React.useState({});
  // the puzzle currently being played
  const [activePuzzle, setActivePuzzle] = React.useState(null);
  // played results keyed by "${day}-${length}"
  const [played,       setPlayed]       = React.useState(loadPlayed);
  const [inProgress,   setInProgress]   = React.useState({});
  // solve-timer start times keyed "${day}-${length}" → epoch ms
  const [startTimes,   setStartTimes]   = React.useState(loadStarts);
  // shot-clock deadlines keyed "${day}-${length}" → epoch ms (null clears)
  const [deadlines,    setDeadlines]    = React.useState(loadDeadlines);
  const setDeadlineFor = (key, val) => {
    setDeadlines(prev => {
      const next = { ...prev };
      if (val == null) delete next[key]; else next[key] = val;
      try { localStorage.setItem(DEADLINE_KEY, JSON.stringify(next)); } catch (_) {}
      return next;
    });
  };
  // Time Attack is always on; taFail[key] records that the clock lapsed for a
  // puzzle (→ dropped + finished untimed, so no ⚡). A solve with no fail flag
  // is a clean ⚡ TA win.
  const [taFail,       setTaFail]       = React.useState(loadTaFail);
  const markTaFail = (key) => {
    setTaFail(prev => {
      if (prev[key]) return prev;
      const next = { ...prev, [key]: true };
      try { localStorage.setItem(TA_FAIL_KEY, JSON.stringify(next)); } catch (_) {}
      return next;
    });
  };
  // ref so the timeout closure reads the freshest fail map without re-subscribing
  const taFailRef = React.useRef(taFail); taFailRef.current = taFail;
  // Hard Mode: day-level toggle, freely flippable (no lock) so all four
  // puzzles are playable. Slams the rubber stamp down + a little haptic.
  const [hardPref,     setHardPref]     = React.useState(loadHardPref);
  const toggleHardPref = () => {
    setHardPref(prev => {
      const next = !prev;
      try { localStorage.setItem(HARD_PREF_KEY, next ? '1' : '0'); } catch (_) {}
      tg?.HapticFeedback?.impactOccurred(next ? 'medium' : 'light');
      return next;
    });
  };
  // zoom animation origin as CSS percentage strings
  const [zoomOrigin,   setZoomOrigin]   = React.useState({ ox: '50%', oy: '50%' });
  // whether to show leaderboard overlay on the lobby
  const [showLb,       setShowLb]       = React.useState(false);

  const [path,         setPath]         = React.useState([]);
  const [input,        setInput]        = React.useState('');
  const [hint,         setHint]         = React.useState(null);
  const [shake,        setShake]        = React.useState(false);
  const [committing,   setCommitting]   = React.useState(null);
  const [promoteIndex, setPromoteIndex] = React.useState(-1);
  // win celebration overlay: null | { perfect: bool }
  const [celebrating,  setCelebrating]  = React.useState(null);
  const [toast,        setToast]        = React.useState(null);
  const [stats,        setStats]        = React.useState(loadStats);
  const [scoreResult,  setScoreResult]  = React.useState(null);
  const [errorMsg,     setErrorMsg]     = React.useState(null);
  const [invalidAttempts, setInvalidAttempts] = React.useState(0);

  // ---- Load puzzles + sync today's scores from server at startup -----------
  React.useEffect(() => {
    let cancelled = false;
    Promise.all([
      window.Diddle.loadFromAPI(4),
      window.Diddle.loadFromAPI(5),
      fetchMyData(),
    ])
      .then(([pz4, pz5, myData]) => {
        if (cancelled) return;
        setPuzzles({ 4: pz4, 5: pz5 });
        const { scores: myScores, progress: myProgress } = myData;
        const dayNum = pz4.num;

        // Server is authoritative — overwrite local played state with DB truth.
        // Keyed by day-length-mode so normal and hard plays coexist.
        const completedKeys = new Set();
        if (myScores.length > 0) {
          setPlayed(prev => {
            const next = { ...prev };
            for (const s of myScores) {
              const k = pkey(dayNum, s.word_length, s.hard_mode);
              completedKeys.add(k);
              const failed = s.gave_up || s.timed_out;
              const ss = s.solve_seconds ?? null;
              next[k] = {
                delta:        failed ? null : s.delta,
                moves:        s.moves,
                gaveUp:       s.gave_up,
                timedOut:     !!s.timed_out,
                timeAttack:   !!s.time_attack,
                hardMode:     !!s.hard_mode,
                solveSeconds: ss,
                score:        (!failed && ss != null) ? ss + s.moves * 60 : null,
                path:         s.path.map(w => w.toUpperCase()),
              };
            }
            try { localStorage.setItem(PLAYED_KEY, JSON.stringify(next)); } catch (_) {}
            return next;
          });
        }

        // Restore in-progress paths for unfinished puzzles (keyed day-length-mode)
        if (myProgress.length > 0) {
          const resume = {};
          const starts = {};
          for (const p of myProgress) {
            const k = pkey(dayNum, p.word_length, p.hard_mode);
            if (completedKeys.has(k)) continue;
            if (p.path.length > 1) {
              resume[k] = p.path.map(w => w.toUpperCase());
            }
            // Server start is authoritative for the display clock too
            // (SQLite datetime('now') is UTC, "YYYY-MM-DD HH:MM:SS")
            if (p.started_at) {
              starts[k] = new Date(p.started_at.replace(' ', 'T') + 'Z').getTime();
            }
          }
          if (Object.keys(resume).length > 0) setInProgress(resume);
          if (Object.keys(starts).length > 0) {
            setStartTimes(prev => {
              const next = { ...prev, ...starts };
              try { localStorage.setItem(START_KEY, JSON.stringify(next)); } catch (_) {}
              return next;
            });
          }
        }

        // Drop shot-clock deadlines + TA-fail flags from previous days or
        // finished puzzles (keep only today's still-unfinished puzzles)
        const liveKey = (k) => k.startsWith(`${dayNum}-`) && !completedKeys.has(k);
        const pruneToLive = (prev, storeKey) => {
          const next = Object.fromEntries(Object.entries(prev).filter(([k]) => liveKey(k)));
          if (Object.keys(next).length !== Object.keys(prev).length) {
            try { localStorage.setItem(storeKey, JSON.stringify(next)); } catch (_) {}
          }
          return next;
        };
        setDeadlines(prev => pruneToLive(prev, DEADLINE_KEY));
        setTaFail(prev => pruneToLive(prev, TA_FAIL_KEY));

        setScreen('lobby');
        // Notify the group board that this user is playing (fire-and-forget)
        if (CHAT_ID && INIT_DATA) {
          fetch('/playing', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ init_data: INIT_DATA, chat_id: CHAT_ID }),
          }).catch(() => {});
        }
      })
      .catch(err => {
        if (cancelled) return;
        setErrorMsg(err.message || 'Failed to load puzzles');
        setScreen('error');
      });
    return () => { cancelled = true; };
  }, []);

  // ---- Zoom animation: after 340 ms switch to playing ----------------------
  React.useEffect(() => {
    if (screen !== 'zooming') return;
    const id = setTimeout(() => setScreen('playing'), 340);
    return () => clearTimeout(id);
  }, [screen]);

  // ---- Toast ---------------------------------------------------------------
  const flashToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 1800);
  };

  // ---- Share handler -------------------------------------------------------
  const shareRef = React.useRef(null);
  React.useEffect(() => {
    const dayNum = puzzles[4]?.num || puzzles[5]?.num;
    if (!dayNum) return;
    shareRef.current = () => {
      // Share the variant currently in view; fall back to whichever has results.
      const txt = buildShareText(played, dayNum, hardPref)
               || buildShareText(played, dayNum, !hardPref);
      if (!txt) return;
      try { navigator.clipboard?.writeText(txt); } catch (_) {}
      flashToast('Copied — paste in chat!');
      tg?.HapticFeedback?.notificationOccurred('success');
    };
  }, [played, puzzles, hardPref]);

  // ---- Telegram MainButton -------------------------------------------------
  React.useEffect(() => {
    if (!tg?.MainButton) return;
    const handler = () => shareRef.current?.();
    tg.MainButton.onClick(handler);
    return () => tg.MainButton.offClick(handler);
  }, []);

  React.useEffect(() => {
    if (!tg?.MainButton) return;
    if (screen === 'finished') {
      tg.MainButton.setText('Share Score 🔤');
      tg.MainButton.show();
    } else {
      tg.MainButton.hide();
    }
  }, [screen]);

  // ---- Shot-clock lapsed → NON-fatal: drop the clock, keep playing untimed ---
  // The puzzle loses its ⚡ (clean TA) but is otherwise played normally from here.
  const expireClock = (puzzle) => {
    const key = puzKey(puzzle);
    markTaFail(key);
    setDeadlineFor(key, null);
    DiddleSound?.buzz?.();
    tg?.HapticFeedback?.notificationOccurred('warning');
    flashToast("Clock's up — keep going, untimed");
  };

  // ShotClock fires this mid-game; the ref keeps the closure fresh without
  // forcing the clock's effect to re-subscribe on every state change.
  const timeoutFnRef = React.useRef(() => {});
  timeoutFnRef.current = () => {
    if (!activePuzzle) return;
    expireClock(activePuzzle);
  };
  const handleTimeout = React.useCallback(() => timeoutFnRef.current(), []);

  // ---- Handle card tap -----------------------------------------------------
  const handlePlay = (puzzle, cardEl) => {
    const key = puzKey(puzzle);
    const playedEntry = played[key];
    DiddleSound?.unlock();   // user gesture — arm the shot-clock beeps

    if (playedEntry) {
      // Already played — show their completed ladder without zoom
      setActivePuzzle(puzzle);
      setPath(playedEntry.path || [puzzle.start, puzzle.target]);
      setScreen('result');
      return;
    }

    const startPath = inProgress[key] || [puzzle.start];

    // Time Attack is always on (no toggle). The clock runs unless it already
    // lapsed for this puzzle (taFail) — then it's played untimed, no ⚡.
    if (puzzle.timeLimit > 0 && !taFail[key]) {
      const stored = deadlines[key];
      if (stored && Date.now() >= stored) {
        // deadline passed while the app was closed — drop the clock (non-fatal),
        // mark not-clean, and just continue into the puzzle untimed.
        markTaFail(key);
        setDeadlineFor(key, null);
      } else if (!stored) {
        // first reveal — clock starts once the zoom animation lands (340 ms)
        setDeadlineFor(key, Date.now() + puzzle.timeLimit * 1000 + 340);
      }
    }

    // Unplayed — zoom animation into game, resuming prior path if available
    let ox = '50%', oy = '50%';
    if (cardEl) {
      const r = cardEl.getBoundingClientRect();
      ox = `${((r.left + r.width  / 2) / window.innerWidth  * 100).toFixed(1)}%`;
      oy = `${((r.top  + r.height / 2) / window.innerHeight * 100).toFixed(1)}%`;
    }
    setZoomOrigin({ ox, oy });
    setActivePuzzle(puzzle);
    setPath(startPath);
    // Stamp the solve-timer start at first reveal: server-side via /progress
    // (authoritative for the recorded time), locally for the display clock.
    // Wall-clock — leaving the app doesn't pause it.
    if (!startTimes[key]) {
      setStartTimes(prev => {
        const next = { ...prev, [key]: Date.now() };
        try { localStorage.setItem(START_KEY, JSON.stringify(next)); } catch (_) {}
        return next;
      });
    }
    if (INIT_DATA) {
      fetch('/progress', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          init_data:   INIT_DATA,
          path:        startPath.map(w => w.toLowerCase()),
          word_length: puzzle.length,
          chat_id:     CHAT_ID,
          hard_mode:   puzzle.hardMode,
        }),
      }).catch(() => {});
    }
    setInput('');
    setHint(null);
    setScoreResult(null);
    setInvalidAttempts(0);
    setScreen('zooming');
  };

  // ---- Return to lobby after finishing/giving up ---------------------------
  const handleBackToLobby = () => {
    setActivePuzzle(null);
    setScreen('lobby');
  };

  // ---- Submit --------------------------------------------------------------
  const submit = () => {
    if (!activePuzzle || committing || celebrating) return;
    const last = path[path.length - 1];
    const res  = activePuzzle.validate(last, input);
    if (!res.ok) {
      setHint({ text: res.reason, err: true });
      setShake(true);
      setInvalidAttempts(n => n + 1);
      setTimeout(() => setShake(false), 420);
      return;
    }

    const word       = res.word;
    const win        = word === activePuzzle.target;
    const finalMoves = path.length;
    setHint(null);

    const pathSnap = path;
    const key      = puzKey(activePuzzle);
    // clock is live iff it hasn't lapsed for this puzzle; a clean (never-lapsed)
    // solve earns ⚡. Read the ref so a just-fired lapse is reflected.
    const clockLive = activePuzzle.timeLimit > 0 && !taFailRef.current[key];

    const promote = () => {
      const newPath = [...pathSnap, word];
      setPath(newPath);
      setPromoteIndex(pathSnap.length);
      setInput('');
      setCommitting(null);
      setTimeout(() => setPromoteIndex(-1), 660);

      // shot clock: a fresh window starts the moment input unlocks again
      if (!win && clockLive) {
        setDeadlineFor(key, Date.now() + activePuzzle.timeLimit * 1000);
      }

      // Track progress after each valid move (winning move handled by POST /score)
      if (!win && INIT_DATA) {
        fetch('/progress', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            init_data:   INIT_DATA,
            path:        newPath.map(w => w.toLowerCase()),
            word_length: activePuzzle.length,
            chat_id:     CHAT_ID,
            hard_mode:   activePuzzle.hardMode,
          }),
        }).catch(() => {});
      }

      if (win) {
        const localDelta = newPath.length - 1 - activePuzzle.par;
        // Celebrate immediately — the green flip just finished, no dead air.
        // Results come in while confetti is still falling over them.
        setCelebrating({ perfect: localDelta <= 0 });
        tg?.HapticFeedback?.notificationOccurred('success');
        setTimeout(() => setScreen('finished'), 1100);
        setTimeout(() => setCelebrating(null), 2200);
        setStats(prev => recordWin(prev, activePuzzle.num, activePuzzle.length,
                                   Math.max(0, localDelta), activePuzzle.hardMode));
        // Save immediately so lobby card updates; rank filled in after API responds
        setPlayed(prev => {
          const next = { ...prev, [key]: { delta: localDelta, moves: newPath.length - 1, gaveUp: false, timeAttack: clockLive, hardMode: activePuzzle.hardMode, solveSeconds: null, path: newPath } };
          try { localStorage.setItem(PLAYED_KEY, JSON.stringify(next)); } catch (_) {}
          return next;
        });
        postScore(newPath, false, invalidAttempts, activePuzzle.length, false, clockLive, activePuzzle.hardMode).then(async r => {
          if (!r) return;
          setScoreResult(r);
          // Use canonical server values — covers the edge case of a duplicate submission
          const canonPath = (r.path || []).map(w => w.toUpperCase());
          setPlayed(prev => {
            const existing = prev[key] || {};
            const next = { ...prev, [key]: {
              ...existing,
              delta:        r.delta,
              moves:        r.moves,
              gaveUp:       r.gave_up,
              timedOut:     !!r.timed_out,
              timeAttack:   !!r.time_attack,
              solveSeconds: r.solve_seconds ?? null,
              score:        r.score ?? null,
              rank:         r.rank,
              ...(canonPath.length > 1 ? { path: canonPath } : {}),
            }};
            try { localStorage.setItem(PLAYED_KEY, JSON.stringify(next)); } catch (_) {}
            return next;
          });
          const apiStats = await fetchStats(activePuzzle.length);
          if (apiStats) setStats(apiStatsToLocal(apiStats));
        });
      }
    };

    setCommitting({ word, win });
    setInput('');
    // valid word beat the clock — pause it for the flip animation (input is
    // locked); promote() starts the next window, or the win clears it for good
    if (clockLive) {
      setDeadlineFor(key, null);
    }
    setTimeout(promote, activePuzzle.length * 140 + 720);
  };

  // ---- Give up -------------------------------------------------------------
  const handleGiveUp = () => {
    const snap = [...path];
    const len  = activePuzzle.length;
    const key  = puzKey(activePuzzle);
    setDeadlineFor(key, null);
    // Save immediately so lobby card updates (gave-up = DNF, no score)
    setPlayed(prev => {
      const next = { ...prev, [key]: { delta: null, moves: snap.length - 1, gaveUp: true, timeAttack: false, hardMode: activePuzzle.hardMode, path: snap } };
      try { localStorage.setItem(PLAYED_KEY, JSON.stringify(next)); } catch (_) {}
      return next;
    });
    postScore(snap, true, 0, len, false, false, activePuzzle.hardMode).then(r => {
      if (!r) return;
      setScoreResult(r);
      // If DB says the user actually won (duplicate gaveUp), correct local state
      const canonPath = (r.path || []).map(w => w.toUpperCase());
      setPlayed(prev => {
        const existing = prev[key] || {};
        const next = { ...prev, [key]: {
          ...existing,
          gaveUp:     r.gave_up,
          timedOut:   !!r.timed_out,
          timeAttack: !!r.time_attack,
          delta:      (r.gave_up || r.timed_out) ? null : r.delta,
          moves:      r.moves,
          ...(canonPath.length > 1 ? { path: canonPath } : {}),
        }};
        try { localStorage.setItem(PLAYED_KEY, JSON.stringify(next)); } catch (_) {}
        return next;
      });
    });
    setScreen('gaveup');
  };

  // ---- Share / retry -------------------------------------------------------
  const handleShare = () => shareRef.current?.();
  const handleRetry = () => window.location.reload();

  // ---- Render --------------------------------------------------------------
  const isInPuzzle = !['loading', 'lobby', 'zooming', 'error'].includes(screen);

  let body;
  if (screen === 'loading') {
    body = <LoadingScreen />;
  } else if (screen === 'error') {
    body = <ErrorScreen message={errorMsg} onRetry={handleRetry} />;
  } else if (screen === 'finished') {
    body = <FinishedScreen puzzle={activePuzzle} path={path} stats={stats}
                           scoreResult={scoreResult}
                           onShare={handleShare} onClose={handleBackToLobby} />;
  } else if (screen === 'gaveup') {
    body = <GaveUpScreen puzzle={activePuzzle} path={path} onClose={handleBackToLobby} />;
  } else if (screen === 'result') {
    const entry = activePuzzle ? played[puzKey(activePuzzle)] : null;
    body = (
      <ResultScreen
        puzzle={activePuzzle}
        path={path}
        gaveUp={entry?.gaveUp || false}
        timedOut={entry?.timedOut || false}
        timeAttack={entry?.timeAttack || false}
        delta={entry?.delta ?? 0}
        score={entry?.score ?? holeScore(entry?.solveSeconds, entry?.moves)}
        onShare={(!entry?.gaveUp && !entry?.timedOut) ? handleShare : null}
        onClose={handleBackToLobby}
      />
    );
  } else if (screen === 'playing') {
    body = (
      <PlayingScreen
        puzzle={activePuzzle}
        path={path}
        input={input}
        setInput={setInput}
        onSubmit={submit}
        hint={hint}
        shake={shake}
        committing={committing}
        promoteIndex={promoteIndex}
        celebrating={!!celebrating}
        onGiveUp={handleGiveUp}
        startTs={startTimes[puzKey(activePuzzle)]}
        deadline={deadlines[puzKey(activePuzzle)] ?? null}
        onTimeout={handleTimeout}
      />
    );
  } else {
    // 'lobby' or 'zooming' — always render the lobby beneath the zoom overlay.
    // Hard Mode swaps both cards to the harder variant (when one exists).
    const hardAvailable = !!(puzzles[4]?.hardVariant && puzzles[5]?.hardVariant);
    const showHard = hardPref && hardAvailable;
    const viewPuzzles = {};
    for (const len of [4, 5]) {
      const p = puzzles[len];
      viewPuzzles[len] = (showHard && p?.hardVariant) ? p.hardVariant : p;
    }
    body = (
      <>
        {showLb
          ? <LeaderboardScreen initData={INIT_DATA} onClose={() => setShowLb(false)} />
          : <LobbyScreen puzzles={viewPuzzles} played={played}
                         onPlay={handlePlay}
                         onShare={handleShare}
                         hardPref={showHard} hardAvailable={hardAvailable}
                         onToggleHard={toggleHardPref}
                         onLeaderboard={() => setShowLb(true)} />
        }
        {screen === 'zooming' && (
          <div className="zoom-overlay"
               style={{ '--ox': zoomOrigin.ox, '--oy': zoomOrigin.oy }} />
        )}
      </>
    );
  }

  const dayNum = puzzles[4]?.num || puzzles[5]?.num || '';
  const subLabel = activePuzzle && screen !== 'lobby'
    ? `day ${activePuzzle.num} · par ${activePuzzle.par}${activePuzzle.isChallenge ? ' · 😈' : ''}`
    : dayNum ? `day ${dayNum}` : 'loading…';

  return (
    <div
      className="app"
      data-dir="quiet"
      data-theme={COLOR_SCHEME}
      data-hl="fill"
      data-motion="med"
    >
      <div className="tg-header">
        {isInPuzzle && (
          <button className="back-btn" onClick={handleBackToLobby} aria-label="Back to lobby">‹</button>
        )}
        <Mark />
        <Wordmark />
        <span className="sub">{subLabel}</span>
      </div>
      {body}
      {celebrating && <Confetti perfect={celebrating.perfect} />}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
