"""
Unit tests for modules/sudoku_puzzle.py

Covers the pure puzzle-generation logic: cell validity checks, the
backtracking solver, and the clue-removal step that turns a full solution
into a puzzle with the requested number of clues.
"""

import sys
import os
import random
from datetime import date
from unittest.mock import patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.sudoku_puzzle import _is_valid, _solve, _generate_puzzle, _render, generate


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

    def test_num_clues_81_produces_full_grid(self):
        puzzle, solution = _generate_puzzle(seed=1, num_clues=81)
        assert puzzle == solution
        assert _count_clues(puzzle) == 81

    def test_num_clues_above_81_still_produces_full_grid(self):
        """Regression: num_clues > 81 used to make cells_to_remove negative,
        and cells[:negative] slices from the end -- stripping nearly the
        whole grid instead of leaving it full."""
        puzzle, solution = _generate_puzzle(seed=1, num_clues=200)
        assert puzzle == solution
        assert _count_clues(puzzle) == 81


class TestRender:
    def _puzzle(self):
        puzzle, _ = _generate_puzzle(seed=42, num_clues=35)
        return puzzle

    def test_render_creates_output_file_at_default_size(self, tmp_path):
        from PIL import Image
        out = str(tmp_path / "sudoku.bmp")
        result = _render(self._puzzle(), out)
        assert result == out
        img = Image.open(out)
        assert img.size == (800, 480)

    def test_render_handles_full_grid_no_blanks(self, tmp_path):
        puzzle, solution = _generate_puzzle(seed=1, num_clues=81)
        out = str(tmp_path / "sudoku.bmp")
        _render(solution, out)  # must not raise
        assert os.path.exists(out)

    def test_render_handles_empty_puzzle(self, tmp_path):
        empty = [[0] * 9 for _ in range(9)]
        out = str(tmp_path / "sudoku.bmp")
        _render(empty, out)  # must not raise
        assert os.path.exists(out)

    def test_render_respects_custom_dimensions(self, tmp_path):
        from PIL import Image
        out = str(tmp_path / "sudoku.bmp")
        _render(self._puzzle(), out, width=400, height=300)
        assert Image.open(out).size == (400, 300)


class TestGenerate:
    def test_generate_default_output_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        output_path = generate({})
        assert output_path == "sudoku_display.bmp"
        assert os.path.exists(output_path)

    def test_generate_respects_custom_output_path_and_num_clues(self, tmp_path):
        from PIL import Image
        out = str(tmp_path / "custom.bmp")
        config = {"sudoku_puzzle": {"output_path": out, "num_clues": 45}}
        result = generate(config)
        assert result == out
        assert Image.open(out).size == (800, 480)

    def test_generate_is_deterministic_within_the_same_day(self, tmp_path):
        out1 = str(tmp_path / "a.bmp")
        out2 = str(tmp_path / "b.bmp")
        generate({"sudoku_puzzle": {"output_path": out1, "num_clues": 30}})
        generate({"sudoku_puzzle": {"output_path": out2, "num_clues": 30}})
        with open(out1, "rb") as f1, open(out2, "rb") as f2:
            assert f1.read() == f2.read()

    def test_generate_differs_across_simulated_days(self, tmp_path):
        out1 = str(tmp_path / "day1.bmp")
        out2 = str(tmp_path / "day2.bmp")
        with patch("modules.sudoku_puzzle.date") as mock_date:
            mock_date.today.return_value = date(2024, 1, 1)
            generate({"sudoku_puzzle": {"output_path": out1, "num_clues": 30}})

            mock_date.today.return_value = date(2024, 6, 15)
            generate({"sudoku_puzzle": {"output_path": out2, "num_clues": 30}})

        with open(out1, "rb") as f1, open(out2, "rb") as f2:
            assert f1.read() != f2.read()
