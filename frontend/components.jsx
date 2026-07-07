/* Diddle — shared presentational components. Exported to window for other
   babel scripts (screens.jsx, app.jsx) to consume. */
const { useState, useEffect, useRef, useMemo } = React;

// tiny logo mark: a 2x2 of "tiles" with one accent cell
function Mark() {
  return (
    <div className="mark" aria-hidden="true">
      <i></i><i className="on"></i><i className="on"></i><i></i>
    </div>
  );
}

function Wordmark() {
  return <div className="wordmark">diddle<span className="dot">.</span></div>;
}

// one word rendered as monospace tiles. Tiles whose letter already matches the
// GOAL at that position get `.hit` (green) — progress toward the target.
// `win` paints the whole row green (the word equals the target).
// `changedIdx` marks the single letter that changed from the previous word.
function Tiles({ word, target, dim = false, win = false, len, variant, flip = false, entry = false, activeIndex = -1, changedIdx = -1 }) {
  const letters = (word || "").split("");
  const total = len || letters.length;
  const cells = [];
  for (let i = 0; i < total; i++) {
    const ch = letters[i];
    const cls = ["tile"];
    if (ch === undefined) cls.push(entry ? "slot" : "empty");
    if (dim) cls.push("dim");
    if (target && ch !== undefined && ch === target[i]) cls.push("hit");
    if (win) cls.push("win");
    if (flip) cls.push("flip");
    if (i === activeIndex) cls.push("active");
    if (i === changedIdx && ch !== undefined && !win) cls.push("changed");
    const glyph = ch === undefined ? (entry ? "" : "·") : ch;
    cells.push(
      <span key={i} className={cls.join(" ")} style={flip ? { "--i": i } : undefined}>{glyph}</span>
    );
  }
  return <div className={"tiles" + (variant ? " " + variant : "")}>{cells}</div>;
}

// a committed row in the history stack (rung-node number + centred tiles)
function ChainRow({ word, target, step, isWin, promote, prev }) {
  const changedIdx = (prev && !isWin)
    ? window.Diddle.changedIndex(prev, word)
    : -1;
  const cls = ["row"];
  if (promote) cls.push("promote");
  if (isWin) cls.push("winrow");
  return (
    <div className={cls.join(" ")}>
      <div className="gutter">{step}</div>
      <Tiles word={word} target={target} win={isWin} changedIdx={changedIdx} />
    </div>
  );
}

// win celebration: a confetti cannon. Pieces launch upward from around the
// winning row, arc under gravity (rise/fall keyframes), drift, spin, fade.
// Perfect-par wins mix 🎯 emoji in with the confetti.
const CONFETTI_COLORS = ["#1ba35e", "#3ddc84", "#2f8fda", "#f5b942", "#e0564f", "#9b6ef3"];

function Confetti({ perfect = false, count = 80 }) {
  const pieces = React.useMemo(() => Array.from({ length: count }, (_, i) => ({
    left:  6 + Math.random() * 88,                                  // launch x (%)
    up:    -(140 + Math.random() * 360),                            // rise (px)
    fall:  420 + Math.random() * 400,                               // fall past origin (px)
    drift: (Math.random() - 0.5) * 260,                             // horizontal drift (px)
    dur:   1.1 + Math.random() * 0.7,
    delay: Math.random() * 0.18,
    rot:   (Math.random() < 0.5 ? -1 : 1) * (240 + Math.random() * 540),
    emoji: perfect && i % 9 === 0 ? "🎯" : null,
    color: CONFETTI_COLORS[i % CONFETTI_COLORS.length],
    round: Math.random() < 0.25,
  })), []);
  return (
    <div className="confetti" aria-hidden="true">
      {pieces.map((p, i) => (
        <span key={i} className="cf-x"
              style={{ left: p.left + "%", "--drift": p.drift + "px",
                       "--dur": p.dur + "s", "--delay": p.delay + "s" }}>
          <span className="cf-y" style={{ "--up": p.up + "px", "--fall": p.fall + "px" }}>
            {p.emoji
              ? <span className="cf-r emoji" style={{ "--rot": p.rot + "deg" }}>{p.emoji}</span>
              : <span className={"cf-r" + (p.round ? " round" : "")}
                      style={{ "--rot": p.rot + "deg", background: p.color }} />}
          </span>
        </span>
      ))}
    </div>
  );
}

// shot-clock audio: tiny WebAudio oscillator beeps. iOS only lets an
// AudioContext start inside a user gesture, so unlock() is called from
// keyboard taps and card taps — both guaranteed before any beep is needed.
const _snd = { ctx: null };
function _sndUnlock() {
  try {
    if (!_snd.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) _snd.ctx = new AC();
    }
    if (_snd.ctx && _snd.ctx.state === "suspended") _snd.ctx.resume();
  } catch (_) {}
}
function _sndTone(freq, dur, vol) {
  const ctx = _snd.ctx;
  if (!ctx || ctx.state !== "running") return;
  try {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "square";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(vol, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + dur);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + dur);
  } catch (_) {}
}
const DiddleSound = {
  unlock: _sndUnlock,
  tick:   () => _sndTone(1320, 0.09, 0.06),   // last-seconds beep
  buzz:   () => _sndTone(196,  0.45, 0.10),   // time's-up buzzer
};

