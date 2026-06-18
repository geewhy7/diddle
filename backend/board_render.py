"""Render the live group leaderboard as a PNG via headless Chromium.

Pure presentation: `render_board_png(data)` takes the structured board dict
produced by `compute_board_data` in main.py and returns PNG bytes (or None on
any failure, so callers fall back to the text board). A single Chromium
instance is launched at startup and reused; an asyncio lock serialises renders
since pages aren't concurrency-safe.

Board data shape:
    {
      "day": 7, "date_label": "June 13th",
      "pars": {4: 7, 5: 7}, "hard_pars": {4: 12, 5: 11},
      "ranked": [ {"rank": 1, "name": "Greg", "score": "1242",  # total hybrid score
                   "holes": {4: hole, 5: hole}} , ... ],   # finished both (normal)
      "others": [ {"name": "Bob", "holes": {4: hole, 5: hole}}, ... ],  # normal
      "hard_ranked": [...], "hard_others": [...],          # the 😈 cohort
      "has_hard": True,                                    # any hard plays today
    }
where hole is one of:
    {"kind": "done", "score": int|None, "delta": int, "ta": bool}  # solved
    {"kind": "gaveup"} | {"kind": "timedout"} | {"kind": "playing"}
    {"kind": "none"}                       # not started
score = solve_seconds + 60·moves; delta (moves−optimal) + ta only drive the
pill colour / superscript / ⚡, never the ranking (which is by total score).
"""
import asyncio
import html as _html
import logging

log = logging.getLogger(__name__)

_PW = None        # async_playwright context
_BROWSER = None   # launched chromium
_LOCK = asyncio.Lock()
WORD_LENGTHS = (4, 5)


async def start_renderer() -> None:
    """Launch a persistent headless Chromium. Safe to call once at startup;
    on any failure the renderer stays disabled and callers use the text board."""
    global _PW, _BROWSER
    try:
        from playwright.async_api import async_playwright
        _PW = await async_playwright().start()
        _BROWSER = await _PW.chromium.launch(args=["--no-sandbox"])
        log.info("board renderer: chromium launched")
    except Exception as exc:
        log.warning("board renderer unavailable (text fallback): %s", exc)
        _PW = _BROWSER = None


async def stop_renderer() -> None:
    global _PW, _BROWSER
    try:
        if _BROWSER:
            await _BROWSER.close()
        if _PW:
            await _PW.stop()
    except Exception:
        pass
    _PW = _BROWSER = None


def renderer_ready() -> bool:
    return _BROWSER is not None


def _esc(s) -> str:
    return _html.escape(str(s))


def _delta_tag(d: int) -> str:
    return "P" if d == 0 else (f"+{d}" if d > 0 else f"−{-d}")   # P / +N / −N


def _delta_cls(d: int) -> str:
    return "c-un" if d < 0 else "c-par" if d == 0 else "c1" if d == 1 else "c2" if d == 2 else "c3"


def _pill(hole: dict) -> str:
    """The colored score pill (no ⚡ — the bolt lives in its own slot so scores
    stay aligned). 'done' = solved: integer hybrid score + a small par-delta
    superscript, tinted by delta (par green → red over, teal under)."""
    kind = hole["kind"]
    if kind == "none":
        return '<span class="hole none">·</span>'
    if kind == "playing":
        return '<span class="pill playing">playing</span>'
    if kind == "gaveup":
        return '<span class="pill fail">💀</span>'
    if kind == "timedout":
        return '<span class="pill fail">⌛</span>'
    d = hole["delta"]
    score = hole.get("score")
    score_txt = _esc(score) if score is not None else "—"
    return f'<span class="pill {_delta_cls(d)}">{score_txt}<sup>{_delta_tag(d)}</sup></span>'


