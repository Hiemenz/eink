"""
Chess Puzzle of the Day module.

Fetches the Lichess daily puzzle from their free API, parses the FEN position
from the PGN metadata (or reconstructs it by replaying moves to initial_ply),
then renders an 800x480 BMP showing:
  - An 8x8 chess board diagram with piece Unicode glyphs (left side, ~360x360 px)
  - Rank / file labels around the board
  - Right-side panel: "Chess Puzzle" header, whose turn to move, rating,
    puzzle ID, "Find the best move!" call-to-action, and today's date.

FEN parsed directly -- no external chess libraries used.

Piece map:
  r=♜  n=♞  b=♝  q=♛  k=♚  p=♟
  R=♖  N=♘  B=♗  Q=♕  K=♔  P=♙

Fallback: if the API is unreachable, renders an empty board with a notice.
"""

import os
import json
import platform
import re
import requests
from datetime import date
from PIL import Image, ImageDraw, ImageFont


HEADERS   = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}
CACHE_DIR = "data"

LIGHT_SQ = (240, 217, 181)
DARK_SQ  = (181, 136, 99)

PIECE_GLYPH = {
    "r": "\u265c", "n": "\u265e", "b": "\u265d",
    "q": "\u265b", "k": "\u265a", "p": "\u265f",
    "R": "\u2656", "N": "\u2658", "B": "\u2657",
    "Q": "\u2655", "K": "\u2654", "P": "\u2659",
}

PIECE_INK = {
    "r": (15,15,15), "n": (15,15,15), "b": (15,15,15),
    "q": (15,15,15), "k": (15,15,15), "p": (15,15,15),
    "R": (255,255,255), "N": (255,255,255), "B": (255,255,255),
    "Q": (255,255,255), "K": (255,255,255), "P": (255,255,255),
}

BOARD_PX   = 360
SQ_PX      = BOARD_PX // 8   # 45 px per square
LABEL_PX   = 18
BOARD_LEFT = LABEL_PX + 8
BOARD_TOP  = (480 - BOARD_PX - LABEL_PX) // 2


# ---------------------------------------------------------------------------
# Font helper
# ---------------------------------------------------------------------------

def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


def _font(size):
    try:
        return ImageFont.truetype(_font_path(), size)
    except Exception:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path():
    today = date.today().isoformat()
    return os.path.join(CACHE_DIR, f"chess_cache_{today}.json")


def _load_cache():
    path = _cache_path()
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def _save_cache(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(), "w") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# Lichess API
# ---------------------------------------------------------------------------

def _fetch_puzzle():
    """
    Fetch today's Lichess daily puzzle.
    Returns dict: {fen, side_to_move, rating, puzzle_id} or None on failure.
    """
    url = "https://lichess.org/api/puzzle/daily"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[chess] Failed to fetch Lichess puzzle: {e}")
        return None

    puzzle_id   = data.get("puzzle", {}).get("id", "unknown")
    rating      = data.get("puzzle", {}).get("rating", 0)
    pgn         = data.get("game",   {}).get("pgn", "")
    initial_ply = data.get("puzzle", {}).get("initialPly", 0)

    fen, side = _fen_from_pgn(pgn, initial_ply)
    return {"fen": fen, "side_to_move": side, "rating": rating, "puzzle_id": puzzle_id}


# ---------------------------------------------------------------------------
# FEN from PGN
# ---------------------------------------------------------------------------

def _fen_from_pgn(pgn, initial_ply):
    """
    Return (fen_string, "White"|"Black") for the puzzle start position.

    1. Look for [FEN "..."] tag in PGN headers as the base position.
    2. Fall back to the standard starting position.
    3. Replay moves from that base up to initial_ply half-moves.
    """
    fen_match = re.search(r'\[FEN\s+"([^"]+)"\]', pgn, re.IGNORECASE)
    base_fen  = fen_match.group(1) if fen_match else \
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

    board, side_char, castling, ep, half_clock, full_move = _parse_fen(base_fen)
    moves = _san_moves_from_pgn(pgn)

    for i, san in enumerate(moves):
        if i >= initial_ply:
            break
        board, castling, ep = _apply_san(board, san, side_char, castling, ep)
        half_clock += 1
        if side_char == "b":
            full_move += 1
        side_char = "b" if side_char == "w" else "w"

    fen   = _board_to_fen(board, side_char, castling, ep, half_clock, full_move)
    label = "White" if side_char == "w" else "Black"
    return fen, label


