"""
Moon Phase module.

Calculates the current moon phase using pure math (no network required),
draws a visual moon disc with Pillow, and renders phase name, illumination
percentage, and days until next full moon on a black canvas.
"""

import math
import platform
from datetime import date, datetime, timezone
from PIL import Image, ImageDraw, ImageFont


def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


# ---------------------------------------------------------------------------
# Moon phase math
# ---------------------------------------------------------------------------

# Known new moon: January 6, 2000 18:14 UTC  (Julian Date 2451550.1)
_REF_NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
_LUNAR_CYCLE = 29.53059  # days


def _moon_age(today=None):
    """Days since the reference new moon (mod lunar cycle)."""
    if today is None:
        today = datetime.now(tz=timezone.utc)
    delta = (today - _REF_NEW_MOON).total_seconds() / 86400
    return delta % _LUNAR_CYCLE


def _phase_fraction(age):
    """Fraction through the lunar cycle: 0 = new, 0.5 = full."""
    return age / _LUNAR_CYCLE


def _phase_name(fraction):
    if fraction < 0.0625:
        return "New Moon"
    elif fraction < 0.25:
        return "Waxing Crescent"
    elif fraction < 0.3125:
        return "First Quarter"
    elif fraction < 0.5:
        return "Waxing Gibbous"
    elif fraction < 0.5625:
        return "Full Moon"
    elif fraction < 0.75:
        return "Waning Gibbous"
    elif fraction < 0.8125:
        return "Last Quarter"
    else:
        return "Waning Crescent"


def _illumination(fraction):
    """Percentage of the moon's face that is illuminated."""
    return round(50 * (1 - math.cos(fraction * 2 * math.pi)))


def _days_until_full(age):
    """Days remaining until the next full moon."""
    fraction = age / _LUNAR_CYCLE
    if fraction < 0.5:
        return (0.5 - fraction) * _LUNAR_CYCLE
    else:
        return (1.5 - fraction) * _LUNAR_CYCLE


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def _draw_moon(fraction, radius=180):
    """
    Draw a moon disc at 2× size for anti-aliasing, return PIL Image.
    fraction: 0.0 = new moon, 0.5 = full moon, 1.0 = new moon again.
    """
    scale = 2
    r = radius * scale
    size = (r * 2 + 20) * scale  # small padding
    canvas_size = int(size)

    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx = cy = canvas_size // 2

    if fraction < 0.5:
        # Waxing: right side lit
        lit_side = "right"
        # Phase angle goes from 0 (new) to pi (full) for waxing
        phase_angle = fraction * 2  # 0 to 1 where 1 = full
    else:
        # Waning: left side lit
        lit_side = "left"
        phase_angle = (1.0 - fraction) * 2  # 1 (full) to 0 (new)

    moon_color = (255, 255, 220, 255)
    dark_color = (30, 30, 40, 255)

    # Draw full disc
    draw.ellipse(
        [cx - r, cy - r, cx + r, cy + r],
        fill=dark_color
    )

    if fraction >= 0.0625 and fraction <= 0.9375:
        # Draw the lit portion
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=moon_color if lit_side == "right" else dark_color
        )

        # Terminator ellipse x-radius varies with phase
        # At quarter: terminator is vertical (x_r = 0)
        # At crescent: terminator curves toward lit side
        terminator_x = int(r * abs(math.cos(fraction * math.pi * 2)))

        if lit_side == "right":
            # Right half is lit; overlay dark ellipse on left with curved terminator
            # Draw the full disc lit
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=moon_color)
            # Cover right half with dark rectangle
            draw.rectangle([cx, cy - r - 1, cx + r + 1, cy + r + 1], fill=dark_color)
            # Draw terminator ellipse (dark side bulge) on right
            draw.ellipse(
                [cx - terminator_x, cy - r, cx + terminator_x, cy + r],
                fill=dark_color
            )
        else:
            # Left half is lit; overlay dark ellipse on right
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=moon_color)
            draw.rectangle([cx - r - 1, cy - r - 1, cx, cy + r + 1], fill=dark_color)
            draw.ellipse(
                [cx - terminator_x, cy - r, cx + terminator_x, cy + r],
                fill=moon_color
            )
    elif fraction < 0.0625 or fraction > 0.9375:
        # New moon: nearly dark
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(40, 40, 50, 255))
    else:
        # Full moon
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=moon_color)

    # Scale down for anti-aliasing
    final_size = canvas_size // scale
    img = img.resize((final_size, final_size), Image.LANCZOS)
    return img


def generate(config):
    """Generate Moon Phase image. Return output path."""
    moon_cfg = config.get("moon_phase", {})
    output_path = moon_cfg.get("output_path", "moon_display.bmp")
    width = config.get("width", 800)
    height = config.get("height", 480)

    age = _moon_age()
    fraction = _phase_fraction(age)
    name = _phase_name(fraction)
    illum = _illumination(fraction)
    days_to_full = _days_until_full(age)

    print(f"[moon] Phase: {name} ({illum}% illuminated, {days_to_full:.1f}d to full moon)")

    # Canvas: black background
    canvas = Image.new("RGB", (width, height), (0, 0, 0))

    # Draw moon disc on the left ~half
    moon_img = _draw_moon(fraction, radius=160)
    moon_img = moon_img.convert("RGBA")

    # Paste centered vertically, left-aligned with padding
    moon_w, moon_h = moon_img.size
    moon_x = 30
    moon_y = (height - moon_h) // 2
    canvas.paste(moon_img, (moon_x, moon_y), moon_img)

    draw = ImageDraw.Draw(canvas)
    fp = _font_path()

    # Text panel on the right
    text_x = moon_x + moon_w + 30
    text_w = width - text_x - 20
    text_y = 80

    # Phase name (large)
    for size in range(44, 20, -2):
        try:
            font = ImageFont.truetype(fp, size)
        except Exception:
            font = ImageFont.load_default()
        if draw.textbbox((0, 0), name, font=font)[2] <= text_w:
            break
    draw.text((text_x, text_y), name, fill="white", font=font)
    text_y += draw.textbbox((0, 0), name, font=font)[3] + 18

    # Illumination
    try:
        info_font = ImageFont.truetype(fp, 26)
    except Exception:
        info_font = ImageFont.load_default()
    draw.text((text_x, text_y), f"{illum}% illuminated", fill=(180, 180, 180), font=info_font)
    text_y += draw.textbbox((0, 0), "Ag", font=info_font)[3] + 12

    # Days until full moon
    try:
        sub_font = ImageFont.truetype(fp, 22)
    except Exception:
        sub_font = ImageFont.load_default()
    if fraction < 0.5:
        full_label = f"Full moon in {days_to_full:.1f} days"
    elif fraction < 0.5625:
        full_label = "Full moon tonight"
    else:
        full_label = f"Full moon in {days_to_full:.1f} days"
    draw.text((text_x, text_y), full_label, fill=(140, 140, 140), font=sub_font)
    text_y += draw.textbbox((0, 0), "Ag", font=sub_font)[3] + 30

    # Divider
    draw.line([(text_x, text_y), (width - 20, text_y)], fill=(60, 60, 60), width=1)
    text_y += 20

    # Today's date
    try:
        date_font = ImageFont.truetype(fp, 18)
    except Exception:
        date_font = ImageFont.load_default()
    today_str = date.today().strftime("%B %-d, %Y")
    draw.text((text_x, text_y), today_str, fill=(100, 100, 100), font=date_font)

    canvas.save(output_path)
    print(f"[moon] Saved to {output_path}")
    return output_path


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