def _hole_cell(hole: dict) -> str:
    # ⚡ sits in a fixed-width slot beside the pill (always reserved) so the
    # pills/scores line up column-to-column whether or not a bolt was earned.
    bolt = "⚡" if (hole.get("kind") == "done" and hole.get("ta")) else ""
    return (f'<div class="c-hole"><span class="cellwrap">{_pill(hole)}'
            f'<span class="bolt-slot">{bolt}</span></span></div>')


def _rank_badge(rank: int) -> str:
    cls = {1: "g", 2: "s", 3: "b"}.get(rank, "n")
    return f'<span class="rank {cls}">{rank}</span>'


def _row(rank_html: str, name: str, holes: dict, score_html: str, muted: bool) -> str:
    cells = "".join(_hole_cell(holes[l]) for l in WORD_LENGTHS)
    return (
        f'<div class="row{" muted" if muted else ""}">'
        f'<div class="c-rank">{rank_html}</div>'
        f'<div class="c-name">{_esc(name)}</div>'
        f'{cells}'
        f'<div class="c-time">{score_html}</div>'
        f'</div>'
    )


_TH = (
    '<div class="th">'
    '<div class="c-rank"></div>'
    '<div class="c-name">PLAYER</div>'
    f'<div class="c-hole">{WORD_LENGTHS[0]} LETTER</div>'
    f'<div class="c-hole">{WORD_LENGTHS[1]} LETTER</div>'
    '<div class="c-time">SCORE</div>'
    '</div>'
)


def _par_sub(pars: dict) -> str:
    return " · ".join(f"{l}-letter par {pars[l]}" for l in WORD_LENGTHS
                      if pars.get(l) is not None)


def _table(ranked: list, others: list, empty_msg: str | None) -> str:
    rows = [_TH]
    for p in ranked:
        rows.append(_row(_rank_badge(p["rank"]), p["name"], p["holes"],
                         _esc(p["score"]), muted=False))
    if others:
        rows.append('<div class="divider"></div>')
        for p in others:
            rows.append(_row("", p["name"], p["holes"], "—", muted=True))
    if not ranked and not others and empty_msg:
        rows.append(f'<div class="empty">{empty_msg}</div>')
    return "".join(rows)


def _build_html(data: dict) -> str:
    day        = data["day"]
    date_label = _esc(data["date_label"])

    blocks = [
        f'<div class="sub">{_par_sub(data["pars"])}</div>',
        _table(data["ranked"], data.get("others", []),
               "No one has played yet — be first!"),
    ]

    # The 😈 hard cohort gets its own stacked section, shown only when someone
    # has played a hard puzzle today.
    if data.get("has_hard"):
        hsub = _par_sub(data.get("hard_pars", {}))
        blocks.append('<div class="section">😈 HARD MODE</div>')
        if hsub:
            blocks.append(f'<div class="sub hsub">{hsub}</div>')
        blocks.append(_table(data.get("hard_ranked", []),
                             data.get("hard_others", []), None))

    body = "".join(blocks)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
html,body {{ background:#0a0e13; }}
body {{ padding:18px; font-family:-apple-system,'Segoe UI',Roboto,'DejaVu Sans',sans-serif;
       -webkit-font-smoothing:antialiased; width:760px; }}
#card {{ background:linear-gradient(155deg,#18222e,#0f1620); border:1px solid rgba(255,255,255,.06);
        border-radius:18px; padding:22px 26px 12px; }}
