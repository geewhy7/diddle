/* Diddle — shared presentational components. Exported to window for other
   babel scripts (screens.jsx, app.jsx) to consume. */
const { useState, useEffect, useRef } = React;

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

// The delta-colored score pill (integer score + par-delta superscript, ⚡ if a
// clean time-attack solve). Used on finish / result / lobby card.
function ScorePill({ score, delta, clean = false, size = 'lg' }) {
  return (
    <span className={`score-pill ${deltaClass(delta)} ${size}`}>
      <span className="sp-num">{score == null ? '—' : score}</span>
      <sup className="sp-tag">{deltaTag(delta)}</sup>
      {clean && <span className="sp-bolt">⚡</span>}
    </span>
  );
}

// compact replay used on finished / gave-up / result screens
function Replay({ title, path, target, optimal = false }) {
  return (
    <div className={"replay" + (optimal ? " optimal" : "")}>
      <h4>{title}</h4>
      {path.map((w, i) => {
        const isWin = !optimal && w === target;
        const prev = i > 0 ? path[i - 1] : null;
        const changedIdx = (prev && !isWin)
          ? window.Diddle.changedIndex(prev, w)
          : -1;
        return (
          <div className="row" key={i}>
            <div className="gutter">{i}</div>
            <Tiles word={w} target={target} win={isWin} changedIdx={changedIdx} />
          </div>
        );
      })}
    </div>
  );
}

Object.assign(window, { Mark, Wordmark, Tiles, ChainRow, Replay, Keyboard, Confetti, DiddleSound,
                        ScorePill, holeScore, deltaTag, deltaClass });
