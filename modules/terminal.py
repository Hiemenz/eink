"""
Terminal module.

Renders a terminal-style view on the e-ink display. Command history and
output are written to data/terminal_state.json by the Discord bot's !run
command, then this module reads them and draws the screen.

Layout: black background, monospace font, prompt + output lines.
Scrolls to show the most recent lines that fit on the 800×480 canvas.
"""

import json
import os
import platform
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont


STATE_PATH = os.path.join("data", "terminal_state.json")

BG      = (13, 17, 23)       # near-black background
FG      = (201, 209, 217)    # light gray text
PROMPT  = (87, 171, 90)      # green prompt
CMD_FG  = (255, 255, 255)    # white command text
ERR_FG  = (255, 100, 100)    # red for non-zero exit
DIM     = (110, 118, 129)    # dimmed — timestamps / separators
MAX_HISTORY = 20


# ---------------------------------------------------------------------------
# Font
# ---------------------------------------------------------------------------

_MONO_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/Library/Fonts/Courier New.ttf",
    "/Library/Fonts/Andale Mono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
]


def _font(size):
    for path in _MONO_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# State helpers (called by the Discord bot)
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"history": []}


def save_entry(command: str, output: str, exit_code: int) -> None:
    """Append a command+output entry. Called by the Discord bot after !run."""
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    state = load_state()
    state.setdefault("history", []).append({
        "command":   command,
        "output":    output,
        "exit_code": exit_code,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    })
    # Keep only the last MAX_HISTORY entries
    state["history"] = state["history"][-MAX_HISTORY:]
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _wrap_line(text: str, font, draw, max_width: int) -> list[str]:
    """Break a single line into wrapped sub-lines."""
    if not text:
        return [""]
    words, lines, cur = text.split(" "), [], ""
    for word in words:
        test = (cur + " " + word).lstrip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _render(state: dict, output_path: str, width=800, height=480):
    img  = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    font_size = 14
    font = _font(font_size)
    line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + 3

    margin_x = 12
    margin_y = 10
    content_w = width - 2 * margin_x

    # ── Build all display lines bottom-up ────────────────────────────────────
    # Each item: (text, color)
    display_lines: list[tuple[str, tuple]] = []

    history = state.get("history", [])
    if not history:
        display_lines.append(("No commands yet. Use !run <command> in Discord.", DIM))
    else:
        for entry in history:
            cmd       = entry.get("command", "")
            output    = entry.get("output", "")
            exit_code = entry.get("exit_code", 0)
            ts        = entry.get("timestamp", "")

            # Prompt + command line
            prompt_str = f"pi@eink:~$ "
            display_lines.append((prompt_str + cmd, CMD_FG))

            # Output lines
            color = ERR_FG if exit_code != 0 else FG
            for raw_line in output.splitlines():
                for wrapped in _wrap_line(raw_line, font, draw, content_w):
                    display_lines.append((wrapped, color))

            # Timestamp separator
            display_lines.append((f"  [{ts}]", DIM))

    # ── How many lines fit? ──────────────────────────────────────────────────
    max_lines = (height - 2 * margin_y) // line_h

    # Take the last max_lines lines
    visible = display_lines[-max_lines:]

    # ── Draw ─────────────────────────────────────────────────────────────────
    y = margin_y
    for text, color in visible:
        draw.text((margin_x, y), text, fill=color, font=font)
        y += line_h

    # Blinking-cursor stub on the last line
    cursor_x = margin_x + draw.textbbox((0, 0), "pi@eink:~$ ", font=font)[2]
    draw.rectangle([cursor_x, y - line_h + 2, cursor_x + 8, y - 3], fill=PROMPT)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    print(f"[terminal] Saved to {output_path} ({len(visible)} lines)")
    return output_path


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate(config: dict) -> str:
    cfg         = config.get("terminal", {})
    output_path = cfg.get("output_path", "images/terminal_display.bmp")
    width       = config.get("width", 800)
    height      = config.get("height", 480)
    state       = load_state()
    return _render(state, output_path, width, height)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