.hd {{ font-size:23px; font-weight:800; letter-spacing:.5px; color:#f2f5f8; }}
.hd .num {{ color:#7e8b99; margin-left:6px; }}
.hd .date {{ color:#7e8b99; font-weight:600; font-size:15px; margin-left:10px; }}
.sub {{ margin-top:5px; font-size:13px; color:#6b7785; font-weight:500; }}
.th, .row {{ display:grid; grid-template-columns:38px 1fr 104px 104px 70px;
            align-items:center; column-gap:10px; }}
.th {{ margin-top:20px; padding:0 0 9px; font-size:11px; letter-spacing:1px;
      color:#5f6b78; font-weight:700; border-bottom:1px solid rgba(255,255,255,.07); }}
.row {{ padding:11px 0; border-bottom:1px solid rgba(255,255,255,.05); }}
.row:last-child {{ border-bottom:none; }}
.row.muted {{ opacity:.55; }}
.c-name {{ color:#e8edf2; font-size:16px; font-weight:600; }}
.c-hole {{ text-align:center; }}
.th .c-hole, .th .c-time {{ text-align:center; }}
.th .c-time {{ text-align:right; }}
.c-time {{ text-align:right; color:#8a96a3; font-size:14px; font-variant-numeric:tabular-nums; }}
.rank {{ display:inline-flex; align-items:center; justify-content:center;
        width:26px; height:26px; border-radius:50%; font-size:13px; font-weight:800; }}
.rank.g {{ background:linear-gradient(135deg,#f7d56b,#e0a526); color:#4a3500; }}
.rank.s {{ background:linear-gradient(135deg,#dde3e9,#aab4bf); color:#34404c; }}
.rank.b {{ background:linear-gradient(135deg,#e6a86b,#bd7838); color:#4a2c12; }}
.rank.n {{ color:#7e8b99; font-weight:700; font-size:14px; }}
.cellwrap {{ display:inline-flex; align-items:center; justify-content:center; }}
.bolt-slot {{ display:inline-flex; align-items:center; justify-content:flex-start;
             width:15px; margin-left:4px; font-size:12px; color:#f5d142; }}
.pill {{ display:inline-block; min-width:58px; padding:5px 11px; border-radius:9px;
        color:#fff; font-size:15px; font-weight:800; text-align:center;
        font-variant-numeric:tabular-nums; }}
.pill sup {{ font-size:9.5px; font-weight:800; opacity:.92; margin-left:1px; }}
.pill.c-par {{ background:linear-gradient(135deg,#43b06a,#2f8d4e); }}
.pill.c1 {{ background:linear-gradient(135deg,#e3b24a,#cf9322); }}
.pill.c2 {{ background:linear-gradient(135deg,#e08a3c,#cf6f22); }}
.pill.c3 {{ background:linear-gradient(135deg,#cf4f47,#b23a33); }}
.pill.c-un {{ background:linear-gradient(135deg,#46c79a,#2f9d78); }}
.pill.fail {{ background:#283139; color:#aeb8c2; min-width:44px; font-weight:700; }}
.pill.playing {{ background:transparent; border:1.5px solid rgba(255,255,255,.16);
                color:#97a2ae; font-size:11px; font-weight:600; min-width:44px; }}
.hole.none {{ color:#48535f; font-size:18px; }}
.divider {{ height:1px; background:rgba(255,255,255,.07); margin:4px 0; }}
.empty {{ padding:22px 0 14px; text-align:center; color:#6b7785; font-size:14px; }}
.section {{ margin-top:24px; padding-top:16px; border-top:1px solid rgba(207,79,71,.35);
           font-size:15px; font-weight:800; letter-spacing:1px; color:#e0726b; }}
.sub.hsub {{ color:#b5736e; }}
</style></head><body><div id="card">
<div class="hd">DIDDLE<span class="num">#{day}</span><span class="date">{date_label}</span></div>
{body}
</div></body></html>"""


async def render_board_png(data: dict) -> bytes | None:
    if _BROWSER is None:
        return None
    html_str = _build_html(data)
    try:
        async with _LOCK:
            page = await _BROWSER.new_page(
                viewport={"width": 760, "height": 200}, device_scale_factor=2,
            )
            try:
                await page.set_content(html_str, wait_until="load")
                try:
                    await page.evaluate("document.fonts.ready")
                except Exception:
                    pass
                png = await page.screenshot(full_page=True, type="png")
            finally:
                await page.close()
        return png
    except Exception as exc:
        log.warning("render_board_png failed: %s", exc)
        return None