// on-screen QWERTY keyboard — replaces the OS keyboard so the layout never
// shifts. ENTER lights up (accent color) once the input reaches full length.
const KB_ROWS = [
  ["Q","W","E","R","T","Y","U","I","O","P"],
  ["A","S","D","F","G","H","J","K","L"],
  ["ENTER","Z","X","C","V","B","N","M","BACK"],
];

function Keyboard({ onKey, disabled, enterReady }) {
  const press = (k) => (e) => {
    e.preventDefault();
    if (disabled) return;
    DiddleSound.unlock();
    window.Telegram?.WebApp?.HapticFeedback?.impactOccurred("light");
    onKey(k);
  };
  return (
    <div className={"kb" + (disabled ? " disabled" : "")}>
      {KB_ROWS.map((row, ri) => (
        <div className="kb-row" key={ri}>
          {row.map((k) => (
            <button
              key={k}
              type="button"
              tabIndex={-1}
              className={"kb-key"
                + (k === "ENTER" ? " wide enter" + (enterReady ? " ready" : "") : "")
                + (k === "BACK" ? " wide" : "")}
              onPointerDown={press(k)}
              aria-label={k === "BACK" ? "Delete letter" : k === "ENTER" ? "Submit word" : k}
            >
              {k === "BACK" ? "⌫" : k}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

// ---- Hybrid score display helpers ------------------------------------------
// Score = solve seconds + 60 per ladder move (lower wins). Par (optimal) is
// shown only as a small delta tag + pill colour, never the ranking.
function holeScore(solveSeconds, moves) {
  return (solveSeconds == null) ? null : solveSeconds + moves * 60;
}
function deltaTag(d) { return d === 0 ? 'P' : (d > 0 ? `+${d}` : `−${-d}`); }   // P / +N / −N
function deltaClass(d) {
  return d < 0 ? 'sc-un' : d === 0 ? 'sc-par' : d === 1 ? 'sc-1' : d === 2 ? 'sc-2' : 'sc-3';
}
// Format a hybrid score (second-units) as a minimal clock: M:SS, rolling to
// H:MM:SS past an hour. The leading unit isn't zero-padded (6:24, 1:12:52);
// trailing units are. null → em dash.
function fmtScore(score) {
  if (score == null) return '—';
  const h = Math.floor(score / 3600);
  const m = Math.floor((score % 3600) / 60);
  const s = score % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`;
}

// The delta-colored score pill (integer score + par-delta superscript, ⚡ if a
// clean time-attack solve). Used on finish / result / lobby card.
function ScorePill({ score, delta, clean = false, size = 'lg' }) {
  return (
    <span className={`score-pill ${deltaClass(delta)} ${size}`}>
      <span className="sp-num">{fmtScore(score)}</span>
      <sup className="sp-tag">{deltaTag(delta)}</sup>
      {clean && <span className="sp-bolt">⚡</span>}
    </span>
  );
}

// compact replay used on finished / gave-up / result screens. `commonWords`
// (the puzzle's par/selection pool) is optional — when given, any played word
// (not the fixed start) outside that pool but still legal gets a little star:
// a rare, bold pick only reachable via the wider validation list. `finish` is
// an optional continuation path (starting from the same word `path` ends on)
// — when given (a give-up with at least one move made), a divider + the
// quickest solve from there renders below, so the player can go "oh, duh".
function Replay({ title, path, target, optimal = false, commonWords, finish }) {
  const showFinish = finish && finish.length > 1;
  return (
    <div className={"replay" + (optimal ? " optimal" : "")}>
      <h4>{title}</h4>
      {path.map((w, i) => {
        const isWin = !optimal && w === target;
        const prev = i > 0 ? path[i - 1] : null;
        const changedIdx = (prev && !isWin)
          ? window.Diddle.changedIndex(prev, w)
          : -1;
        const rare = commonWords && i > 0 && !commonWords.has(w);
        return (
          <div className="row" key={i}>
            <div className="row-core">
              <div className="gutter">{i}</div>
              <Tiles word={w} target={target} win={isWin} changedIdx={changedIdx} />
              {rare && <span className="rare-star" title="Not in the common word list — bold pick">★</span>}
            </div>
          </div>
        );
      })}
      {showFinish && (
        <>
          <div className="replay-divider" />
          <div className="replay-continued">
            <div className="replay-hint-label">quickest finish from here</div>
            {finish.slice(1).map((w, i) => {
              const isWin = w === target;
              const prev = i === 0 ? finish[0] : finish[i];
              const changedIdx = !isWin ? window.Diddle.changedIndex(prev, w) : -1;
              return (
                <div className="row" key={"f" + i}>
                  <div className="row-core">
                    <div className="gutter">{path.length + i}</div>
                    <Tiles word={w} target={target} win={isWin} changedIdx={changedIdx} />
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

// One ladder view with a two-tier picker instead of a flat pill row: Par/Best
// side by side up top (Best omitted when it equals Par — nothing to toggle),
// then one full-width row per player — You, then every connected player who
// finished this variant today. Selecting a row swaps the single Replay below.
// Selected = a lit pill (solid fill + glow); everything else reads as "off"
// but stays legible. `Par` is the server's common-word optimal (what
// score/delta is measured against); `Best` is the true shortest path over the
// full validation graph — only differs from Par when a rarer valid word
// offers a golf shortcut.
function LadderPicker({ puzzle, path, gaveUp, peers, peersLoading, soloSeconds }) {
  const parPath   = puzzle.optimalPath;
  const bestPath  = puzzle.bestPath || parPath;
  const parSteps  = parPath.length - 1;
  const bestSteps = bestPath.length - 1;
  const bestDiffers = bestSteps < parSteps;

  const routeOptions = [{ key: 'par', label: 'Par', path: parPath, steps: parSteps, optimal: true }];
  if (bestDiffers) {
    routeOptions.push({ key: 'best', label: 'Best', path: bestPath, steps: bestSteps, optimal: true });
  }

  const userOptions = [{
    key: 'you', label: 'You', path, steps: path.length - 1,
    gaveUp: !!gaveUp, solveSeconds: gaveUp ? null : soloSeconds,
  }];
  for (const p of (peers || [])) {
    const pPath = (p.path && p.path.length > 1) ? p.path.map(w => w.toUpperCase()) : [puzzle.start];
    userOptions.push({
      key: `p${p.user_id}`, label: p.name, path: pPath, steps: p.moves,
      gaveUp: !!p.gave_up, timedOut: !!p.timed_out,
      solveSeconds: p.gave_up ? null : p.solve_seconds,
    });
  }

  const [sel, setSel] = useState('you');
  const active = [...routeOptions, ...userOptions].find(o => o.key === sel) || userOptions[0];
  const pronoun = active.key === 'you' ? 'you' : 'they';

  // Give-ups with at least one move get a bonus: the quickest completion from
  // the word they left off on, over the puzzle's common pool.
  const finishPath = useMemo(() => {
    if (!active.gaveUp || !active.path || active.path.length <= 1) return null;
    if (typeof puzzle.finishFromHere !== 'function') return null;
    return puzzle.finishFromHere(active.path[active.path.length - 1]);
  }, [active.key, active.path, puzzle]);

  const routePill = (o) => (
    <button key={o.key} className={"lp-pill lp-route" + (o.key === sel ? " on" : " off")} onClick={() => setSel(o.key)}>
      <span className="lp-route-label">{o.label}</span>
      <span className="lp-route-moves">{o.steps} {o.steps === 1 ? 'move' : 'moves'}</span>
    </button>
  );
  const userRow = (o) => {
    const time = !o.gaveUp ? fmtTime(o.solveSeconds) : null;
    return (
      <button key={o.key} className={"lp-pill lp-user" + (o.key === sel ? " on" : " off")} onClick={() => setSel(o.key)}>
        <span className="lp-dot" aria-hidden="true" />
        <span className="lp-name">{o.label}</span>
        {!o.gaveUp && (
          <span className="lp-meta">
            {time && <span className="lp-time">{time}</span>}
            <span className="lp-moves"><span className="lp-moves-num">{o.steps}</span> {o.steps === 1 ? 'move' : 'moves'}</span>
          </span>
        )}
      </button>
    );
  };

  return (
    <div className="ladder-picker">
      <div className="lp-routes">{routeOptions.map(routePill)}</div>
      <div className="lp-users">{userOptions.map(userRow)}</div>
      {peersLoading && <div className="lp-loading">finding your people…</div>}
      {active.gaveUp && active.key !== 'you' && <GaveUpStamp compact={true} />}
      <Replay
        title={active.gaveUp ? `how far ${pronoun} got` : `${active.steps} ${active.steps === 1 ? 'move' : 'moves'}`}
        path={active.path}
        target={puzzle.target}
        optimal={!!active.optimal}
        commonWords={puzzle.commonWords}
        finish={finishPath}
      />
    </div>
  );
}

// give-up treatment: a skull over a "GAVE UP" callout, one letter-span per
// character so the CSS can give each one its own little wobble.
function BloodyText({ text }) {
  return (
    <div className="bloody-text" aria-label={text}>
      {text.split('').map((ch, i) => (
        <span key={i} className="bl-ch" style={{ '--i': i }}>{ch === ' ' ? ' ' : ch}</span>
      ))}
    </div>
  );
}

function GaveUpStamp({ compact = false }) {
  return (
    <div className={"gave-up-stamp" + (compact ? " compact" : "")}>
      <div className="gave-up-skull">💀</div>
      <BloodyText text="GAVE UP" />
    </div>
  );
}

Object.assign(window, { Mark, Wordmark, Tiles, ChainRow, Replay, Keyboard, Confetti, DiddleSound,
                        ScorePill, holeScore, fmtScore, deltaTag, deltaClass,
                        LadderPicker, BloodyText, GaveUpStamp });
