/* Diddle — the five screens. Each is a pure component driven by App state. */
const { Tiles, ChainRow, Replay, Wordmark, Mark, Keyboard } = window;

function LoadingScreen() {
  return (
    <div className="screen">
      <div className="center-screen">
        <div className="spinner"></div>
        <p>Loading today's puzzle…</p>
      </div>
    </div>
  );
}

function ErrorScreen({ message, onRetry }) {
  return (
    <div className="screen">
      <div className="center-screen">
        <Mark />
        <p className="big">{message || "Couldn't reach the puzzle."}</p>
        <p>Check your connection and try again.</p>
        <button className="mainbtn" style={{ maxWidth: 220 }} onClick={onRetry}>Retry</button>
      </div>
    </div>
  );
}

function PlayingScreen({ puzzle, path, input, setInput, onSubmit, hint, shake, committing, promoteIndex, celebrating, onGiveUp }) {
  const historyRef = React.useRef(null);
  const len = puzzle.length;
  const last = path[path.length - 1];
  const moves = path.length - 1;
  const best = puzzle.bestFromHere(last);

  React.useEffect(() => {
    const el = historyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [path.length]);

  // on commit, slide the WHOLE stack up by exactly one row so old rows rise
  // together with the new one (no jerk). useLayoutEffect = applied before paint.
  React.useLayoutEffect(() => {
    if (promoteIndex < 0) return;
    const el = historyRef.current;
    if (!el) return;
    const row = el.querySelector(".row");
    const rowH = row ? row.getBoundingClientRect().height : 52;
    el.style.setProperty("--rise", rowH + "px");
    el.classList.remove("rising");
    void el.offsetWidth; // restart the animation
    el.classList.add("rising");
    const id = setTimeout(() => el.classList.remove("rising"), 640);
    return () => clearTimeout(id);
  }, [promoteIndex]);

  const clean = (v) => v.replace(/[^a-zA-Z]/g, "").slice(0, len).toUpperCase();

  const keyLocked = !!committing || !!celebrating;
  const handleVKey = (k) => {
    if (keyLocked) return;
    if (k === "ENTER") onSubmit();
    else if (k === "BACK") setInput(input.slice(0, -1));
    else setInput(clean(input + k));
  };

  // physical keyboard support (Telegram Desktop / web). No deps array on
  // purpose: handleVKey closes over input/committing, so re-attach per render.
  React.useEffect(() => {
    const onKeyDown = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Enter") handleVKey("ENTER");
      else if (e.key === "Backspace") handleVKey("BACK");
      else if (/^[a-zA-Z]$/.test(e.key)) handleVKey(e.key.toUpperCase());
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  // give up needs a second tap to confirm — it sits near the keyboard
  const [confirmGiveUp, setConfirmGiveUp] = React.useState(false);
  React.useEffect(() => {
    if (!confirmGiveUp) return;
    const id = setTimeout(() => setConfirmGiveUp(false), 2500);
    return () => clearTimeout(id);
  }, [confirmGiveUp]);
  const handleGiveUpTap = () => {
    if (confirmGiveUp) onGiveUp();
    else setConfirmGiveUp(true);
  };

  const entryWord = committing ? committing.word : input;
  const entryTarget = committing ? puzzle.target : null;
  const activeIndex = committing ? -1 : (input.length < len ? input.length : -1);

  return (
    <div className="screen playing">
      <div className="playstack">
        <div className="history" ref={historyRef}>
          {path.map((w, i) => (
            <ChainRow key={i} word={w} target={puzzle.target} step={i}
              isWin={w === puzzle.target} promote={i === promoteIndex}
              prev={i > 0 ? path[i - 1] : null} />
          ))}
        </div>

        <div className="entry-zone">
          <div className={"row entry cur" + (shake ? " shake" : "")}>
            <div className="gutter">{path.length}</div>
            <Tiles word={entryWord} len={len} target={entryTarget}
              entry={!committing} flip={!!committing}
              activeIndex={activeIndex} />
          </div>
          <div className={"hint-line" + (hint && hint.err ? " err" : "")}>
            {hint ? hint.text : (committing ? "\u00a0" : `change one letter of ${last}`)}
          </div>
        </div>
      </div>

      <div className="statusbar">
        <div className="target">
          <span className="lab">goal</span>
          <Tiles word={puzzle.target} variant="goal" />
        </div>
        <div className="counters">
          <div className="counter">
            <span className="n">{moves}</span>
            <span className="k">moves</span>
          </div>
          <div className="counter best">
            <span className="n">{best === Infinity ? "\u2014" : best}</span>
            <span className="k">best from here</span>
          </div>
        </div>
      </div>

      <button className={"giveup-link" + (confirmGiveUp ? " confirm" : "")} onClick={handleGiveUpTap}>
        {confirmGiveUp ? "tap again to give up" : "give up & see the answer"}
      </button>

      <Keyboard onKey={handleVKey} disabled={keyLocked} enterReady={input.length === len} />
    </div>
  );
}

function Countdown() {
  const calc = () => {
    const now = new Date();
    const end = new Date(now); end.setHours(24, 0, 0, 0);
    let s = Math.max(0, Math.floor((end - now) / 1000));
    const hh = String(Math.floor(s / 3600)).padStart(2, "0"); s %= 3600;
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    return `${hh}:${mm}:${ss}`;
  };
  const [v, setV] = React.useState(calc);
  React.useEffect(() => {
    const id = setInterval(() => setV(calc()), 1000);
    return () => clearInterval(id);
  }, []);
  return <div className="countdown">Next Diddle in <b>{v}</b></div>;
}

function ordinalSuffix(n) {
  const s = ["th","st","nd","rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function FinishedScreen({ puzzle, path, stats, scoreResult, onShare, onClose }) {
  const moves = path.length - 1;
  const best = puzzle.par;
  const extra = Math.max(0, moves - best);
  const perfect = extra === 0;
  const avg = stats.wins ? stats.totalExtra / stats.wins : 0;
  const maxd = Math.max(1, ...stats.dist);
  const ordinal = scoreResult?.ordinal_position;

  return (
    <div className="screen">
      <div className="sheet finish">
        <div className={"finish-tag" + (perfect ? " perfect" : "")}>{perfect ? "Perfect!" : "Solved"}</div>
        <div className="finish-pair">{puzzle.start}<span className="to">to</span>{puzzle.target}</div>

        <div className="finish-lines">
          <div>You used <b>{moves}</b> {moves === 1 ? "move" : "moves"}.</div>
          <div>The best solution was <b>{best}</b> {best === 1 ? "move" : "moves"}.</div>
          {ordinal && <div>You were the <b>{ordinalSuffix(ordinal)}</b> person to finish today.</div>}
        </div>
        <button className="copy" onClick={onShare}>Copy results</button>

        <div className="stats">
          <h4>Stats</h4>
          <div className="statgrid">
            <div className="statcell"><span className="v">{stats.wins}</span><span className="l">Wins</span></div>
            <div className="statcell"><span className="v">{avg.toFixed(2)}</span><span className="l">Avg. extra</span></div>
            <div className="statcell"><span className="v">{stats.currentStreak}</span><span className="l">Streak</span></div>
            <div className="statcell"><span className="v">{stats.bestStreak}</span><span className="l">Best streak</span></div>
          </div>
        </div>

        <div className="dist">
          <h4>Distribution · extra moves</h4>
          {stats.dist.map((c, i) => {
            const me = i === Math.min(extra, 6);
            return (
              <div className={"distrow" + (me ? " me" : "")} key={i}>
                <span className="k">{i === 6 ? "6+" : i}</span>
                <div className="bar" style={{ width: `${(c / maxd) * 100}%` }}>{c}</div>
              </div>
            );
          })}
        </div>

        <Countdown />
      </div>

      <div className="mainbtn-wrap">
        <button className="mainbtn green" onClick={onShare}>Share result</button>
        <button className="linkbtn" onClick={onClose}>← Back to puzzles</button>
      </div>
    </div>
  );
}

function GaveUpScreen({ puzzle, path, onClose }) {
  const last = path[path.length - 1];
  const away = puzzle.bestFromHere(last);
  return (
    <div className="screen">
      <div className="sheet">
        <div className="eyebrow">Diddle · No.{puzzle.num} · {puzzle.length} letters</div>
        <h1 className="headline">Maybe tomorrow.</h1>
        <p style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--tg-theme-hint-color)", marginTop: 8 }}>
          You were {away === Infinity ? "a few" : away} {away === 1 ? "move" : "moves"} from <b style={{ color: "var(--tg-theme-text-color)" }}>{puzzle.target}</b>.
        </p>

        <Replay title="how far you got" path={path} target={puzzle.target} />
        <Replay title={`the shortest route · ${puzzle.par} moves`} path={puzzle.optimalPath} target={puzzle.target} optimal={true} />
      </div>

      <div className="mainbtn-wrap">
        <button className="linkbtn" onClick={onClose}>← Back to puzzles</button>
      </div>
    </div>
  );
}

// ---- PuzzleCard ------------------------------------------------------------

function PuzzleCard({ puzzle, playedResult, onPlay }) {
  const cardRef = React.useRef(null);
  const played  = !!playedResult;
  const perfect = played && !playedResult.gaveUp && playedResult.delta === 0;

  function badge() {
    if (!played) return null;
    if (playedResult.gaveUp)      return <div className="card-result">gave up</div>;
    if (playedResult.delta === 0) return <div className="card-result perfect">Perfect!</div>;
    return <div className="card-result">+{playedResult.delta} moves</div>;
  }

  return (
    <div
      ref={cardRef}
      className={"puzzle-card" + (played ? " played" : "") + (perfect ? " perfect" : "")}
      onClick={() => onPlay(puzzle, cardRef.current)}
    >
      <div className="card-label">{puzzle.length} letters</div>
      <div className="card-words">
        <span className="card-word start">{puzzle.start}</span>
        <span className="card-arrow">↓</span>
        <span className="card-word end">{puzzle.target}</span>
      </div>
      <div className="card-par">par {puzzle.par}</div>
      {played ? badge() : <div className="card-play">Play →</div>}
    </div>
  );
}

// ---- LobbyScreen -----------------------------------------------------------

function LobbyScreen({ puzzles, played, onPlay, onLeaderboard, onShare }) {
  const dayNum = puzzles[4]?.num || puzzles[5]?.num || '';

  const completedCount = [4, 5].filter(len => {
    const e = played[`${dayNum}-${len}`];
    return e && !e.gaveUp;
  }).length;
  const shareLabel = completedCount === 2 ? 'Share Scores' : 'Share Score';

  return (
    <div className="screen lobby">
      <div className="lobby-day">Day {dayNum} — choose your puzzle</div>
      <div className="lobby-cards">
        {[4, 5].map(len => puzzles[len] ? (
          <PuzzleCard
            key={len}
            puzzle={puzzles[len]}
            playedResult={played[`${puzzles[len].num}-${len}`] || null}
            onPlay={onPlay}
          />
        ) : null)}
      </div>
      {completedCount > 0 && (
        <button className="lobby-share-btn" onClick={onShare}>
          {shareLabel} 📋
        </button>
      )}
      <button className="lobby-lb-btn" onClick={onLeaderboard}>
        🏆 Today's Leaderboard
      </button>
    </div>
  );
}

// ---- ResultScreen (replay a previously completed or gave-up puzzle) --------

function ResultScreen({ puzzle, path, gaveUp, delta, onShare, onClose }) {
  const moves   = path.length - 1;
  const perfect = !gaveUp && delta === 0;
  const last    = path[path.length - 1];
  const away    = gaveUp ? puzzle.bestFromHere(last) : 0;

  return (
    <div className="screen">
      <div className="sheet">
        <div className="eyebrow">Day {puzzle.num} · {puzzle.length} letters</div>
        <h1 className={"headline" + (perfect ? " win" : "")}>
          {gaveUp ? 'Maybe tomorrow.' : perfect ? 'Perfect!' : 'Solved!'}
        </h1>
        {gaveUp && (
          <p style={{ fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--tg-theme-hint-color)', marginTop: 8 }}>
            You were {away === Infinity ? 'a few' : away} {away === 1 ? 'move' : 'moves'} from{' '}
            <b style={{ color: 'var(--tg-theme-text-color)' }}>{puzzle.target}</b>.
          </p>
        )}
        {!gaveUp && (
          <p style={{ fontFamily: 'var(--mono)', fontSize: 14, color: 'var(--tg-theme-text-color)', marginTop: 12 }}>
            {moves} {moves === 1 ? 'move' : 'moves'}{delta > 0 ? ` · +${delta} over par` : ' · perfect par'}
          </p>
        )}
        <Replay
          title={gaveUp ? 'how far you got' : 'your solution'}
          path={path}
          target={puzzle.target}
        />
        {gaveUp && (
          <Replay
            title={`the shortest route · ${puzzle.par} moves`}
            path={puzzle.optimalPath}
            target={puzzle.target}
            optimal={true}
          />
        )}
      </div>
      <div className="mainbtn-wrap">
        {onShare && <button className="mainbtn green" onClick={onShare}>Share result</button>}
        <button className="linkbtn" onClick={onClose}>← Back to puzzles</button>
      </div>
    </div>
  );
}

// ---- LeaderboardScreen -----------------------------------------------------

const MEDALS = ['🥇', '🥈', '🥉'];

function LeaderboardScreen({ initData, onClose }) {
  const [length, setLength]   = React.useState(5);
  const [boards, setBoards]   = React.useState({});   // { 4: [...], 5: [...] }
  const [loading, setLoading] = React.useState(false);
  const [err,     setErr]     = React.useState(null);

  React.useEffect(() => {
    if (boards[length] !== undefined) return;  // already fetched
    setLoading(true);
    const headers = initData ? { Authorization: `tma ${initData}` } : {};
    fetch(`/leaderboard?length=${length}`, { headers })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => setBoards(prev => ({ ...prev, [length]: data })))
      .catch(() => setErr("Couldn't load leaderboard."))
      .finally(() => setLoading(false));
  }, [length]);

  const board = boards[length];
  let completionRank = 0;

  return (
    <div className="screen">
      <div className="sheet">
        <div className="eyebrow">Today's Leaderboard</div>

        <div className="lb-toggle">
          <button className={"lb-tab" + (length === 4 ? " active" : "")} onClick={() => setLength(4)}>
            4 letters
          </button>
          <button className={"lb-tab" + (length === 5 ? " active" : "")} onClick={() => setLength(5)}>
            5 letters
          </button>
        </div>

        {err && <p style={{ color: 'var(--tg-theme-hint-color)', fontFamily: 'var(--mono)', fontSize: 13, marginTop: 16 }}>{err}</p>}
        {loading && (
          <div style={{ display: 'flex', justifyContent: 'center', padding: '40px 0' }}>
            <div className="spinner" />
          </div>
        )}
        {board && board.length === 0 && (
          <p style={{ fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--tg-theme-hint-color)', marginTop: 20 }}>
            No scores yet — be the first! 🎯
          </p>
        )}
        {board && board.map((e, i) => {
          const gave  = e.gave_up;
          const delta = e.moves - e.optimal;
          if (!gave) completionRank++;
          const rank  = gave ? null : completionRank;
          const medal = rank && rank <= 3 ? MEDALS[rank - 1] : null;
          const avgStr = e.avg !== null && e.avg !== undefined ? `+${e.avg.toFixed(1)} avg` : '—';
          return (
            <div key={i} className={"lb-row" + (gave ? " gave-up" : "")}>
              <span className="lb-rank">{medal || (gave ? '—' : rank)}</span>
              <span className="lb-name">{e.name}</span>
              <span className="lb-moves">{gave ? 'gave up' : `${e.moves} moves`}</span>
              <span className={"lb-avg" + (e.avg === null || e.avg === undefined ? " dash" : "")}>
                {avgStr}
              </span>
            </div>
          );
        })}
      </div>
      <div className="mainbtn-wrap">
        <button className="linkbtn" onClick={onClose}>← Back to puzzles</button>
      </div>
    </div>
  );
}

Object.assign(window, {
  LoadingScreen, ErrorScreen, PlayingScreen, FinishedScreen, GaveUpScreen,
  LobbyScreen, PuzzleCard, LeaderboardScreen, ResultScreen,
});
