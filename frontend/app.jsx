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
  Wordmark, Mark,
} = window;

// ---- Win stats (localStorage) — per-puzzle-length counters -----------------
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
function recordWin(stats, num, length, extra) {
  const key = `${num}-${length}`;
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
const PLAYED_KEY = 'diddle.played.v1';
function loadPlayed() {
  try {
    const s = JSON.parse(localStorage.getItem(PLAYED_KEY));
    if (s && typeof s === 'object') return s;
  } catch (_) {}
  return {};
}

// ---- Share text ------------------------------------------------------------
function deltaEmoji(delta) {
  if (delta === 0) return '🎯';
  if (delta <= 2) return '⭐';
  if (delta <= 4) return '😂';
  return '🤡';
}

function buildShareText(played, dayNum) {
  const completions = [4, 5]
    .map(len => ({ len, entry: played[`${dayNum}-${len}`] }))
    .filter(x => x.entry && !x.entry.gaveUp);

  if (completions.length === 0) return '';

  if (completions.length === 1) {
    const { len, entry } = completions[0];
    const emoji = deltaEmoji(entry.delta);
    const lines = [`Diddle #${dayNum} (${len}L) ${emoji}`];
    lines.push(entry.delta === 0
      ? `${entry.moves} moves — PERFECT`
      : `${entry.moves} moves (+${entry.delta})`);
    return lines.join('\n');
  }

  // Both completed
  const lines = [`Diddle #${dayNum}`];
  for (const { len, entry } of completions) {
    const emoji = deltaEmoji(entry.delta);
    const deltaStr = entry.delta > 0 ? ` +${entry.delta}` : '';
    lines.push(`${len}L · ${entry.moves} moves${deltaStr} ${emoji}`);
  }
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
async function postScore(path, gaveUp, invalidAttempts, wordLength = 5) {
  if (!INIT_DATA) return null;
  try {
    const res = await fetch('/score', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        init_data:        INIT_DATA,
        path:             path.map(w => w.toLowerCase()),
        gave_up:          gaveUp,
        word_length:      wordLength,
        invalid_attempts: invalidAttempts ?? 0,
        chat_id:          CHAT_ID,
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
  const [bounce,       setBounce]       = React.useState(false);
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

        // Server is authoritative — overwrite local played state with DB truth
        const completedLengths = new Set();
        if (myScores.length > 0) {
          setPlayed(prev => {
            const next = { ...prev };
            for (const s of myScores) {
              completedLengths.add(s.word_length);
              next[`${dayNum}-${s.word_length}`] = {
                delta:  s.gave_up ? null : s.delta,
                moves:  s.moves,
                gaveUp: s.gave_up,
                path:   s.path.map(w => w.toUpperCase()),
              };
            }
            try { localStorage.setItem(PLAYED_KEY, JSON.stringify(next)); } catch (_) {}
            return next;
          });
        }

        // Restore in-progress paths for unfinished puzzles
        if (myProgress.length > 0) {
          const resume = {};
          for (const p of myProgress) {
            if (!completedLengths.has(p.word_length) && p.path.length > 1) {
              resume[p.word_length] = p.path.map(w => w.toUpperCase());
            }
          }
          if (Object.keys(resume).length > 0) setInProgress(resume);
        }

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
      const txt = buildShareText(played, dayNum);
      if (!txt) return;
      try { navigator.clipboard?.writeText(txt); } catch (_) {}
      flashToast('Copied — paste in chat!');
      tg?.HapticFeedback?.notificationOccurred('success');
    };
  }, [played, puzzles]);

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

  // ---- Handle card tap -----------------------------------------------------
  const handlePlay = (puzzle, cardEl) => {
    const key = `${puzzle.num}-${puzzle.length}`;
    const playedEntry = played[key];

    if (playedEntry) {
      // Already played — show their completed ladder without zoom
      setActivePuzzle(puzzle);
      setPath(playedEntry.path || [puzzle.start, puzzle.target]);
      setScreen('result');
      return;
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
    setPath(inProgress[puzzle.length] || [puzzle.start]);
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
    if (!activePuzzle || committing || bounce) return;
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

    const promote = () => {
      const newPath = [...pathSnap, word];
      setPath(newPath);
      setPromoteIndex(pathSnap.length);
      setInput('');
      setCommitting(null);
      setTimeout(() => setPromoteIndex(-1), 660);

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
          }),
        }).catch(() => {});
      }

      if (win) {
        const localDelta = newPath.length - 1 - activePuzzle.par;
        const key = `${activePuzzle.num}-${activePuzzle.length}`;
        setStats(prev => recordWin(prev, activePuzzle.num, activePuzzle.length,
                                   Math.max(0, localDelta)));
        // Save immediately so lobby card updates; rank filled in after API responds
        setPlayed(prev => {
          const next = { ...prev, [key]: { delta: localDelta, moves: newPath.length - 1, gaveUp: false, path: newPath } };
          try { localStorage.setItem(PLAYED_KEY, JSON.stringify(next)); } catch (_) {}
          return next;
        });
        postScore(newPath, false, invalidAttempts, activePuzzle.length).then(async r => {
          if (!r) return;
          setScoreResult(r);
          // Use canonical server values — covers the edge case of a duplicate submission
          const canonPath = (r.path || []).map(w => w.toUpperCase());
          setPlayed(prev => {
            const existing = prev[key] || {};
            const next = { ...prev, [key]: {
              ...existing,
              delta:  r.delta,
              moves:  r.moves,
              gaveUp: r.gave_up,
              rank:   r.rank,
              ...(canonPath.length > 1 ? { path: canonPath } : {}),
            }};
            try { localStorage.setItem(PLAYED_KEY, JSON.stringify(next)); } catch (_) {}
            return next;
          });
          const apiStats = await fetchStats(activePuzzle.length);
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
    setTimeout(promote, activePuzzle.length * 140 + 720);
  };

  // ---- Give up -------------------------------------------------------------
  const handleGiveUp = () => {
    const snap = [...path];
    const len  = activePuzzle.length;
    const num  = activePuzzle.num;
    const key  = `${num}-${len}`;
    // Save immediately so lobby card updates
    setPlayed(prev => {
      const next = { ...prev, [key]: { delta: null, moves: snap.length - 1, gaveUp: true, path: snap } };
      try { localStorage.setItem(PLAYED_KEY, JSON.stringify(next)); } catch (_) {}
      return next;
    });
    postScore(snap, true, 0, len).then(r => {
      if (!r) return;
      setScoreResult(r);
      // If DB says the user actually won (duplicate gaveUp), correct local state
      const canonPath = (r.path || []).map(w => w.toUpperCase());
      setPlayed(prev => {
        const existing = prev[key] || {};
        const next = { ...prev, [key]: {
          ...existing,
          gaveUp: r.gave_up,
          delta:  r.gave_up ? null : r.delta,
          moves:  r.moves,
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
    const entry = activePuzzle ? played[`${activePuzzle.num}-${activePuzzle.length}`] : null;
    body = (
      <ResultScreen
        puzzle={activePuzzle}
        path={path}
        gaveUp={entry?.gaveUp || false}
        delta={entry?.delta ?? 0}
        onShare={!entry?.gaveUp ? handleShare : null}
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
        bounce={bounce}
        onGiveUp={handleGiveUp}
      />
    );
  } else {
    // 'lobby' or 'zooming' — always render the lobby beneath the zoom overlay
    body = (
      <>
        {showLb
          ? <LeaderboardScreen initData={INIT_DATA} onClose={() => setShowLb(false)} />
          : <LobbyScreen puzzles={puzzles} played={played}
                         onPlay={handlePlay}
                         onShare={handleShare}
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
    ? `day ${activePuzzle.num} · ${activePuzzle.length} letters`
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
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
