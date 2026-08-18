"""
Unit tests for Conway's Game of Life grid/state logic in
modules/game_of_life.py.
"""

import sys
import os
import json

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.game_of_life import (
    _new_grid,
    _next_generation,
    _count_alive,
    _load_state,
    _save_state,
    generate,
)


class TestNewGrid:
    def test_dimensions_match_rows_cols(self):
        grid = _new_grid(5, 7, 0.3, seed=1)
        assert len(grid) == 5
        assert all(len(row) == 7 for row in grid)

    def test_density_zero_yields_all_dead(self):
        grid = _new_grid(10, 10, 0.0, seed=1)
        assert _count_alive(grid) == 0

    def test_density_one_yields_all_alive(self):
        grid = _new_grid(10, 10, 1.0, seed=1)
        assert _count_alive(grid) == 100

    def test_seed_is_reproducible(self):
        grid_a = _new_grid(8, 8, 0.5, seed=42)
        grid_b = _new_grid(8, 8, 0.5, seed=42)
        assert grid_a == grid_b

    def test_cells_are_only_0_or_1(self):
        grid = _new_grid(6, 6, 0.5, seed=3)
        for row in grid:
            for cell in row:
                assert cell in (0, 1)


class TestNextGenerationRules:
    def _empty(self, rows, cols):
        return [[0] * cols for _ in range(rows)]

    def test_dead_cell_stays_dead_with_no_neighbors(self):
        grid = self._empty(5, 5)
        new_grid = _next_generation(grid, wrap=False)
        assert _count_alive(new_grid) == 0

    def test_live_cell_with_fewer_than_two_neighbors_dies(self):
        grid = self._empty(5, 5)
        grid[2][2] = 1  # lone cell, no neighbors
        new_grid = _next_generation(grid, wrap=False)
        assert new_grid[2][2] == 0

    def test_live_cell_with_more_than_three_neighbors_dies(self):
        grid = self._empty(5, 5)
        # Surround center with 4 neighbors (overpopulation)
        grid[2][2] = 1
        grid[1][2] = 1
        grid[3][2] = 1
        grid[2][1] = 1
        grid[2][3] = 1
        new_grid = _next_generation(grid, wrap=False)
        assert new_grid[2][2] == 0

    def test_live_cell_with_two_or_three_neighbors_survives(self):
        grid = self._empty(5, 5)
        grid[2][2] = 1
        grid[1][2] = 1
        grid[3][2] = 1
        new_grid = _next_generation(grid, wrap=False)
        assert new_grid[2][2] == 1

    def test_dead_cell_with_exactly_three_neighbors_becomes_alive(self):
        grid = self._empty(5, 5)
        grid[1][2] = 1
        grid[2][1] = 1
        grid[2][3] = 1
        new_grid = _next_generation(grid, wrap=False)
        assert new_grid[2][2] == 1

    def test_blinker_oscillates_with_period_two(self):
        # Classic blinker: horizontal 3-cell line oscillates to vertical
        # and back every generation.
        grid = self._empty(5, 5)
        grid[2][1] = grid[2][2] = grid[2][3] = 1
        gen1 = _next_generation(grid, wrap=False)
        expected_gen1 = self._empty(5, 5)
        expected_gen1[1][2] = expected_gen1[2][2] = expected_gen1[3][2] = 1
        assert gen1 == expected_gen1

        gen2 = _next_generation(gen1, wrap=False)
        assert gen2 == grid

    def test_still_life_block_is_stable(self):
        # 2x2 block is a still life: unchanged generation after generation.
        grid = self._empty(6, 6)
        grid[2][2] = grid[2][3] = grid[3][2] = grid[3][3] = 1
        new_grid = _next_generation(grid, wrap=False)
        assert new_grid == grid

    def test_wrap_true_neighbors_wrap_around_edges(self):
        # A single live cell at (0,0) with wrap enabled counts a neighbor
        # placed at the opposite edge (rows-1, cols-1) as adjacent.
        rows, cols = 4, 4
        grid = self._empty(rows, cols)
        grid[0][0] = 1
        grid[rows - 1][cols - 1] = 1
        grid[0][1] = 1
        # Neighbors of (0,0) with wrap: (rows-1,cols-1), (rows-1,0), (rows-1,1),
        # (0,cols-1), (0,1), (1,cols-1), (1,0), (1,1)
        # We've set (rows-1,cols-1) and (0,1) alive -> 2 neighbors -> survives
        new_grid = _next_generation(grid, wrap=True)
        assert new_grid[0][0] == 1

    def test_wrap_false_no_wraparound_neighbors(self):
        # Without wrap, a corner cell's off-grid neighbors don't count.
        rows, cols = 4, 4
        grid = self._empty(rows, cols)
        grid[0][0] = 1
        grid[rows - 1][cols - 1] = 1  # would count as neighbor only if wrapped
        new_grid = _next_generation(grid, wrap=False)
        # (0,0) has 0 real neighbors -> dies regardless
        assert new_grid[0][0] == 0

    def test_output_dimensions_match_input(self):
        grid = _new_grid(7, 9, 0.4, seed=5)
        new_grid = _next_generation(grid, wrap=True)
        assert len(new_grid) == 7
        assert all(len(row) == 9 for row in new_grid)


