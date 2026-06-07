/* Diddle — word-ladder engine (production).
   Fetches puzzle + word list from the API; builds the adjacency graph
   client-side so PlayingScreen can compute "best from here" and GaveUpScreen
   can show the optimal path — both require live BFS over the real word graph.

   Pattern-grouping adjacency (O(n×L)) replaces the naive O(n²) approach so
   building a ~2,000-word graph takes < 10 ms instead of several seconds. */
(function () {

  // ---- BFS utilities -------------------------------------------------------

  function changedIndex(a, b) {
    if (!a || !b || a.length !== b.length) return -1;
    let idx = -1;
    for (let i = 0; i < a.length; i++) {
      if (a[i] !== b[i]) { if (idx !== -1) return -1; idx = i; }
    }
    return idx;
  }

  function oneAway(a, b) {
    return a.length === b.length && changedIndex(a, b) !== -1;
  }

  // O(n×L): group words by wildcard patterns, link words that share one.
  function buildAdj(words) {
    const pmap = new Map();
    for (const w of words) {
      for (let i = 0; i < w.length; i++) {
        const key = w.slice(0, i) + '*' + w.slice(i + 1);
        if (!pmap.has(key)) pmap.set(key, []);
        pmap.get(key).push(w);
      }
    }
    const adj = new Map();
    for (const w of words) adj.set(w, []);
    for (const group of pmap.values()) {
      for (let i = 0; i < group.length; i++) {
        for (let j = i + 1; j < group.length; j++) {
          adj.get(group[i]).push(group[j]);
          adj.get(group[j]).push(group[i]);
        }
      }
    }
    return adj;
  }

  function shortestPath(start, target, adj) {
    if (start === target) return [start];
    if (!adj.has(start) || !adj.has(target)) return null;
    const prev = new Map([[start, null]]);
    const q = [start];
    while (q.length) {
      const cur = q.shift();
      for (const nb of adj.get(cur)) {
        if (!prev.has(nb)) {
          prev.set(nb, cur);
          if (nb === target) {
            const path = [];
            let c = nb;
            while (c !== null) { path.unshift(c); c = prev.get(c); }
            return path;
          }
          q.push(nb);
        }
      }
    }
    return null;
  }

  function distance(start, target, adj) {
    const p = shortestPath(start, target, adj);
    return p ? p.length - 1 : Infinity;
  }

  // ---- API loading ---------------------------------------------------------

  async function loadFromAPI() {
    const [pzRes, wRes] = await Promise.all([
      fetch('/puzzle'),
      fetch('/words'),
    ]);
    if (!pzRes.ok) throw new Error(`Puzzle fetch failed (${pzRes.status})`);
    if (!wRes.ok)  throw new Error(`Words fetch failed (${wRes.status})`);

    const { start, end, optimal_steps, day, word_length } = await pzRes.json();
    const wordText = await wRes.text();

    const words = wordText.trim().split('\n')
      .map(w => w.trim().toUpperCase())
      .filter(w => w.length === word_length && /^[A-Z]+$/.test(w));

    const adj       = buildAdj(words);
    const startUC   = start.toUpperCase();
    const targetUC  = end.toUpperCase();
    const optimalPath = shortestPath(startUC, targetUC, adj) || [startUC, targetUC];

    return {
      id:     `day-${day}`,
      num:    day,
      length: word_length,
      start:  startUC,
      target: targetUC,
      par:    optimal_steps,
      optimalPath,
      dict: new Set(words),
      adj,
      bestFromHere(word) {
        return distance(word.toUpperCase(), targetUC, adj);
      },
      validate(prev, guess) {
        const g = (guess || '').toUpperCase().trim();
        if (g.length !== prev.length) return { ok: false, reason: `Needs ${prev.length} letters` };
        if (!/^[A-Z]+$/.test(g))      return { ok: false, reason: 'Letters only' };
        if (g === prev)                return { ok: false, reason: 'Change one letter' };
        const ci = changedIndex(prev, g);
        if (ci === -1)                 return { ok: false, reason: 'Change exactly one letter' };
        if (!this.dict.has(g))         return { ok: false, reason: 'Not in word list' };
        return { ok: true, word: g, changed: ci };
      },
    };
  }

  window.Diddle = { loadFromAPI, oneAway, changedIndex, shortestPath, distance };
})();
