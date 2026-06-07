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
function Tiles({ word, target, dim = false, win = false, len, variant, flip = false, entry = false, activeIndex = -1 }) {
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
    const glyph = ch === undefined ? (entry ? "" : "·") : ch;
    cells.push(
      <span key={i} className={cls.join(" ")} style={flip ? { "--i": i } : undefined}>{glyph}</span>
    );
  }
  return <div className={"tiles" + (variant ? " " + variant : "")}>{cells}</div>;
}

// a committed row in the history stack (rung-node number + centred tiles)
function ChainRow({ word, target, step, isWin, promote }) {
  const cls = ["row"];
  if (promote) cls.push("promote");
  return (
    <div className={cls.join(" ")}>
      <div className="gutter">{step}</div>
      <Tiles word={word} target={target} win={isWin} />
    </div>
  );
}

// compact replay used on finished / gave-up screens
function Replay({ title, path, target, optimal = false }) {
  return (
    <div className={"replay" + (optimal ? " optimal" : "")}>
      <h4>{title}</h4>
      {path.map((w, i) => {
        const isWin = !optimal && w === target;
        return (
          <div className="row" key={i}>
            <div className="gutter">{i}</div>
            <Tiles word={w} target={target} win={isWin} />
          </div>
        );
      })}
    </div>
  );
}

Object.assign(window, { Mark, Wordmark, Tiles, ChainRow, Replay });
