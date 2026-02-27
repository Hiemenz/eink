"""
Sudoku Puzzle module.

Generates a daily Sudoku puzzle locally (no API needed) using a date-seeded
random number generator so the puzzle is consistent within the day but changes
each day.

Layout:
  - Top: "Sudoku" header centered, black
  - Below header: today's date in gray
  - Center: 9x9 grid with thick borders between 3x3 boxes, thin borders between cells
  - Clue numbers rendered in black; empty cells left blank
"""

import os
import platform
import random
from datetime import date
from PIL import Image, ImageDraw, ImageFont


def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


def _is_valid(grid, row, col, num):
    if num in grid[row]:
        return False
    if num in (grid[r][col] for r in range(9)):
        return False
    box_r, box_c = (row // 3) * 3, (col // 3) * 3
    for r in range(box_r, box_r + 3):
        for c in range(box_c, box_c + 3):
            if grid[r][c] == num:
                return False
    return True


def _solve(grid, rng):
    for row in range(9):
        for col in range(9):
            if grid[row][col] == 0:
                candidates = list(range(1, 10))
                rng.shuffle(candidates)
                for num in candidates:
                    if _is_valid(grid, row, col, num):
                        grid[row][col] = num
                        if _solve(grid, rng):
                            return True
                        grid[row][col] = 0
                return False
    return True


def _generate_puzzle(seed, num_clues=35):
    rng = random.Random(seed)
    solution = [[0] * 9 for _ in range(9)]
    _solve(solution, rng)
    puzzle = [row[:] for row in solution]
    cells = [(r, c) for r in range(9) for c in range(9)]
    rng.shuffle(cells)
    cells_to_remove = 81 - num_clues
    for r, c in cells[:cells_to_remove]:
        puzzle[r][c] = 0
    return puzzle, solution


def _render(puzzle, output_path, width=800, height=480):
    bg = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(bg)
    fp = _font_path()
    today_str = date.today().strftime("%B %d, %Y")
    try:
        title_font = ImageFont.truetype(fp, 32)
    except Exception:
        title_font = ImageFont.load_default()
    try:
        date_font = ImageFont.truetype(fp, 16)
    except Exception:
        date_font = ImageFont.load_default()
    title_text = "Sudoku"
    title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
    title_w = title_bbox[2] - title_bbox[0]
    title_h = title_bbox[3] - title_bbox[1]
    title_y = 10
    draw.text(((width - title_w) // 2, title_y), title_text, fill="black", font=title_font)
    date_bbox = draw.textbbox((0, 0), today_str, font=date_font)
    date_w = date_bbox[2] - date_bbox[0]
    date_h = date_bbox[3] - date_bbox[1]
    date_y = title_y + title_h + 4
    draw.text(((width - date_w) // 2, date_y), today_str, fill=(140, 140, 140), font=date_font)
    header_bottom = date_y + date_h + 10
    margin_bottom = 12
    available_h = height - header_bottom - margin_bottom
    cell_size = min(available_h // 9, (width - 40) // 9)
    grid_size = cell_size * 9
    grid_x = (width - grid_size) // 2
    grid_y = header_bottom + (available_h - grid_size) // 2
    num_font_size = max(10, int(cell_size * 0.55))
    try:
        num_font = ImageFont.truetype(fp, num_font_size)
    except Exception:
        num_font = ImageFont.load_default()
    thin_width = 1
    thick_width = 3
    for row in range(9):
        for col in range(9):
            val = puzzle[row][col]
            if val == 0:
                continue
            x0 = grid_x + col * cell_size
            y0 = grid_y + row * cell_size
            text = str(val)
            tb = draw.textbbox((0, 0), text, font=num_font)
            tw = tb[2] - tb[0]
            th = tb[3] - tb[1]
            tx = x0 + (cell_size - tw) // 2
            ty = y0 + (cell_size - th) // 2
            draw.text((tx, ty), text, fill="black", font=num_font)
    for i in range(10):
        is_box_line = (i % 3 == 0)
        lw = thick_width if is_box_line else thin_width
        x = grid_x + i * cell_size
        draw.line([(x, grid_y), (x, grid_y + grid_size)], fill="black", width=lw)
        y = grid_y + i * cell_size
        draw.line([(grid_x, y), (grid_x + grid_size, y)], fill="black", width=lw)
    bg.save(output_path)
    print(f"[sudoku] Saved to {output_path}")
    return output_path


def generate(config):
    """Generate Sudoku puzzle image. Return output path."""
    cfg = config.get("sudoku_puzzle", {})
    output_path = cfg.get("output_path", "sudoku_display.bmp")
    num_clues = cfg.get("num_clues", 35)
    seed = date.today().toordinal()
    print(f"[sudoku] Generating puzzle with seed {seed} ({date.today().isoformat()})")
    puzzle, _solution = _generate_puzzle(seed, num_clues=num_clues)
    return _render(puzzle, output_path)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