# ---------------------------------------------------------------------------
# FEN serialisation helpers
# ---------------------------------------------------------------------------

def _parse_fen(fen):
    """Parse a FEN string. Returns (board_2d, side, castling, ep, half, full)."""
    parts      = fen.strip().split()
    board_str  = parts[0] if len(parts) > 0 else "8/8/8/8/8/8/8/8"
    side       = parts[1] if len(parts) > 1 else "w"
    castling   = parts[2] if len(parts) > 2 else "-"
    ep         = parts[3] if len(parts) > 3 else "-"
    half_clock = int(parts[4]) if len(parts) > 4 else 0
    full_move  = int(parts[5]) if len(parts) > 5 else 1
    board = []
    for rank in board_str.split("/"):
        row = []
        for ch in rank:
            if ch.isdigit():
                row.extend([None] * int(ch))
            else:
                row.append(ch)
        board.append(row[:8])
    return board, side, castling, ep, half_clock, full_move


def _board_to_fen(board, side, castling, ep, half_clock, full_move):
    rows = []
    for row in board:
        s, empty = "", 0
        for cell in row:
            if cell is None:
                empty += 1
            else:
                if empty:
                    s += str(empty)
                    empty = 0
                s += cell
        if empty:
            s += str(empty)
        rows.append(s)
    c = castling if castling and castling != "-" else "-"
    return f"{'/'.join(rows)} {side} {c} {ep} {half_clock} {full_move}"


# ---------------------------------------------------------------------------
# Minimal SAN parser / move applier
# ---------------------------------------------------------------------------

def _san_moves_from_pgn(pgn):
    """Strip PGN headers/annotations and return a list of SAN move tokens."""
    pgn = re.sub(r'\[[^\]]*\]', "", pgn)
    pgn = re.sub(r'\{[^}]*\}', "", pgn)
    pgn = re.sub(r'\([^)]*\)', "", pgn)
    pgn = re.sub(r'\d+\.+', "", pgn)
    pgn = re.sub(r'(1-0|0-1|1/2-1/2|\*)', "", pgn)
    return [t.strip("+#!?") for t in pgn.split() if t.strip("+#!?")]


def _sq(alg):
    """'e4' -> (row, col) 0-indexed from top-left."""
    return 8 - int(alg[1]), ord(alg[0]) - ord("a")


def _find_piece(board, piece, to_r, to_c, hc=None, hr=None):
    """Return (row, col) of the first matching piece, respecting disambiguation hints."""
    for r in range(8):
        for c in range(8):
            if board[r][c] != piece:
                continue
            if hc is not None and c != hc:
                continue
            if hr is not None and r != hr:
                continue
            return (r, c)
    return None


def _apply_san(board, san, side, castling, ep):
    """
    Apply a SAN half-move to the board (best-effort implementation).
    Handles castling, normal moves, captures, en passant, and promotion.
    Returns (new_board, new_castling, new_ep).
    """
    import copy
    board   = copy.deepcopy(board)
    ep_next = "-"

    # Castling
    if san in ("O-O", "0-0"):
        if side == "w":
            board[7][4]=None; board[7][6]="K"; board[7][7]=None; board[7][5]="R"
            castling = castling.replace("K","").replace("Q","")
        else:
            board[0][4]=None; board[0][6]="k"; board[0][7]=None; board[0][5]="r"
            castling = castling.replace("k","").replace("q","")
        return board, castling or "-", ep_next

    if san in ("O-O-O", "0-0-0"):
        if side == "w":
            board[7][4]=None; board[7][2]="K"; board[7][0]=None; board[7][3]="R"
            castling = castling.replace("K","").replace("Q","")
        else:
            board[0][4]=None; board[0][2]="k"; board[0][0]=None; board[0][3]="r"
            castling = castling.replace("k","").replace("q","")
        return board, castling or "-", ep_next

    # Promotion (e.g. e8=Q)
    promo = None
    pm = re.search(r"=?([QRBNqrbn])$", san)
    if pm:
        promo = pm.group(1).upper() if side == "w" else pm.group(1).lower()
        san   = san[:pm.start()]

    # Destination square
    dm = re.search(r"([a-h][1-8])$", san)
    if not dm:
        return board, castling, ep
    to_r, to_c = _sq(dm.group(1))
    rest   = san[:dm.start()]
    is_cap = "x" in rest
    rest   = rest.replace("x", "")

    # Piece type
    piece_upper = "P"
    hc = hr = None
    if rest and rest[0].isupper():
        piece_upper = rest[0]
        rest = rest[1:]

    # Disambiguation
    for ch in rest:
        if ch.isdigit():
            hr = 8 - int(ch)
        elif ch.islower():
            hc = ord(ch) - ord("a")

    # Pawn capture file hint (e.g. "exd5")
    if piece_upper == "P" and is_cap and hc is None:
        fc = re.search(r"([a-h])x", san)
        if fc:
            hc = ord(fc.group(1)) - ord("a")

    piece = piece_upper if side == "w" else piece_upper.lower()
    src   = _find_piece(board, piece, to_r, to_c, hc, hr)
    if src is None:
        return board, castling, ep
    fr, fc2 = src

    # En passant pawn removal
    if piece_upper == "P" and fc2 != to_c and board[to_r][to_c] is None:
        board[fr][to_c] = None

    # Double pawn push: record en-passant square
    if piece_upper == "P" and abs(to_r - fr) == 2:
        ep_r    = (fr + to_r) // 2
        ep_next = chr(ord("a") + to_c) + str(8 - ep_r)

    board[to_r][to_c] = promo if promo else piece
    board[fr][fc2]    = None

    # Update castling rights
    if piece == "K":
        castling = castling.replace("K","").replace("Q","")
    elif piece == "k":
        castling = castling.replace("k","").replace("q","")
    elif piece == "R":
        if fr == 7 and fc2 == 7: castling = castling.replace("K","")
        if fr == 7 and fc2 == 0: castling = castling.replace("Q","")
    elif piece == "r":
        if fr == 0 and fc2 == 7: castling = castling.replace("k","")
        if fr == 0 and fc2 == 0: castling = castling.replace("q","")

    return board, castling or "-", ep_next


