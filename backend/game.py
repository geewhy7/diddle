#!/usr/bin/env python3
"""
Word Ladder engine — imported by main.py. Functions:
  load_words(length)       — fetch + frequency-filter word list
  build_graph(words)       — pattern-grouped adjacency graph O(n×L)
  largest_component(graph) — keep only the main connected blob
  bfs(graph, start, end)   — shortest path
  pick_puzzle(words, graph) — daily deterministic puzzle (date-seeded)
  validate(current, guess, words) — move validation

Source: word_ladder.py (CLI game). Do not restructure this file.
"""

import sys
import random
import urllib.request
from collections import defaultdict, deque
from datetime import date


WORD_LIST_URL   = "https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt"
WORDLE_LIST_URL = "https://raw.githubusercontent.com/tabatkins/wordle-list/main/words"

# ANSI colours — used by the CLI play() function below
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def c(colour: str, text: str) -> str:
    return f"{colour}{text}{RESET}"


# ── Word list ──────────────────────────────────────────────────────────────────

def load_words(length: int) -> set[str]:
    """
    Return a curated set of common N-letter English words.

    Filtering strategy:
      • 5-letter: dwyl base list  ∩  top-50k frequency  ∩  Wordle valid guesses
                  (Wordle list is hand-curated for exactly this use case)
      • 4-letter: dwyl base list  ∩  top-30k frequency
      • other  :  dwyl base list  ∩  top-50k frequency

    This eliminates archaic/specialist words (PALP, PAGA, PALA, NAGA…)
    while keeping real but uncommon words (FJORD, NYMPH, TRYST, ABYSS…).
    """
    try:
        from wordfreq import top_n_list
    except ImportError:
        raise SystemExit(
            "wordfreq not found. Install it with:  pip install wordfreq"
        )

    print(f"  Fetching word lists...", end="", flush=True)

    with urllib.request.urlopen(WORD_LIST_URL, timeout=10) as resp:
        raw = resp.read().decode()

    base = {
        w.strip().lower()
        for w in raw.splitlines()
        if len(w.strip()) == length and w.strip().isalpha()
    }

    ENABLE_URL = "https://raw.githubusercontent.com/dolph/dictionary/master/enable1.txt"

    if length == 5:
        freq_set = set(top_n_list("en", 50_000))
        with urllib.request.urlopen(WORDLE_LIST_URL, timeout=10) as resp:
            wordle = set(resp.read().decode().split())
        words = base & wordle
    elif length == 4:
        with urllib.request.urlopen(ENABLE_URL, timeout=10) as r:
            enable = {w.strip().lower() for w in r.read().decode().splitlines() 
                    if len(w.strip()) == 4 and w.strip().isalpha()}
        freq = set(top_n_list("en", 30_000))
        words = base & enable & freq
    else:
        freq_set = set(top_n_list("en", 50_000))
        words = base & freq_set

    print(f" {len(words):,} {length}-letter words loaded.")
    return words


# ── Graph construction ─────────────────────────────────────────────────────────

def build_graph(words: set[str]) -> dict[str, set[str]]:
    """
    Pattern-grouping approach: for each word generate wildcard patterns,
    e.g. FIRE → *IRE / F*RE / FI*E / FIR*. Words sharing a pattern are
    one-letter-change neighbours. O(n × L) vs naïve O(n²).
    """
    pattern_map: dict[str, list[str]] = defaultdict(list)
    for word in words:
        for i in range(len(word)):
            key = word[:i] + "*" + word[i + 1:]
            pattern_map[key].append(word)

    graph: dict[str, set[str]] = defaultdict(set)
    for neighbours in pattern_map.values():
        for i, w1 in enumerate(neighbours):
            for w2 in neighbours[i + 1:]:
                graph[w1].add(w2)
                graph[w2].add(w1)
    return graph


# ── Connected components ───────────────────────────────────────────────────────

