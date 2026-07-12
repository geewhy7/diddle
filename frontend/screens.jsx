/* Diddle — the five screens. Each is a pure component driven by App state. */
const { Tiles, ChainRow, Replay, Wordmark, Mark, Keyboard, DiddleSound } = window;

// m:ss, rolling to h:mm:ss past an hour
function fmtTime(secs) {
  if (secs === null || secs === undefined) return null;
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

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

// Per-guess countdown ("shot clock"). deadline is an epoch-ms timestamp;
// null = idle (full limit shown — e.g. during the commit flip, when input is
// locked). Remaining time derives from Date.now() every frame, so it's a
// strict wall clock: backgrounding the app doesn't pause it, and coming back
// past zero loses immediately. onTimeout fires exactly once per deadline.
function ShotClock({ deadline, limit, onTimeout }) {
  const [remaining, setRemaining] = React.useState(limit);
  const firedRef    = React.useRef(false);
  const lastBeepRef = React.useRef(null);
  const dangerAt = Math.min(5, limit / 4);   // pulse + beep zone (5 s at limit 20)
  const warnAt   = limit / 2;                // orange zone (10 s at limit 20)

  React.useEffect(() => {
    firedRef.current = false;
    lastBeepRef.current = null;
    if (!deadline) { setRemaining(limit); return; }
    let raf;
    const loop = () => {
      const rem = (deadline - Date.now()) / 1000;
      if (rem <= 0) {
        setRemaining(0);
        if (!firedRef.current) {
          firedRef.current = true;
          DiddleSound.buzz();
          window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred("error");
          onTimeout();
        }
        return;
      }
      const sec = Math.ceil(rem);
      if (rem <= dangerAt && lastBeepRef.current !== sec) {
        lastBeepRef.current = sec;   // one beep per remaining second: 5…4…3…2…1
        DiddleSound.tick();
        window.Telegram?.WebApp?.HapticFeedback?.impactOccurred("medium");
      }
      setRemaining(rem);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [deadline, limit, onTimeout]);

  const danger = !!deadline && remaining <= dangerAt;
  const warn   = !!deadline && !danger && remaining <= warnAt;
  const ss = String(Math.floor(remaining)).padStart(2, "0");
  const xx = String(Math.floor((remaining % 1) * 100)).padStart(2, "0");
  return (
    <div className={"counter shotclock" + (danger ? " danger" : warn ? " warn" : "")}>
      {/* key change per second re-triggers the pulse animation in the danger zone */}
      <span className="n" key={danger ? Math.ceil(remaining) : "idle"}>{ss}.{xx}</span>
      <span className="k">clock</span>
    </div>
  );
}

function PlayingScreen({ puzzle, path, input, setInput, onSubmit, hint, shake, committing, promoteIndex, celebrating, onGiveUp, startTs, deadline, onTimeout }) {
  const historyRef = React.useRef(null);
  const len = puzzle.length;
  const last = path[path.length - 1];
  const moves = path.length - 1;
  const best = puzzle.bestFromHere(last);

  // Time Attack is always on; the clock is live while a deadline is set. When it
  // lapses the deadline is cleared (clock dropped) → fall back to the count-up.
  const shotClockOn = deadline != null && puzzle.timeLimit > 0;

  // running solve clock (only when no shot clock) — freezes under the win overlay
  const [now, setNow] = React.useState(Date.now());
  React.useEffect(() => {
    if (celebrating || shotClockOn) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [celebrating, shotClockOn]);
  const elapsed = !shotClockOn && startTs
    ? fmtTime(Math.max(0, Math.floor((now - startTs) / 1000))) : null;

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
      DiddleSound.unlock();   // physical keys count as the audio-arming gesture too
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
        <div className="statusbar-main">
          <div className="counter moves-c">
            <span className="n">{moves}</span>
            <span className="k">moves</span>
          </div>
          <div className="target">
            <Tiles word={puzzle.target} variant="goal" />
          </div>
          <div className="counter best">
            <span className="n">{best === Infinity ? "\u2014" : best}</span>
            <span className="k">best from here</span>
          </div>
        </div>
        {(shotClockOn || elapsed) && (
          <div className="statusbar-time">
            {shotClockOn ? (
              <ShotClock deadline={celebrating ? null : deadline}
                         limit={puzzle.timeLimit} onTimeout={onTimeout} />
            ) : (
              <span className="solve-elapsed">{elapsed}</span>
            )}
          </div>
        )}
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

function FinishedScreen({ puzzle, path, stats, scoreResult, peers, peersLoading }) {
  const moves = path.length - 1;
  const best = puzzle.par;
  const delta = scoreResult?.delta ?? (moves - best);   // signed: <0 = under par
  const score = scoreResult?.score ?? holeScore(scoreResult?.solve_seconds, moves);
  const clean = !!scoreResult?.time_attack;             // clean time-attack solve

  const avg = stats.wins ? stats.totalExtra / stats.wins : 0;
  const maxd = Math.max(1, ...stats.dist);
  const extra = Math.max(0, delta);
  const ordinal = scoreResult?.ordinal_position;

  return (
    <div className="screen">
      <div className="sheet finish">
        <div className="finish-tag">Solved</div>
        <div className="finish-score"><ScorePill score={score} delta={delta} clean={clean} size="lg" /></div>
        <div className="finish-pair">{puzzle.start}<span className="to">to</span>{puzzle.target}</div>

        <div className="finish-lines">
          <div>
            You used <b>{moves}</b> {moves === 1 ? "move" : "moves"}
            {fmtTime(scoreResult?.solve_seconds) ? <> in <b>{fmtTime(scoreResult.solve_seconds)}</b></> : null}.
          </div>
          <div>
            Score <b>{fmtScore(score)}</b> = {fmtTime(scoreResult?.solve_seconds) || `${moves * 60}s`}
            {' '}+ <b>{moves}</b> {moves === 1 ? "guess" : "guesses"} × 60s.
          </div>
          <div>
            {delta < 0 ? <>Under par — best possible is <b>{best}</b> moves, you used {moves}. 🔥</>
              : delta === 0 ? <>Right on par (<b>{best}</b> moves).</>
              : <>Best possible was <b>{best}</b> {best === 1 ? "move" : "moves"} (+{delta}).</>}
          </div>
          {ordinal && <div>You were the <b>{ordinalSuffix(ordinal)}</b> person to finish today.</div>}
        </div>

        <LadderPicker puzzle={puzzle} path={path} peers={peers} peersLoading={peersLoading} soloSeconds={scoreResult?.solve_seconds} />

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
    </div>
  );
}

function GaveUpScreen({ puzzle, path, peers, peersLoading }) {
  const last = path[path.length - 1];
  const away = puzzle.bestFromHere(last);
  return (
    <div className="screen">
      <div className="sheet">
        <div className="eyebrow">Diddle · No.{puzzle.num} · {puzzle.length} letters</div>
        <GaveUpStamp />
        <p style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--tg-theme-hint-color)", marginTop: 14, textAlign: "center" }}>
          You were {away === Infinity ? "a few" : away} {away === 1 ? "move" : "moves"} from <b style={{ color: "var(--tg-theme-text-color)" }}>{puzzle.target}</b>.
        </p>

        <LadderPicker puzzle={puzzle} path={path} gaveUp={true} peers={peers} peersLoading={peersLoading} />
      </div>
    </div>
  );
}

// ---- PuzzleCard ------------------------------------------------------------

function PuzzleCard({ puzzle, playedResult, onPlay }) {
  const cardRef = React.useRef(null);
  const played  = !!playedResult;
  const solved  = played && !playedResult.gaveUp && !playedResult.timedOut;

  function badge() {
    if (!played) return null;
    if (playedResult.timedOut) return <div className="card-result timeout">⌛ out of time</div>;
    if (playedResult.gaveUp)   return <div className="card-result">gave up</div>;
    const score = playedResult.score ?? holeScore(playedResult.solveSeconds, playedResult.moves);
    return (
      <div className={"card-result score " + deltaClass(playedResult.delta)}>
        <ScorePill score={score} delta={playedResult.delta} clean={!!playedResult.timeAttack} size="sm" />
      </div>
    );
  }

  // words stay hidden until the card is played — the shot clock starts at
  // first reveal, so there's no pre-planning from the lobby
  const mysteryRow = (cls) => (
    <div className="mrow">
      {Array.from({ length: puzzle.length }, (_, i) => (
        <span key={i} className={"mtile" + (cls ? " " + cls : "")}>?</span>
      ))}
    </div>
  );

  return (
    <div
      ref={cardRef}
      className={"puzzle-card" + (played ? " played" : "") + (solved ? " perfect" : "")
        + (played && playedResult.timedOut ? " timedout" : "")
        + (puzzle.isChallenge ? " challenge" : "")}
      onClick={() => onPlay(puzzle, cardRef.current)}
    >
      <div className="card-label">{puzzle.length} letters</div>
      {played ? (
        <div className="card-words">
          <span className="card-word start">{puzzle.start}</span>
          <span className="card-arrow">↓</span>
          <span className="card-word end">{puzzle.target}</span>
        </div>
      ) : (
        <div className="card-words mystery">
          {mysteryRow("")}
          <span className="card-arrow">↓</span>
          {mysteryRow("end")}
        </div>
      )}
      <div className="card-par">par {puzzle.par}</div>
      {played ? badge() : <div className="card-play">Play →</div>}
    </div>
  );
}

// ---- LobbyScreen -----------------------------------------------------------

function LobbyScreen({ puzzles, played, onPlay, onLeaderboard, onHowTo,
                      hardPref, hardAvailable, onToggleHard }) {
  const dayNum = puzzles[4]?.num || puzzles[5]?.num || '';
  // The view's variant is hard when the toggle is on (cards swap to hardVariant).
  const isChallenge = !!hardPref;

  return (
    <div className="screen lobby">
      <button className="howto-fab" onClick={onHowTo} aria-label="How to play">?</button>
      {isChallenge && (
        <div className="challenge-stamp" aria-label="Hard Mode — extra hard puzzles">
          Hard<br />Mode
        </div>
      )}
      <div className="lobby-day">Day {dayNum} — choose your puzzle</div>
      {isChallenge && (
        <div className="challenge-note">today's ladders run deep</div>
      )}
      {hardAvailable && (
        <button
          className={"hard-toggle" + (hardPref ? " on" : "")}
          onClick={onToggleHard}
          role="switch" aria-checked={hardPref}
          aria-label="Hard Mode — swap to the harder puzzles"
        >
          <span className="ta-dot" />
          😈 Hard Mode
        </button>
      )}
      <div className="lobby-cards">
        {[4, 5].map(len => puzzles[len] ? (
          <PuzzleCard
            key={len}
            puzzle={puzzles[len]}
            playedResult={played[pkey(puzzles[len].num, len, puzzles[len].hardMode)] || null}
            onPlay={onPlay}
          />
        ) : null)}
      </div>
    </div>
  );
}

// ---- ResultScreen (replay a previously completed or gave-up puzzle) --------

function ResultScreen({ puzzle, path, gaveUp, timedOut, timeAttack, delta, score, solveSeconds, peers, peersLoading }) {
  const failed  = gaveUp || timedOut;
  const last    = path[path.length - 1];
  const away    = failed ? puzzle.bestFromHere(last) : 0;

  return (
    <div className="screen">
      <div className="sheet">
        <div className="eyebrow">Day {puzzle.num} · {puzzle.length} letters</div>
        {gaveUp ? (
          <GaveUpStamp />
        ) : (
          <h1 className={"headline" + (!failed ? " win" : "")}>
            {timedOut ? "Time's up. ⌛" : 'Solved!'}
          </h1>
        )}
        {!failed && (
          <div className="finish-score"><ScorePill score={score} delta={delta} clean={!!timeAttack} size="lg" /></div>
        )}
        {failed && (
          <p style={{ fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--tg-theme-hint-color)', marginTop: gaveUp ? 14 : 8, textAlign: gaveUp ? 'center' : 'left' }}>
            You were {away === Infinity ? 'a few' : away} {away === 1 ? 'move' : 'moves'} from{' '}
            <b style={{ color: 'var(--tg-theme-text-color)' }}>{puzzle.target}</b>.
          </p>
        )}
        <LadderPicker puzzle={puzzle} path={path} gaveUp={gaveUp} peers={peers} peersLoading={peersLoading} soloSeconds={solveSeconds} />
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
  const normalRows = board ? board.filter(e => !e.hard_mode) : null;
  const hardRows   = board ? board.filter(e => e.hard_mode) : null;

  // Render one cohort's rows with its own completion ranking (1,2,3…).
  const renderRows = (rows) => {
    let completionRank = 0;
    return rows.map((e, i) => {
      const failed = e.gave_up || e.timed_out;
      if (!failed) completionRank++;
      const rank  = failed ? null : completionRank;
      const medal = rank && rank <= 3 ? MEDALS[rank - 1] : null;
      const hasAvg = e.avg !== null && e.avg !== undefined;
      const avgStr = hasAvg ? `+${e.avg.toFixed(1)} avg` : '—';
      const ttToday = !!e.time_attack && !failed;   // this run was timed
      const ttWins  = e.tt_wins || 0;               // all-time ⚡ wins
      return (
        <div key={i} className={"lb-row" + (failed ? " gave-up" : "")}>
          <span className="lb-rank">{medal || (failed ? '—' : rank)}</span>
          <span className="lb-name">{e.name}</span>
          <span className="lb-moves">
            {e.timed_out ? '⌛ out of time'
              : e.gave_up ? 'gave up'
              : `${ttToday ? '⚡ ' : ''}${e.moves} moves${e.solve_seconds != null ? ` · ${fmtTime(e.solve_seconds)}` : ''}`}
          </span>
          <span className={"lb-avg" + (hasAvg ? "" : " dash")}>
            <span className="lb-avg-main">{avgStr}</span>
            {ttWins > 0 && <span className="lb-tt">⚡{ttWins}</span>}
          </span>
        </div>
      );
    });
  };

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
        {board && board.length > 0 && (
          <div className="lb-legend">avg steps ranks · ⚡ = time-attack solve</div>
        )}
        {normalRows && renderRows(normalRows)}
        {hardRows && hardRows.length > 0 && (
          <>
            <div className="lb-section">😈 Hard Mode</div>
            {renderRows(hardRows)}
          </>
        )}
      </div>
      <div className="mainbtn-wrap">
        <button className="linkbtn" onClick={onClose}>← Back to puzzles</button>
      </div>
    </div>
  );
}

// ---- HowToScreen (first-play instructions) ---------------------------------
// Two-step splash rendered as a full-screen overlay: step 1 is the ladder
// mechanic shown with a real solved ladder (live Tiles — amber ring = the
// changed letter, green fills as letters lock into the target's position);
// step 2 is par/scoring + the rare-word star. Gated to the first Play tap
// ever on this device (the shot clock stamps at reveal, so reading must not
// cost score time); the lobby "?" reopens it any time.

const HOWTO_LADDER = ["CAT", "COT", "COG", "DOG"];

function HowToRow({ word, prev, target, star }) {
  const isWin = !!target && word === target;
  const changedIdx = prev && !isWin ? window.Diddle.changedIndex(prev, word) : -1;
  return (
    <div className="row">
      <div className="row-core">
        <Tiles word={word} target={target} win={isWin} changedIdx={changedIdx} />
        {star && <span className="rare-star" title="Not in the common word list — bold pick">★</span>}
      </div>
    </div>
  );
}

function HowToScreen({ cta, onDone }) {
  const [step, setStep] = React.useState(0);
  const goal = HOWTO_LADDER[HOWTO_LADDER.length - 1];
  return (
    <div className="howto" role="dialog" aria-label="How to play">
      <div className="howto-card" style={{ "--tile": "40px", "--tile-fs": "19px" }}>
        {step === 0 ? (
          <>
            <h3>Climb the ladder</h3>
            <p>Get from the start word to the target, changing <b>one letter</b> each rung. Every rung must be a real word.</p>
            <div className="howto-ladder">
              {HOWTO_LADDER.map((w, i) => (
                <HowToRow key={w} word={w} prev={i ? HOWTO_LADDER[i - 1] : null} target={goal} />
              ))}
            </div>
            <p className="howto-note">The amber ring is the letter that changed. Tiles turn green as letters lock into the target's position.</p>
          </>
        ) : (
          <>
            <h3>Par, the clock &amp; the star</h3>
            <p><b>Par</b> is the fewest rungs possible using everyday words. Your score is your solve time plus a minute per rung — quick thinking and short ladders both pay.</p>
            <div className="howto-ladder">
              <HowToRow word="WOKS" star />
            </div>
            <p>The purple star marks a <b>rare word</b> — perfectly legal, just not on the everyday list par is measured with. A bold pick can sneak you <b>under par</b>.</p>
            <p className="howto-note">Words stay hidden until you press Play, and the clock keeps ticking even if you leave — no scouting ahead.</p>
          </>
        )}
        <div className="howto-dots">
          {[0, 1].map(i => (
            <button key={i} type="button" className={"dot" + (i === step ? " on" : "")}
                    onClick={() => setStep(i)} aria-label={`Step ${i + 1} of 2`} />
          ))}
        </div>
        {step === 0
          ? <button type="button" className="howto-btn" onClick={() => setStep(1)}>Next</button>
          : <button type="button" className="howto-btn" onClick={onDone}>{cta}</button>}
      </div>
    </div>
  );
}

Object.assign(window, {
  LoadingScreen, ErrorScreen, PlayingScreen, FinishedScreen, GaveUpScreen,
  LobbyScreen, PuzzleCard, LeaderboardScreen, ResultScreen, HowToScreen,
});
