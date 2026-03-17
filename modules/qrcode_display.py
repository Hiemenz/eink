import os
from PIL import Image, ImageDraw, ImageFont
import qrcode
import qrcode.constants

from utils import get_font, get_logger

logger = get_logger("qrcode_display")

CANVAS_W = 800
CANVAS_H = 480
QR_SIZE = 340  # target rendered size in pixels


def _build_qr_text(cfg: dict) -> str | None:
    """Return the raw string to encode, or None if nothing is configured."""
    ssid = cfg.get("wifi_ssid", "").strip()
    if ssid:
        password = cfg.get("wifi_password", "")
        security = cfg.get("wifi_security", "WPA")
        return f"WIFI:T:{security};S:{ssid};P:{password};;"
    text = cfg.get("text", "").strip()
    return text if text else None


def _make_qr_image(text: str) -> Image.Image:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=2,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    return img.convert("RGB")


def _render_placeholder(draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont) -> None:
    msg = "No QR text configured.\nUse !set qrcode_display.text <value>"
    bbox = draw.multiline_textbbox((0, 0), msg, font=font, align="center")
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (CANVAS_W - w) // 2
    y = (CANVAS_H - h) // 2
    draw.multiline_text((x, y), msg, fill="black", font=font, align="center")


def generate(config: dict) -> str:
    cfg = config.get("qrcode_display", {})
    if not isinstance(cfg, dict):
        cfg = {}

    output_path = cfg.get("output_path", "images/qrcode_display.bmp")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), color=(255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    qr_text = _build_qr_text(cfg)

    if not qr_text:
        placeholder_font = get_font(24)
        _render_placeholder(draw, placeholder_font)
        canvas.save(output_path)
        logger.info("Saved placeholder QR image to %s", output_path)
        return output_path

    # --- Generate and resize QR code ---
    qr_img = _make_qr_image(qr_text)
    qr_img = qr_img.resize((QR_SIZE, QR_SIZE), Image.NEAREST)

    label = cfg.get("label", "").strip()
    sublabel = cfg.get("sublabel", "").strip()

    label_font = get_font(28)
    sublabel_font = get_font(16)

    # Measure label area height so we can vertically center QR + labels together
    label_gap = 20
    label_h = 0
    sublabel_gap = 8
    sublabel_h = 0

    if label:
        lb = draw.textbbox((0, 0), label, font=label_font)
        label_h = lb[3] - lb[1]
    if sublabel:
        sb = draw.textbbox((0, 0), sublabel, font=sublabel_font)
        sublabel_h = sb[3] - sb[1]

    total_h = QR_SIZE
    if label:
        total_h += label_gap + label_h
    if sublabel:
        total_h += sublabel_gap + sublabel_h

    # Slight upward offset: center of canvas minus a small nudge
    nudge_up = 20
    block_top = (CANVAS_H - total_h) // 2 - nudge_up

    qr_x = (CANVAS_W - QR_SIZE) // 2
    qr_y = block_top

    # --- Thin border 4px outside QR code ---
    border_margin = 4
    border_color = (180, 180, 180)
    draw.rectangle(
        [
            qr_x - border_margin,
            qr_y - border_margin,
            qr_x + QR_SIZE + border_margin,
            qr_y + QR_SIZE + border_margin,
        ],
        outline=border_color,
        width=1,
    )

    # --- Paste QR code ---
    canvas.paste(qr_img, (qr_x, qr_y))

    # --- Draw label ---
    cursor_y = qr_y + QR_SIZE + label_gap
    if label:
        lb = draw.textbbox((0, 0), label, font=label_font)
        lw = lb[2] - lb[0]
        label_x = (CANVAS_W - lw) // 2
        draw.text((label_x, cursor_y), label, fill="black", font=label_font)
        cursor_y += label_h + sublabel_gap

    if sublabel:
        sb = draw.textbbox((0, 0), sublabel, font=sublabel_font)
        sw = sb[2] - sb[0]
        sublabel_x = (CANVAS_W - sw) // 2
        draw.text((sublabel_x, cursor_y), sublabel, fill=(170, 170, 170), font=sublabel_font)

    canvas.save(output_path)
    logger.info("Saved QR image to %s", output_path)
    return output_path


if __name__ == "__main__":
    import yaml

    with open("config.yml", "r") as f:
        config = yaml.safe_load(f)

    output = generate(config)
    print(f"Generated: {output}")