def largest_component(graph: dict[str, set[str]]) -> set[str]:
    """
    Not all words are reachable from all others (JAZZ, QUIZ, etc. are islands).
    We only want words in the main connected blob so puzzles are always solvable.
    """
    visited: set[str] = set()
    best: set[str] = set()

    for seed in graph:
        if seed in visited:
            continue
        component: set[str] = set()
        q = deque([seed])
        while q:
            node = q.popleft()
            if node in component:
                continue
            component.add(node)
            q.extend(graph[node] - component)
        visited |= component
        if len(component) > len(best):
            best = component

    return best


# ── BFS shortest path ──────────────────────────────────────────────────────────

def bfs(graph: dict[str, set[str]], start: str, end: str) -> list[str] | None:
    """Return shortest path start→end, or None if unreachable."""
    if start == end:
        return [start]
    parent: dict[str, str | None] = {start: None}
    q = deque([start])
    while q:
        node = q.popleft()
        for nb in graph.get(node, set()):
            if nb in parent:
                continue
            parent[nb] = node
            if nb == end:
                path, cur = [], end
                while cur is not None:
                    path.append(cur)
                    cur = parent[cur]
                return path[::-1]
            q.append(nb)
    return None


# ── Daily puzzle selection ─────────────────────────────────────────────────────

def pick_puzzle(
    words: set[str],
    graph: dict[str, set[str]],
    min_steps: int = 4,
    max_steps: int = 7,
    seed: int | None = None,
) -> tuple[str, str, list[str]]:
    """
    Reproducible daily puzzle. Tries random pairs until one falls in the
    target difficulty range (path length in [min_steps, max_steps]).
    Same seed → same puzzle, so everyone gets the same one each day.
    """
    if seed is None:
        seed = date.today().toordinal()
    rng = random.Random(seed)
    word_list = sorted(words)   # sorted = deterministic

    for attempt in range(20_000):
        start = rng.choice(word_list)
        end   = rng.choice(word_list)
        if start == end:
            continue
        path = bfs(graph, start, end)
        if path and min_steps <= len(path) - 1 <= max_steps:
            return start, end, path

    raise RuntimeError(
        f"Couldn't find a puzzle with {min_steps}–{max_steps} steps after 20k tries. "
        "Try widening the range."
    )


# ── Validation ─────────────────────────────────────────────────────────────────

def letter_diffs(a: str, b: str) -> list[int]:
    return [i for i, (x, y) in enumerate(zip(a, b)) if x != y]

def validate(current: str, guess: str, valid: set[str]) -> str | None:
    """Return error message or None if move is legal."""
    g = guess.strip().lower()
    if len(g) != len(current):
        return f"Need exactly {len(current)} letters."
    if not g.isalpha():
        return "Letters only, no spaces or numbers."
    if g == current:
        return "That's the same word — change a letter!"
    diffs = letter_diffs(current, g)
    if len(diffs) != 1:
        return f"Change exactly one letter (you changed {len(diffs)})."
    if g not in valid:
        return f"'{g.upper()}' isn't a valid word."
    return None


# ── Display (CLI only) ─────────────────────────────────────────────────────────

W = 48

def ruler(ch="─"): print(ch * W)

def render_word_with_diff(prev: str, word: str) -> str:
    diffs = letter_diffs(prev, word)
    out = []
    for i, ch in enumerate(word.upper()):
        out.append(c(GREEN + BOLD, ch) if i in diffs else ch)
    return "".join(out)

def print_path(path: list[str]) -> None:
    for i, word in enumerate(path):
        if i == 0:
            print(f"    {word.upper()}")
        else:
            diff_str = render_word_with_diff(path[i - 1], word)
            print(f"    {c(DIM, '↓')}  {diff_str}")

def print_header(start: str, end: str, optimal: int, day: int) -> None:
    ruler("═")
    print(f"  {c(BOLD, 'WORD LADDER')}  ·  {c(DIM, f'Day {day}')}".center(W + 10))
    ruler("═")
    print(f"  Start  : {c(CYAN + BOLD, start.upper())}")
    print(f"  Target : {c(YELLOW + BOLD, end.upper())}")
    print(f"  Optimal: {optimal} step{'s' if optimal != 1 else ''}")
    ruler()