class TestCountAlive:
    def test_empty_grid_zero(self):
        assert _count_alive([[0, 0], [0, 0]]) == 0

    def test_full_grid_counts_all(self):
        assert _count_alive([[1, 1], [1, 1]]) == 4

    def test_mixed_grid(self):
        assert _count_alive([[1, 0], [0, 1], [1, 1]]) == 4


class TestStatePersistence:
    def test_save_then_load_round_trip(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        grid = [[1, 0], [0, 1]]
        _save_state(state_file, 42, grid)
        loaded = _load_state(state_file)
        assert loaded == {"generation": 42, "grid": grid}

    def test_load_missing_file_returns_none(self, tmp_path):
        state_file = str(tmp_path / "does_not_exist.json")
        assert _load_state(state_file) is None

    def test_load_corrupt_file_returns_none(self, tmp_path):
        state_file = tmp_path / "corrupt.json"
        state_file.write_text("{not valid json")
        assert _load_state(str(state_file)) is None

    def test_save_creates_parent_directories(self, tmp_path):
        state_file = str(tmp_path / "nested" / "dir" / "state.json")
        _save_state(state_file, 1, [[0]])
        assert os.path.exists(state_file)
        with open(state_file) as f:
            data = json.load(f)
        assert data["generation"] == 1


class TestGenerate:
    def _config(self, tmp_path, **overrides):
        cfg = {
            "width": 40,
            "height": 40,
            "game_of_life": {
                "output_path": str(tmp_path / "out.bmp"),
                "state_file": str(tmp_path / "state.json"),
                "cell_size": 10,
                "initial_density": 0.5,
                "random_seed": 7,
            },
        }
        cfg["game_of_life"].update(overrides)
        return cfg

    def test_first_run_creates_output_and_state(self, tmp_path):
        config = self._config(tmp_path)
        result = generate(config)
        assert result == config["game_of_life"]["output_path"]
        assert os.path.exists(result)
        state = _load_state(config["game_of_life"]["state_file"])
        assert state["generation"] == 1

    def test_second_run_resumes_and_advances_generation(self, tmp_path):
        config = self._config(tmp_path)
        generate(config)
        generate(config)
        state = _load_state(config["game_of_life"]["state_file"])
        assert state["generation"] == 2

    def test_start_new_ignores_existing_state(self, tmp_path):
        config = self._config(tmp_path)
        generate(config)
        generate(config)  # generation now 2

        config["game_of_life"]["start_new"] = True
        generate(config)
        state = _load_state(config["game_of_life"]["state_file"])
        assert state["generation"] == 1  # restarted, not 3

    def test_mismatched_grid_dimensions_starts_fresh(self, tmp_path):
        config = self._config(tmp_path)
        generate(config)  # 4x4 grid (40/10)

        config["game_of_life"]["cell_size"] = 20  # now 2x2 grid — dims no longer match
        generate(config)
        state = _load_state(config["game_of_life"]["state_file"])
        assert state["generation"] == 1  # treated as a fresh start

    def test_output_image_has_configured_dimensions(self, tmp_path):
        config = self._config(tmp_path)
        generate(config)
        img = Image.open(config["game_of_life"]["output_path"])
        assert img.size == (40, 40)

    def test_show_gen_and_pop_false_does_not_crash(self, tmp_path):
        config = self._config(tmp_path, show_generation=False, show_population=False)
        result = generate(config)
        assert os.path.exists(result)

    def test_empty_grid_color_disables_outline_without_crash(self, tmp_path):
        config = self._config(tmp_path, grid_color=None)
        result = generate(config)
        assert os.path.exists(result)
