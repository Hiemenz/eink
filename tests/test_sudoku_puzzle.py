"""
Unit tests for modules/sudoku_puzzle.py

Covers the pure puzzle-generation logic: cell validity checks, the
backtracking solver, and the clue-removal step that turns a full solution
into a puzzle with the requested number of clues.
"""

import sys
import os
import random

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.sudoku_puzzle import _is_valid, _solve, _generate_puzzle


def _count_clues(puzzle):
    return sum(1 for row in puzzle for v in row if v != 0)


def _is_complete_valid_solution(grid):
    """A full 9x9 grid is a valid sudoku solution iff every row, column, and
    3x3 box contains each of 1-9 exactly once."""
    full_set = set(range(1, 10))
    for row in grid:
        if set(row) != full_set:
            return False
    for col in range(9):
        if set(grid[r][col] for r in range(9)) != full_set:
            return False
    for box_r in range(0, 9, 3):
        for box_c in range(0, 9, 3):
            cells = [grid[r][c] for r in range(box_r, box_r + 3) for c in range(box_c, box_c + 3)]
            if set(cells) != full_set:
                return False
    return True


class TestIsValid:
    def test_number_already_in_row_is_invalid(self):
        grid = [[0] * 9 for _ in range(9)]
        grid[0][0] = 5
        assert _is_valid(grid, 0, 3, 5) is False

    def test_number_already_in_column_is_invalid(self):
        grid = [[0] * 9 for _ in range(9)]
        grid[3][2] = 7
        assert _is_valid(grid, 0, 2, 7) is False

    def test_number_already_in_box_is_invalid(self):
        grid = [[0] * 9 for _ in range(9)]
        grid[1][1] = 4
        assert _is_valid(grid, 0, 0, 4) is False

    def test_valid_placement_returns_true(self):
        grid = [[0] * 9 for _ in range(9)]
        assert _is_valid(grid, 0, 0, 1) is True

    def test_valid_when_number_present_elsewhere_out_of_scope(self):
        grid = [[0] * 9 for _ in range(9)]
        # 5 present far away in a different row/col/box than (8, 8)
        grid[0][0] = 5
        assert _is_valid(grid, 8, 8, 5) is True


class TestSolve:
    def test_solve_fills_empty_grid_completely(self):
        grid = [[0] * 9 for _ in range(9)]
        rng = random.Random(42)
        result = _solve(grid, rng)
        assert result is True
        assert all(all(cell != 0 for cell in row) for row in grid)

    def test_solve_produces_valid_sudoku_solution(self):
        grid = [[0] * 9 for _ in range(9)]
        rng = random.Random(123)
        _solve(grid, rng)
        assert _is_complete_valid_solution(grid)

    def test_solve_is_deterministic_given_same_seed(self):
        grid1 = [[0] * 9 for _ in range(9)]
        _solve(grid1, random.Random(7))
        grid2 = [[0] * 9 for _ in range(9)]
        _solve(grid2, random.Random(7))
        assert grid1 == grid2

    def test_different_seeds_can_produce_different_solutions(self):
        grid1 = [[0] * 9 for _ in range(9)]
        _solve(grid1, random.Random(1))
        grid2 = [[0] * 9 for _ in range(9)]
        _solve(grid2, random.Random(999))
        # Not guaranteed to differ in theory, but overwhelmingly likely with
        # a shuffled candidate order; this pins down real randomization.
        assert grid1 != grid2

    def test_unsolvable_grid_returns_false(self):
        # Cell (0, 0) is empty but every digit 1-9 is already used somewhere
        # in its row or column, so no candidate can ever be placed there.
        # _solve() checks the first empty cell (row-major) before recursing,
        # so this is rejected on the very first call -- fast and deterministic.
        #
        # NOTE: a same-row duplicate (e.g. two 5's) is NOT a reliable
        # "unsolvable" fixture here: _is_valid() only forbids placing a value
        # that already exists in the row/col/box, it doesn't require a row to
        # contain every digit. That leaves the rest of the grid free enough
        # that naive backtracking spends enormous time searching before it
        # can conclude there's truly no valid completion -- it hung for over
        # 10 minutes in this suite before being killed. Don't reintroduce that
        # pattern; use a first-cell dead-end like the one below instead.
        grid = [[0] * 9 for _ in range(9)]
        grid[0] = [0, 1, 2, 3, 4, 5, 6, 7, 8]
        grid[1][0] = 9
        rng = random.Random(1)
        assert _solve(grid, rng) is False


class TestGeneratePuzzle:
    def test_generate_puzzle_returns_puzzle_and_solution(self):
        puzzle, solution = _generate_puzzle(seed=2024, num_clues=35)
        assert len(puzzle) == 9
        assert len(solution) == 9
        assert _is_complete_valid_solution(solution)

    def test_puzzle_has_requested_number_of_clues(self):
        puzzle, _solution = _generate_puzzle(seed=2024, num_clues=35)
        assert _count_clues(puzzle) == 35

    def test_puzzle_clues_match_solution(self):
        puzzle, solution = _generate_puzzle(seed=99, num_clues=40)
        for r in range(9):
            for c in range(9):
                if puzzle[r][c] != 0:
                    assert puzzle[r][c] == solution[r][c]

    def test_same_seed_produces_same_puzzle(self):
        puzzle1, solution1 = _generate_puzzle(seed=555, num_clues=30)
        puzzle2, solution2 = _generate_puzzle(seed=555, num_clues=30)
        assert puzzle1 == puzzle2
        assert solution1 == solution2

    def test_different_seeds_produce_different_puzzles(self):
        puzzle1, _ = _generate_puzzle(seed=1, num_clues=30)
        puzzle2, _ = _generate_puzzle(seed=2, num_clues=30)
        assert puzzle1 != puzzle2

    def test_num_clues_zero_produces_empty_puzzle(self):
        puzzle, _solution = _generate_puzzle(seed=1, num_clues=0)
        assert _count_clues(puzzle) == 0