def print_result(player_path: list[str], optimal_path: list[str]) -> None:
    steps   = len(player_path) - 1
    optimal = len(optimal_path) - 1
    ruler("═")
    if steps == optimal:
        print(f"  {c(GREEN + BOLD, '🏆  Perfect!')} Shortest possible path — {steps} steps.")
    elif steps <= optimal + 1:
        print(f"  {c(GREEN, '✓  Solved!')} {steps} steps  (optimal: {optimal})")
    elif steps <= optimal + 3:
        print(f"  {c(YELLOW, '✓  Solved.')} {steps} steps  (optimal: {optimal})")
    else:
        print(f"  Solved in {steps} steps  (optimal was {optimal})")
    print()
    print(f"  {c(BOLD, 'Your path:')}")
    print_path(player_path)
    print()
    print(f"  {c(BOLD, 'Optimal path:')}")
    print_path(optimal_path)
    ruler("═")

def print_hint(graph: dict[str, set[str]], current: str, end: str, level: int) -> None:
    path = bfs(graph, current, end)
    if not path or len(path) < 2:
        print(f"  {c(DIM, 'You are one step away!')}")
        return
    next_word = path[1]
    diffs = letter_diffs(current, next_word)
    pos = diffs[0] + 1
    if level == 1:
        remaining = len(path) - 1
        plural = "s" if remaining != 1 else ""
        print(f"  {c(DIM, f'Hint: optimal path from here is {remaining} step{plural}.')}")
    elif level == 2:
        print(f"  {c(DIM, f'Hint: change letter {pos}.')}")
    else:
        print(f"  {c(DIM, f'Hint: change letter {pos} to  \"{next_word[diffs[0]].upper()}\".')}")


# ── Game loop (CLI only) ───────────────────────────────────────────────────────

def play(valid: set[str], graph: dict[str, set[str]], debug: bool = False) -> None:
    day = date.today().toordinal() % 10_000
    print(f"\n  Finding today's puzzle (seed: {date.today()})...")
    start, end, optimal_path = pick_puzzle(valid, graph)

    if debug:
        print(f"  [debug] component size: {len(valid):,}")
        print(f"  [debug] optimal path:   {' → '.join(w.upper() for w in optimal_path)}")

    print_header(start, end, len(optimal_path) - 1, day)

    player_path = [start]
    hint_level  = 0

    while True:
        current = player_path[-1]
        if current == end:
            break

        print()
        print_path(player_path)
        print()
        remaining = bfs(graph, current, end)
        steps_left = len(remaining) - 1 if remaining else "?"
        print(f"  Target: {c(YELLOW + BOLD, end.upper())}   "
              f"Moves: {len(player_path) - 1}   "
              f"Best from here: {steps_left}")
        print(f"  {c(DIM, 'Commands: hint · give up · quit')}")
        print()

        try:
            raw = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n\n  Bye!\n")
            return

        if raw == "quit":
            print("\n  Bye!\n")
            return

        if raw == "give up":
            print(f"\n  Optimal path ({len(optimal_path) - 1} steps):")
            print_path(optimal_path)
            print()
            return

        if raw == "hint":
            hint_level = min(hint_level + 1, 3)
            print_hint(graph, current, end, hint_level)
            continue

        err = validate(current, raw, valid)
        if err:
            print(f"\n  {c(YELLOW, '✗')}  {err}\n")
            continue

        hint_level = 0
        player_path.append(raw)

    print()
    print_result(player_path, optimal_path)
    print()


# ── Entry point (CLI only) ─────────────────────────────────────────────────────

def main() -> None:
    args    = sys.argv[1:]
    debug   = "--debug" in args
    lengths = [a for a in args if a.isdigit()]
    length  = int(lengths[0]) if lengths else 5

    if not (3 <= length <= 8):
        print("Word length must be between 3 and 8.")
        sys.exit(1)

    print(f"\n  {c(BOLD, 'WORD LADDER')}  —  building {length}-letter graph\n")

    words = load_words(length)
    print(f"  Building graph...", end="", flush=True)
    graph = build_graph(words)
    print(f" done.")

    print(f"  Finding main component...", end="", flush=True)
    valid = largest_component(graph)
    print(f" {len(valid):,} connected words.\n")

    play(valid, graph, debug=debug)


if __name__ == "__main__":
    main()
