/* Diddle — the five screens. Each is a pure component driven by App state. */
const { Tiles, ChainRow, Replay, Wordmark, Mark } = window;

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

function PlayingScreen({ puzzle, path, input, setInput, onSubmit, hint, shake, committing, promoteIndex, bounce, onGiveUp }) {
  const inputRef = React.useRef(null);
  const historyRef = React.useRef(null);
  const len = puzzle.length;
  const last = path[path.length - 1];
  const moves = path.length - 1;
  const best = puzzle.bestFromHere(last);

  const focusInput = () => { const el = inputRef.current; if (el && !committing) el.focus(); };
  React.useEffect(() => { focusInput(); }, []);
  React.useEffect(() => { if (!committing) focusInput(); }, [committing]);
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

  const handleKey = (e) => { if (e.key === "Enter") { e.preventDefault(); onSubmit(); } };
  const clean = (v) => v.replace(/[^a-zA-Z]/g, "").slice(0, len).toUpperCase();

  const entryWord = committing ? committing.word : input;
  const entryTarget = committing ? puzzle.target : null;
  const activeIndex = committing ? -1 : (input.length < len ? input.length : -1);

  return (
    <div className="screen playing">
      <div className={"playstack" + (bounce ? " bounce" : "")}>
        <div className="history" ref={historyRef}>
          {path.map((w, i) => (
            <ChainRow key={i} word={w} target={puzzle.target} step={i}
              isWin={w === puzzle.target} promote={i === promoteIndex} />
          ))}
        </div>

        <div className="entry-zone" onMouseDown={(e) => { if (e.target.tagName !== "INPUT") { e.preventDefault(); focusInput(); } }}>
          <div className={"row entry cur" + (shake ? " shake" : "")}>
            <div className="gutter">{path.length}</div>
            <Tiles word={entryWord} len={len} target={entryTarget}
              entry={!committing} flip={!!committing}
              activeIndex={activeIndex} />
          </div>
          <input
            ref={inputRef}
            className="entry-capture"
            value={input}
            onChange={(e) => setInput(clean(e.target.value))}
            onKeyDown={handleKey}
            inputMode="text"
            autoCapitalize="characters"
            autoCorrect="off"
            spellCheck="false"
            maxLength={len}
            disabled={!!committing}
            aria-label="Type your next word"
          />
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

      <button className="giveup-link" onClick={onGiveUp}>give up &amp; see the answer</button>
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

function FinishedScreen({ puzzle, path, stats, onShare, onClose }) {
  const moves = path.length - 1;
  const best = puzzle.par;
  const extra = Math.max(0, moves - best);
  const perfect = extra === 0;
  const avg = stats.wins ? stats.totalExtra / stats.wins : 0;
  const maxd = Math.max(1, ...stats.dist);

  return (
    <div className="screen">
      <div className="sheet finish">
        <div className={"finish-tag" + (perfect ? " perfect" : "")}>{perfect ? "Perfect!" : "Solved"}</div>
        <div className="finish-pair">{puzzle.start}<span className="to">to</span>{puzzle.target}</div>

        <div className="finish-lines">
          <div>You used <b>{moves}</b> {moves === 1 ? "move" : "moves"}.</div>
          <div>The best solution was <b>{best}</b> {best === 1 ? "move" : "moves"}.</div>
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
        <button className="linkbtn" onClick={onClose}>Back to chat</button>
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
        <button className="linkbtn" onClick={onClose}>Back to chat</button>
      </div>
    </div>
  );
}

Object.assign(window, { LoadingScreen, ErrorScreen, PlayingScreen, FinishedScreen, GaveUpScreen });
