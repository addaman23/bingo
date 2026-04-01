"""US-style 75-ball bingo card: 5x5 grid, B-I-N-G-O columns, center is FREE (0)."""

from __future__ import annotations

import random

COL_RANGES = ((1, 15), (16, 30), (31, 45), (46, 60), (61, 75))


def col_for_number(n: int) -> int:
    for i, (lo, hi) in enumerate(COL_RANGES):
        if lo <= n <= hi:
            return i
    raise ValueError(f"Number {n} is not in 1-75")


def generate_card_from_card_id(card_id: int) -> list[list[int]]:
    """
    Deterministic valid 75-ball card for lobby "card" IDs beyond 1–75 (e.g. 1–400).
    Each ID maps to a unique shuffle seed so picks don't share the same grid.
    """
    if card_id < 1:
        raise ValueError("card_id must be >= 1")
    rng = random.Random(card_id)
    grid: list[list[int]] = [[0] * 5 for _ in range(5)]
    grid[2][2] = 0
    for c in range(5):
        lo, hi = COL_RANGES[c]
        pool = list(range(lo, hi + 1))
        rng.shuffle(pool)
        rows = [0, 1, 2, 3, 4] if c != 2 else [0, 1, 3, 4]
        need = len(rows)
        picks: list[int] = []
        for _ in range(need):
            picks.append(pool.pop())
        rng.shuffle(picks)
        for r, val in zip(rows, picks):
            grid[r][c] = val
    return grid


def generate_card(ticket: int) -> list[list[int]]:
    """
    Build a valid card that includes `ticket` on the correct column (not on FREE cell).
    """
    _ = col_for_number(ticket)
    tc = col_for_number(ticket)
    grid: list[list[int]] = [[0] * 5 for _ in range(5)]
    grid[2][2] = 0  # FREE

    for c in range(5):
        lo, hi = COL_RANGES[c]
        pool = list(range(lo, hi + 1))
        random.shuffle(pool)
        rows = [0, 1, 2, 3, 4] if c != 2 else [0, 1, 3, 4]
        need = len(rows)
        picks: list[int] = []
        if c == tc:
            picks.append(ticket)
            pool = [p for p in pool if p != ticket]
        while len(picks) < need:
            picks.append(pool.pop())
        random.shuffle(picks)
        for r, val in zip(rows, picks):
            grid[r][c] = val
    return grid


def marks_to_set(marked: list) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for item in marked:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            out.add((int(item[0]), int(item[1])))
    return out


def has_complete_line(marks: set[tuple[int, int]]) -> bool:
    """True if any supported bingo pattern is fully covered."""
    return winning_line_cells(marks) is not None


def winning_line_cells(marks: set[tuple[int, int]]) -> tuple[str, set[tuple[int, int]]] | None:
    """First completed bingo pattern: (human label, cells in that pattern). FREE counts as marked."""
    marks = set(marks)
    marks.add((2, 2))

    for r in range(5):
        cells = {(r, c) for c in range(5)}
        if cells <= marks:
            return (f"Row {r + 1}", cells)
    for c in range(5):
        cells = {(r, c) for r in range(5)}
        letter = "BINGO"[c]
        if cells <= marks:
            return (f"Column {letter}", cells)
    diag_main = {(i, i) for i in range(5)}
    if diag_main <= marks:
        return ("Diagonal (Top-Left to Bottom-Right)", diag_main)
    diag_anti = {(i, 4 - i) for i in range(5)}
    if diag_anti <= marks:
        return ("Diagonal (Top-Right to Bottom-Left)", diag_anti)
    corners = {(0, 0), (0, 4), (4, 0), (4, 4)}
    if corners <= marks:
        return ("Four Corners", corners)
    return None