# ---------------------------------------------------------------------------
# FEN grid parser (for rendering only)
# ---------------------------------------------------------------------------

def _fen_to_grid(fen):
    """Return (8x8 board_2d, side_char) from a FEN string."""
    parts = fen.strip().split()
    side  = parts[1] if len(parts) > 1 else "w"
    board = []
    for rank in parts[0].split("/"):
        row = []
        for ch in rank:
            if ch.isdigit():
                row.extend([None] * int(ch))
            else:
                row.append(ch)
        board.append(row[:8])
    return board, side


# ---------------------------------------------------------------------------
# Board drawing
# ---------------------------------------------------------------------------

def _draw_board(draw, board):
    """Draw the 8x8 board with alternating squares, pieces, border, and labels."""
    lbl_font   = _font(14)
    piece_font = _font(32)

    for row in range(8):
        for col in range(8):
            x0 = BOARD_LEFT + col * SQ_PX
            y0 = BOARD_TOP  + row * SQ_PX
            x1, y1 = x0 + SQ_PX, y0 + SQ_PX

            light = (row + col) % 2 == 0
            draw.rectangle([x0, y0, x1, y1], fill=LIGHT_SQ if light else DARK_SQ)

            piece = board[row][col] if row < len(board) and col < len(board[row]) else None
            if piece and piece in PIECE_GLYPH:
                glyph = PIECE_GLYPH[piece]
                ink   = PIECE_INK[piece]
                bb    = draw.textbbox((0, 0), glyph, font=piece_font)
                gw, gh = bb[2] - bb[0], bb[3] - bb[1]
                gx = x0 + (SQ_PX - gw) // 2 - bb[0]
                gy = y0 + (SQ_PX - gh) // 2 - bb[1]
                # Shadow so white pieces read on light squares
                if piece.isupper() and light:
                    draw.text((gx + 1, gy + 1), glyph, fill=(90, 70, 50), font=piece_font)
                draw.text((gx, gy), glyph, fill=ink, font=piece_font)

    # Board border
    draw.rectangle(
        [BOARD_LEFT, BOARD_TOP, BOARD_LEFT + BOARD_PX, BOARD_TOP + BOARD_PX],
        outline=(50, 35, 15), width=2,
    )

    # File labels  a-h  below the board
    for col, letter in enumerate("abcdefgh"):
        cx = BOARD_LEFT + col * SQ_PX + SQ_PX // 2
        cy = BOARD_TOP + BOARD_PX + 3
        bb = draw.textbbox((0, 0), letter, font=lbl_font)
        draw.text((cx - (bb[2] - bb[0]) // 2, cy), letter, fill=(70, 70, 70), font=lbl_font)

    # Rank labels  8-1  left of the board
    for row in range(8):
        label = str(8 - row)
        ry    = BOARD_TOP + row * SQ_PX + SQ_PX // 2
        bb    = draw.textbbox((0, 0), label, font=lbl_font)
        draw.text(
            (BOARD_LEFT - LABEL_PX + 2, ry - (bb[3] - bb[1]) // 2),
            label, fill=(70, 70, 70), font=lbl_font,
        )


# ---------------------------------------------------------------------------
# Right-panel info
# ---------------------------------------------------------------------------

def _draw_info(draw, puzzle_data, width=800, height=480):
    """Render the info panel to the right of the board."""
    px = BOARD_LEFT + BOARD_PX + LABEL_PX + 12
    pw = width - px - 14
    y  = 28

    # Header
    hf = _font(34)
    draw.text((px, y), "Chess Puzzle", fill=(25, 25, 25), font=hf)
    y += draw.textbbox((0, 0), "Chess Puzzle", font=hf)[3] + 10

    draw.line([(px, y), (px + pw, y)], fill=(180, 180, 180), width=1)
    y += 14

    # Side to move
    side  = puzzle_data.get("side_to_move", "White")
    color = (0, 110, 0) if side == "White" else (150, 0, 0)
    sf    = _font(26)
    draw.text((px, y), f"{side} to move", fill=color, font=sf)
    y += draw.textbbox((0, 0), f"{side} to move", font=sf)[3] + 14

    # CTA
    cf = _font(22)
    draw.text((px, y), "Find the best move!", fill=(55, 55, 55), font=cf)
    y += draw.textbbox((0, 0), "Find the best move!", font=cf)[3] + 22

    draw.line([(px, y), (px + pw, y)], fill=(200, 200, 200), width=1)
    y += 14

    # Rating
    rating = puzzle_data.get("rating", 0)
    df = _font(20)
    if rating:
        draw.text((px, y), f"Rating: {rating}", fill=(85, 85, 85), font=df)
        y += draw.textbbox((0, 0), f"Rating: {rating}", font=df)[3] + 8

    # Puzzle ID
    pid = puzzle_data.get("puzzle_id", "")
    if pid and pid != "unknown":
        draw.text((px, y), f"Puzzle: {pid}", fill=(120, 120, 120), font=df)
        y += draw.textbbox((0, 0), f"Puzzle: {pid}", font=df)[3] + 8

    # Date
    today_str = date.today().strftime("%B %-d, %Y")
    draw.text((px, y + 4), today_str, fill=(160, 160, 160), font=_font(16))

    # Attribution
    attr = "via lichess.org"
    af   = _font(13)
    ab   = draw.textbbox((0, 0), attr, font=af)
    draw.text((px + pw - (ab[2] - ab[0]), height - 18), attr, fill=(185, 185, 185), font=af)


# ---------------------------------------------------------------------------
# Fallback image
# ---------------------------------------------------------------------------

def _render_fallback(output_path, width=800, height=480):
    img  = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    _draw_board(draw, [[None] * 8 for _ in range(8)])
    px = BOARD_LEFT + BOARD_PX + LABEL_PX + 12
    draw.text((px, 80),  "Chess Puzzle",           fill=(30, 30, 30),    font=_font(28))
    draw.text((px, 130), "Puzzle unavailable.",     fill=(120, 120, 120), font=_font(18))
    draw.text((px, 156), "Check your connection.",  fill=(120, 120, 120), font=_font(18))
    img.save(output_path)
    print(f"[chess] Fallback image saved to {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def _render(puzzle_data, output_path, width=800, height=480):
    img  = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    fen  = puzzle_data.get("fen", "")
    if fen:
        board, side_char = _fen_to_grid(fen)
        puzzle_data["side_to_move"] = "White" if side_char == "w" else "Black"
    else:
        board = [[None] * 8 for _ in range(8)]
    _draw_board(draw, board)
    _draw_info(draw, puzzle_data, width, height)
    img.save(output_path)
    print(f"[chess] Saved to {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate(config):
    """Generate Chess Puzzle image. Return output path."""
    cfg         = config.get("chess_puzzle", {})
    output_path = cfg.get("output_path", "chess_display.bmp")
    puzzle_data = _load_cache()
    if not puzzle_data:
        puzzle_data = _fetch_puzzle()
        if not puzzle_data:
            return _render_fallback(output_path)
        _save_cache(puzzle_data)
    return _render(puzzle_data, output_path)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
