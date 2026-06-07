/* Diddle — production app shell.
   Wired to the real API and Telegram Mini App SDK.
   No design-tool wrapper (IOSDevice / TweaksPanel). */

// ---- Telegram SDK init (runs before React, at script parse time) ------------
const tg          = window.Telegram?.WebApp ?? null;
const INIT_DATA   = tg?.initData   ?? '';
const COLOR_SCHEME = tg?.colorScheme ?? 'light';

if (tg) {
  tg.ready();
  tg.expand();
  tg.disableVerticalSwipes();
}

// ---- Globals set by components.jsx and screens.jsx -------------------------
const {
  LoadingScreen, ErrorScreen, PlayingScreen, FinishedScreen, GaveUpScreen,
  Wordmark, Mark,
} = window;

// ---- Stats (localStorage) --------------------------------------------------
const STATS_KEY  = 'diddle.stats.v1';
const ZERO_STATS = { wins: 0, totalExtra: 0, currentStreak: 0, bestStreak: 0,
                     dist: [0,0,0,0,0,0,0], counted: {} };
function loadStats() {
  try {
    const s = JSON.parse(localStorage.getItem(STATS_KEY));
    if (s && Array.isArray(s.dist)) return s;
  } catch (_) {}
  return JSON.parse(JSON.stringify(ZERO_STATS));
}
function recordWin(stats, num, extra) {
  if (stats.counted?.[num]) return stats;
  const s         = JSON.parse(JSON.stringify(stats));
  s.counted       = s.counted || {};
  s.counted[num]  = true;
  s.wins         += 1;
  s.totalExtra   += Math.max(0, extra);
  s.dist[Math.min(Math.max(0, extra), 6)] += 1;
  s.currentStreak += 1;
  s.bestStreak    = Math.max(s.bestStreak, s.currentStreak);
  try { localStorage.setItem(STATS_KEY, JSON.stringify(s)); } catch (_) {}
  return s;
}

// ---- Share text ------------------------------------------------------------
function emojiGrid(path, target) {
  return path
    .map(w => w.split('').map((ch, i) => ch === target[i] ? '🟩' : '⬜').join(''))
    .join('\n');
}

