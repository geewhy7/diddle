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

  // Same BFS as shortestPath, but `start` need not already be a node in `adj`
  // — its edges are derived on the fly against `poolWords` instead of
  // mutating the shared graph. Used to auto-solve a give-up from wherever the
  // player actually stopped, which may be a legal-but-rare word that never
  // made it into the narrower pool the graph was built from.
  function shortestPathFromNode(start, target, adj, poolWords) {
    if (start === target) return [start];
    const startNeighbors = adj.has(start) ? adj.get(start) : poolWords.filter(w => oneAway(start, w));
    const prev = new Map([[start, null]]);
    const q = [start];
    while (q.length) {
      const cur = q.shift();
      const neighbors = cur === start ? startNeighbors : (adj.get(cur) || []);
      for (const nb of neighbors) {
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

  // ---- API loading ---------------------------------------------------------

  async function loadWordSet(url, wordLength) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Words fetch failed (${res.status})`);
    const text = await res.text();
    return new Set(text.trim().split('\n')
      .map(w => w.trim().toUpperCase())
      .filter(w => w.length === wordLength && /^[A-Z]+$/.test(w)));
  }

  async function loadFromAPI(wordLength = 5) {
    const pzRes = await fetch(`/puzzle?length=${wordLength}`);
    if (!pzRes.ok) throw new Error(`Puzzle fetch failed (${pzRes.status})`);
    const { start, end, optimal_steps, optimal_path, day, word_length,
            time_limit, hard_available, hard } = await pzRes.json();

    const [dictSet, commonSet, hardCommonSet] = await Promise.all([
      loadWordSet(`/words?length=${word_length}`, word_length),
      loadWordSet(`/words/pool?length=${word_length}&hard=false`, word_length),
      hard_available
        ? loadWordSet(`/words/pool?length=${word_length}&hard=true`, word_length)
        : Promise.resolve(null),
    ]);
    const words = [...dictSet];

    // Word graph + dictionary are shared by both variants (validation uses the
    // full word set either way) — only start/target/par differ.
    const adj  = buildAdj(words);
    const dict = dictSet;

    // Build a playable puzzle object for one variant of this length.
    const makeVariant = (s, e, par, hardMode, serverPath, commonWords) => {
      const startUC  = s.toUpperCase();
      const targetUC = e.toUpperCase();
      // Prefer the server's canonical par path (common-word pool, matches the
      // par number shown on "give up"). Only BFS the full graph as a fallback,
      // which can route through obscure rarer words.
      const optimalPath = (Array.isArray(serverPath) && serverPath.length > 1)
        ? serverPath.map(w => w.toUpperCase())
        : (shortestPath(startUC, targetUC, adj) || [startUC, targetUC]);
      // True shortest path over the full validation graph — can be shorter than
      // `optimalPath` (which is measured over the common-word pool) when a rarer
      // valid word offers a golf shortcut. Powers the Par/Best ladder toggle.
      const bestPath = shortestPath(startUC, targetUC, adj) || optimalPath;
      // The narrower "common" pool (same one par is measured against) — used
      // to auto-solve a give-up so the reveal reads as an "oh, duh" common
      // word rather than an obscure shortcut through the wide validation dict.
      const poolWords = [...commonWords];
      if (!commonWords.has(startUC))  poolWords.push(startUC);
      if (!commonWords.has(targetUC)) poolWords.push(targetUC);
      const commonAdj = buildAdj(poolWords);
      return {
        id:     `day-${day}-${hardMode ? 'h' : 'n'}`,
        num:    day,
        length: word_length,
        start:  startUC,
        target: targetUC,
        par,
        hardMode:    !!hardMode,
        isChallenge: !!hardMode,      // hard variant wears the 😈 treatment
        timeLimit: time_limit || 0,   // per-guess shot clock seconds; 0 = off
        optimalPath,
        bestPath,
        dict,
        commonWords,
        adj,
        bestFromHere(word) {
          return distance(word.toUpperCase(), targetUC, adj);
        },
        // Quickest completion from `word` to the target, over the narrower
        // common pool (falls back to the full validation graph if the pool
        // graph can't reach it from here). Returns null if already solved.
        finishFromHere(word) {
          const w = (word || '').toUpperCase();
          if (w === targetUC) return null;
          return shortestPathFromNode(w, targetUC, commonAdj, poolWords)
              || shortestPath(w, targetUC, adj);
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
    };

    const normal = makeVariant(start, end, optimal_steps, false, optimal_path, commonSet);
    // The hard variant (same length) — null if today has no qualifying pair.
    normal.hardVariant = (hard_available && hard)
      ? makeVariant(hard.start, hard.end, hard.optimal_steps, true, hard.optimal_path, hardCommonSet)
      : null;
    return normal;
  }

  window.Diddle = { loadFromAPI, oneAway, changedIndex, shortestPath, distance };
})();
