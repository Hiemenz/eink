"""
Conway's Game of Life — e-ink module.

Each call advances the simulation one generation, renders it to a BMP,
and saves state so the simulation persists across restarts.

Config keys (under game_of_life:):
  output_path:      images/game_of_life.bmp
  state_file:       data/game_of_life_state.json
  cell_size:        10             # pixels per cell
  alive_color:      [0, 0, 0]     # RGB for live cells
  dead_color:       [255, 255, 255]  # RGB for dead cells
  grid_color:       [180, 180, 180]  # RGB cell borders; null to disable
  initial_density:  0.3           # fraction of cells alive on a new game (0.0–1.0)
  random_seed:      null          # integer for reproducible starts, null for random
  start_new:        false         # set true to discard state and restart fresh
  wrap:             true          # toroidal (wrap-around) edges
  show_generation:  true          # overlay generation counter
  show_population:  true          # overlay live-cell count
"""

import json
import os
import random
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw

from utils import get_font, get_logger

logger = get_logger("game_of_life")

STATE_FILE = "data/game_of_life_state.json"


# ── Grid helpers ──────────────────────────────────────────────────────────────

def _new_grid(rows: int, cols: int, density: float, seed: Optional[int]) -> List[List[int]]:
    """Generate a random initial grid."""
    rng = random.Random(seed)
    return [[1 if rng.random() < density else 0 for _ in range(cols)] for _ in range(rows)]


def _next_generation(grid: List[List[int]], wrap: bool) -> List[List[int]]:
    """Apply one step of Conway's rules and return the new grid."""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    new_grid = [[0] * cols for _ in range(rows)]
    for r in range(rows):
        for c in range(cols):
            neighbors = 0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = r + dr, c + dc
                    if wrap:
                        neighbors += grid[nr % rows][nc % cols]
                    elif 0 <= nr < rows and 0 <= nc < cols:
                        neighbors += grid[nr][nc]
            alive = grid[r][c]
            if alive and neighbors in (2, 3):
                new_grid[r][c] = 1
            elif not alive and neighbors == 3:
                new_grid[r][c] = 1
    return new_grid


def _count_alive(grid: List[List[int]]) -> int:
    return sum(cell for row in grid for cell in row)


# ── State persistence ─────────────────────────────────────────────────────────

def _load_state(state_file: str) -> Optional[dict]:
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Could not load state file: %s", e)
    return None


def _save_state(state_file: str, generation: int, grid: List[List[int]]) -> None:
    os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)
    with open(state_file, "w") as f:
        json.dump({"generation": generation, "grid": grid}, f)


# ── Rendering ─────────────────────────────────────────────────────────────────

def _render(
    grid: List[List[int]],
    width: int,
    height: int,
    cell_size: int,
    alive_color: Tuple,
    dead_color: Tuple,
    grid_color: Optional[Tuple],
    generation: int,
    population: int,
    show_gen: bool,
    show_pop: bool,
    config: dict,
) -> Image.Image:
    img = Image.new("RGB", (width, height), dead_color)
    draw = ImageDraw.Draw(img)

    rows = len(grid)
    cols = len(grid[0]) if rows else 0

    for r in range(rows):
        for c in range(cols):
            x0 = c * cell_size
            y0 = r * cell_size
            x1 = x0 + cell_size - 1
            y1 = y0 + cell_size - 1
            if x1 >= width or y1 >= height:
                continue
            color = alive_color if grid[r][c] else dead_color
            if grid_color and cell_size > 2:
                draw.rectangle([x0, y0, x1, y1], fill=color, outline=grid_color)
            else:
                draw.rectangle([x0, y0, x1, y1], fill=color)

    # Overlay: generation / population counter
    if show_gen or show_pop:
        parts = []
        if show_gen:
            parts.append(f"Gen {generation:,}")
        if show_pop:
            parts.append(f"Pop {population:,}")
        text = "  |  ".join(parts)
        font = get_font(18, config=config)
        # Draw shadow first for readability on any background
        draw.text((11, 11), text, fill=dead_color, font=font)
        draw.text((10, 10), text, fill=alive_color, font=font)

    return img


# ── Public entry point ────────────────────────────────────────────────────────

def generate(config: dict) -> str:
    cfg = config.get("game_of_life", {})
    output_path = cfg.get("output_path", "images/game_of_life.bmp")
    state_file = cfg.get("state_file", STATE_FILE)
    cell_size = max(1, int(cfg.get("cell_size", 10)))
    alive_color = tuple(cfg.get("alive_color", [0, 0, 0]))
    dead_color = tuple(cfg.get("dead_color", [255, 255, 255]))
    raw_gc = cfg.get("grid_color", [180, 180, 180])
    grid_color: Optional[Tuple] = tuple(raw_gc) if raw_gc else None
    density = float(cfg.get("initial_density", 0.3))
    seed = cfg.get("random_seed")
    start_new = bool(cfg.get("start_new", False))
    wrap = bool(cfg.get("wrap", True))
    show_gen = bool(cfg.get("show_generation", True))
    show_pop = bool(cfg.get("show_population", True))

    width = config.get("width", 800)
    height = config.get("height", 480)
    cols = width // cell_size
    rows = height // cell_size

    # Load saved state or start fresh
    state = None if start_new else _load_state(state_file)

    if (
        state
        and isinstance(state.get("grid"), list)
        and len(state["grid"]) == rows
        and state["grid"]
        and len(state["grid"][0]) == cols
    ):
        grid = state["grid"]
        generation = int(state.get("generation", 0))
        logger.info("Resuming from generation %d (%dx%d grid)", generation, rows, cols)
    else:
        grid = _new_grid(rows, cols, density, seed)
        generation = 0
        reason = "start_new=true" if start_new else "no compatible saved state"
        logger.info("New game (%s): %dx%d grid, density=%.2f", reason, rows, cols, density)

    # Advance one generation
    grid = _next_generation(grid, wrap)
    generation += 1
    population = _count_alive(grid)
    logger.info("Generation %d — population %d / %d cells", generation, population, rows * cols)

    # Render to image
    img = _render(
        grid, width, height, cell_size,
        alive_color, dead_color, grid_color,
        generation, population,
        show_gen, show_pop, config,
    )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    logger.info("Saved: %s", output_path)

    # Persist state for next run
    _save_state(state_file, generation, grid)

    return output_path


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    logger.info("Output: %s", path)