// ---- Stats -----------------------------------------------------------------
async function fetchStats() {
  if (!INIT_DATA) return null;
  try {
    const res = await fetch('/stats', {
      headers: { Authorization: `tma ${INIT_DATA}` },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

// Transform the server stats shape to the shape FinishedScreen expects.
// Server keys: total_won, total_extra, current_streak, longest_streak,
//              distribution (dict str→int, keys = extra moves 0-6)
// Local keys:  wins, totalExtra, currentStreak, bestStreak, dist (array[7])
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
async function postScore(path, gaveUp) {
  if (!INIT_DATA) return null;   // not running inside Telegram
  try {
    const res = await fetch('/score', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        init_data: INIT_DATA,
        path:      path.map(w => w.toLowerCase()),
        gave_up:   gaveUp,
      }),
    });
    if (!res.ok) return null;
    return await res.json();     // { moves, optimal, delta, rank, message }
  } catch (_) {
    return null;
  }
}

// ---- App -------------------------------------------------------------------
function App() {
  const [screen,       setScreen]       = React.useState('loading');
  const [puzzle,       setPuzzle]       = React.useState(null);
  const [path,         setPath]         = React.useState([]);
  const [input,        setInput]        = React.useState('');
  const [hint,         setHint]         = React.useState(null);
  const [shake,        setShake]        = React.useState(false);
  const [committing,   setCommitting]   = React.useState(null);
  const [promoteIndex, setPromoteIndex] = React.useState(-1);
  const [bounce,       setBounce]       = React.useState(false);
  const [toast,        setToast]        = React.useState(null);
  const [stats,        setStats]        = React.useState(loadStats);
  const [scoreResult,  setScoreResult]  = React.useState(null);
  const [errorMsg,     setErrorMsg]     = React.useState(null);

  // ---- Load puzzle from API on mount ---------------------------------------
  React.useEffect(() => {
    let cancelled = false;
    window.Diddle.loadFromAPI()
      .then(pz => {
        if (cancelled) return;
        setPuzzle(pz);
        setPath([pz.start]);
        setScreen('playing');
      })
      .catch(err => {
        if (cancelled) return;
        setErrorMsg(err.message || 'Failed to load puzzle');
        setScreen('error');
      });
    return () => { cancelled = true; };
  }, []);

  // ---- Toast ---------------------------------------------------------------
  const flashToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 1800);
  };

  // ---- Share handler — kept in a ref so the MainButton always calls the
  //      latest closure even though it's registered once --------------------
  const shareRef = React.useRef(null);
  React.useEffect(() => {
    if (!puzzle) return;
    shareRef.current = () => {
      const moves   = path.length - 1;
      const rank    = scoreResult?.rank;
      const rankStr = rank ? ` · #${rank} today` : '';
      const txt     = `Diddle No.${puzzle.num} — ${moves}/${puzzle.par} moves${rankStr}\n\n`
                    + emojiGrid(path, puzzle.target);
      try { navigator.clipboard?.writeText(txt); } catch (_) {}
      flashToast('Copied — paste in chat!');
      tg?.HapticFeedback?.notificationOccurred('success');
    };
  }, [path, scoreResult, puzzle]);

  // ---- Telegram MainButton -------------------------------------------------
  // Register the handler once; visibility is toggled by screen changes.
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

  // ---- Submit --------------------------------------------------------------
  const submit = () => {
    if (!puzzle || committing || bounce) return;
    const last = path[path.length - 1];
    const res  = puzzle.validate(last, input);
    if (!res.ok) {
      setHint({ text: res.reason, err: true });
      setShake(true);
      setTimeout(() => setShake(false), 420);
      return;
    }

    const word       = res.word;
    const win        = word === puzzle.target;
    const finalMoves = path.length;   // moves = finalMoves (after adding word)
    setHint(null);

    // Capture path snapshot for the timeout closure (path won't change while
    // committing=true, but snapshot makes the intent explicit).
    const pathSnap = path;

    const promote = () => {
      const newPath = [...pathSnap, word];
      setPath(newPath);
      setPromoteIndex(pathSnap.length);
      setInput('');
      setCommitting(null);
      setTimeout(() => setPromoteIndex(-1), 660);

      if (win) {
        setStats(prev => recordWin(prev, puzzle.num, Math.max(0, finalMoves - puzzle.par)));
        // Fire alongside the bounce animation; update stats from server when ready
        postScore(newPath, false).then(async r => {
          if (!r) return;
          setScoreResult(r);
          const apiStats = await fetchStats();
          if (apiStats) setStats(apiStatsToLocal(apiStats));
        });
        setTimeout(() => {
          setBounce(true);
          setTimeout(() => { setBounce(false); setScreen('finished'); }, 1000);
        }, 900);
      }
    };

    setCommitting({ word, win });
    setInput('');
    setTimeout(promote, puzzle.length * 140 + 720);
  };

  // ---- Give up -------------------------------------------------------------
  const handleGiveUp = () => {
    const snap = [...path];
    // Submit in background; show screen immediately
    postScore(snap, true).then(r => { if (r) setScoreResult(r); });
    setScreen('gaveup');
  };

  // ---- Share ---------------------------------------------------------------
  const handleShare = () => shareRef.current?.();

  // ---- Close Mini App ------------------------------------------------------
  const handleClose = () => tg?.close();

  // ---- Retry on error ------------------------------------------------------
  const handleRetry = () => window.location.reload();

  // ---- Render --------------------------------------------------------------
  let body;
  if (screen === 'loading' || !puzzle) {
    body = <LoadingScreen />;
  } else if (screen === 'error') {
    body = <ErrorScreen message={errorMsg} onRetry={handleRetry} />;
  } else if (screen === 'finished') {
    body = <FinishedScreen puzzle={puzzle} path={path} stats={stats}
                           onShare={handleShare} onClose={handleClose} />;
  } else if (screen === 'gaveup') {
    body = <GaveUpScreen puzzle={puzzle} path={path} onClose={handleClose} />;
  } else {
    body = (
      <PlayingScreen
        puzzle={puzzle}
        path={path}
        input={input}
        setInput={setInput}
        onSubmit={submit}
        hint={hint}
        shake={shake}
        committing={committing}
        promoteIndex={promoteIndex}
        bounce={bounce}
        onGiveUp={handleGiveUp}
      />
    );
  }

  return (
    <div
      className="app"
      data-dir="quiet"
      data-theme={COLOR_SCHEME}
      data-hl="fill"
      data-motion="med"
    >
      <div className="tg-header">
        <Mark />
        <Wordmark />
        <span className="sub">
          {puzzle ? `day ${puzzle.num} · 5 letters` : 'loading…'}
        </span>
      </div>
      {body}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
