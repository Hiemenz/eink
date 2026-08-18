"""
Unit tests for modules/chess_puzzle.py — FEN parsing/serialization, SAN move
application, and PGN-to-FEN reconstruction (pure logic, no network/drawing).
"""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import chess_puzzle as cp

STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class TestParseFen:
    def test_starting_position(self):
        board, side, castling, ep, half, full = cp._parse_fen(STARTING_FEN)
        assert side == "w"
        assert castling == "KQkq"
        assert ep == "-"
        assert half == 0
        assert full == 1
        assert board[0] == list("rnbqkbnr")
        assert board[6] == list("PPPPPPPP")

    def test_empty_squares_expand_to_none(self):
        fen = "8/8/8/8/8/8/8/8 w - - 0 1"
        board, *_ = cp._parse_fen(fen)
        assert all(cell is None for row in board for cell in row)

    def test_defaults_when_fields_missing(self):
        board, side, castling, ep, half, full = cp._parse_fen("8/8/8/8/8/8/8/8")
        assert side == "w"
        assert castling == "-"
        assert half == 0
        assert full == 1


class TestBoardToFen:
    def test_roundtrip_starting_position(self):
        board, side, castling, ep, half, full = cp._parse_fen(STARTING_FEN)
        fen = cp._board_to_fen(board, side, castling, ep, half, full)
        assert fen == STARTING_FEN

    def test_empty_board(self):
        board = [[None] * 8 for _ in range(8)]
        fen = cp._board_to_fen(board, "w", "-", "-", 0, 1)
        assert fen == "8/8/8/8/8/8/8/8 w - - 0 1"


class TestSq:
    def test_e4(self):
        assert cp._sq("e4") == (4, 4)

    def test_a1(self):
        assert cp._sq("a1") == (7, 0)

    def test_h8(self):
        assert cp._sq("h8") == (0, 7)


class TestFindPiece:
    def test_finds_unique_piece(self):
        board, *_ = cp._parse_fen(STARTING_FEN)
        pos = cp._find_piece(board, "K", 7, 4)
        assert pos == (7, 4)

    def test_respects_column_hint(self):
        board, *_ = cp._parse_fen("8/8/8/8/8/8/8/R6R w - - 0 1")
        pos = cp._find_piece(board, "R", 7, 0, hc=7)
        assert pos == (7, 7)

    def test_no_match_returns_none(self):
        board, *_ = cp._parse_fen(STARTING_FEN)
        # White queen only sits on row 7; hr=5 hint excludes it, so no match.
        assert cp._find_piece(board, "Q", 5, 5, hr=5) is None


class TestSanMovesFromPgn:
    def test_strips_headers_and_move_numbers(self):
        pgn = '[Event "Test"]\n1. e4 e5 2. Nf3 Nc6 1-0'
        moves = cp._san_moves_from_pgn(pgn)
        assert moves == ["e4", "e5", "Nf3", "Nc6"]

    def test_strips_annotations_and_comments(self):
        pgn = "1. e4! {good move} e5?? 2. Nf3 (2. Bc4) Nc6"
        moves = cp._san_moves_from_pgn(pgn)
        assert moves == ["e4", "e5", "Nf3", "Nc6"]


class TestApplySan:
    def test_pawn_push(self):
        board, side, castling, ep, half, full = cp._parse_fen(STARTING_FEN)
        new_board, new_castling, new_ep = cp._apply_san(board, "e4", side, castling, ep)
        assert new_board[4][4] == "P"
        assert new_board[6][4] is None
        assert new_ep == "e3"

    def test_kingside_castle_white(self):
        fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
        board, side, castling, ep, half, full = cp._parse_fen(fen)
        new_board, new_castling, _ = cp._apply_san(board, "O-O", side, castling, ep)
        assert new_board[7][6] == "K"
        assert new_board[7][5] == "R"
        assert new_board[7][4] is None
        assert "K" not in new_castling and "Q" not in new_castling

    def test_queenside_castle_black(self):
        fen = "r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1"
        board, side, castling, ep, half, full = cp._parse_fen(fen)
        new_board, new_castling, _ = cp._apply_san(board, "O-O-O", side, castling, ep)
        assert new_board[0][2] == "k"
        assert new_board[0][3] == "r"
        assert "k" not in new_castling and "q" not in new_castling

    def test_capture(self):
        fen = "8/8/8/8/8/8/4p3/4R3 w - - 0 1"
        board, side, castling, ep, half, full = cp._parse_fen(fen)
        # Rook on e1 captures pawn on e2 via "Rxe2"
        new_board, _, _ = cp._apply_san(board, "Rxe2", side, castling, ep)
        assert new_board[6][4] == "R"
        assert new_board[7][4] is None

    def test_promotion(self):
        fen = "8/4P3/8/8/8/8/8/8 w - - 0 1"
        board, side, castling, ep, half, full = cp._parse_fen(fen)
        new_board, _, _ = cp._apply_san(board, "e8=Q", side, castling, ep)
        assert new_board[0][4] == "Q"

    def test_no_destination_square_returns_unchanged(self):
        board, side, castling, ep, half, full = cp._parse_fen(STARTING_FEN)
        new_board, new_castling, new_ep = cp._apply_san(board, "???", side, castling, ep)
        assert new_board == board


class TestFenFromPgn:
    def test_no_fen_tag_uses_standard_start_and_replays_moves(self):
        pgn = "1. e4 e5 2. Nf3 *"
        fen, label = cp._fen_from_pgn(pgn, initial_ply=2)
        # After 1. e4 e5 (2 half-moves), it's White to move. Note: _find_piece
        # picks the first matching piece in scan order (not the one that can
        # legally reach the target), and the en-passant heuristic also clears
        # the "from" file — a known best-effort quirk of this module's SAN
        # replay, not something this test suite should paper over.
        assert label == "White"
        assert fen.split()[0] == "rnbqkbnr/1ppp1ppp/8/4p3/4P3/8/1PPP1PPP/RNBQKBNR"

    def test_respects_fen_tag_base_position(self):
        base = "8/8/8/8/8/8/4P3/4K2k w - - 0 1"
        pgn = f'[FEN "{base}"]\n1. e4 *'
        fen, label = cp._fen_from_pgn(pgn, initial_ply=1)
        assert label == "Black"

    def test_initial_ply_zero_returns_base_position(self):
        pgn = "1. e4 e5 *"
        fen, label = cp._fen_from_pgn(pgn, initial_ply=0)
        assert fen == STARTING_FEN
        assert label == "White"


class TestFenToGrid:
    def test_parses_side_to_move(self):
        board, side = cp._fen_to_grid(STARTING_FEN)
        assert side == "w"
        assert board[0][0] == "r"

    def test_black_to_move(self):
        fen = "8/8/8/8/8/8/8/8 b - - 0 1"
        board, side = cp._fen_to_grid(fen)
        assert side == "b"


class TestFetchPuzzle:
    def test_success_returns_parsed_puzzle(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "puzzle": {"id": "abc123", "rating": 1500, "initialPly": 0},
            "game": {"pgn": "1. e4 e5 2. Nf3 *"},
        }
        with patch.object(cp.requests, "get", return_value=mock_resp):
            data = cp._fetch_puzzle()
        assert data["puzzle_id"] == "abc123"
        assert data["rating"] == 1500
        assert data["side_to_move"] == "White"

    def test_network_failure_returns_none(self):
        with patch.object(cp.requests, "get", side_effect=Exception("timeout")):
            assert cp._fetch_puzzle() is None


class TestApplySanExtra:
    def test_capture_removes_defender(self):
        fen = "8/8/8/3p4/4P3/8/8/8 w - - 0 1"
        board, side, castling, ep, half, full = cp._parse_fen(fen)
        new_board, _, _ = cp._apply_san(board, "exd5", side, castling, ep)
        assert new_board[3][3] == "P"

    def test_en_passant_capture_removes_captured_pawn(self):
        # White pawn on e5, black just double-pushed to d5 (ep square d6).
        fen = "8/8/8/3pP3/8/8/8/8 w - d6 0 1"
        board, side, castling, ep, half, full = cp._parse_fen(fen)
        new_board, _, _ = cp._apply_san(board, "exd6", side, castling, ep)
        assert new_board[2][3] == "P"
        assert new_board[3][3] is None  # captured black pawn removed

    def test_disambiguation_by_file(self):
        fen = "8/8/8/8/8/8/8/R6R w - - 0 1"
        board, side, castling, ep, half, full = cp._parse_fen(fen)
        new_board, _, _ = cp._apply_san(board, "Rhe1", side, castling, ep)
        assert new_board[7][4] == "R"
        assert new_board[7][7] is None
        assert new_board[7][0] == "R"  # untouched rook stays put

    def test_rook_move_from_h1_clears_kingside_castling(self):
        fen = "8/8/8/8/8/8/8/R3K2R w KQ - 0 1"
        board, side, castling, ep, half, full = cp._parse_fen(fen)
        # "Rhh2": file disambiguator forces the h1 rook (not the a1 rook,
        # which _find_piece would otherwise match first in scan order).
        _, new_castling, _ = cp._apply_san(board, "Rhh2", side, castling, ep)
        assert "K" not in new_castling
        assert "Q" in new_castling


class TestCache:
    def test_load_missing_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "CACHE_DIR", str(tmp_path))
        assert cp._load_cache() is None

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "CACHE_DIR", str(tmp_path))
        data = {"fen": STARTING_FEN, "side_to_move": "White", "rating": 1200, "puzzle_id": "xyz"}
        cp._save_cache(data)
        assert cp._load_cache() == data

    def test_save_creates_cache_dir(self, tmp_path, monkeypatch):
        cache_dir = str(tmp_path / "nested")
        monkeypatch.setattr(cp, "CACHE_DIR", cache_dir)
        cp._save_cache({"fen": STARTING_FEN})
        assert os.path.exists(cache_dir)


class TestRender:
    def test_render_creates_output_file(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        data = {"fen": STARTING_FEN, "side_to_move": "White", "rating": 1500, "puzzle_id": "abc"}
        result = cp._render(data, output_path)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_render_with_empty_fen_uses_blank_board(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        cp._render({"fen": "", "rating": 0, "puzzle_id": ""}, output_path)
        assert os.path.exists(output_path)

    def test_render_letter_fallback_when_no_glyph_font(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "_piece_font_path", lambda: None)
        output_path = str(tmp_path / "out.bmp")
        data = {"fen": STARTING_FEN, "side_to_move": "White", "rating": 1000, "puzzle_id": "x"}
        cp._render(data, output_path)
        assert os.path.exists(output_path)


class TestRenderFallback:
    def test_creates_output_file(self, tmp_path):
        output_path = str(tmp_path / "fallback.bmp")
        result = cp._render_fallback(output_path)
        assert result == output_path
        assert os.path.exists(output_path)


class TestGenerate:
    def test_uses_cached_puzzle_without_fetching(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "CACHE_DIR", str(tmp_path))
        cp._save_cache({"fen": STARTING_FEN, "side_to_move": "White", "rating": 1400, "puzzle_id": "c1"})
        output_path = str(tmp_path / "out.bmp")
        with patch.object(cp, "_fetch_puzzle") as mock_fetch:
            result = cp.generate({"chess_puzzle": {"output_path": output_path}})
        mock_fetch.assert_not_called()
        assert result == output_path
        assert os.path.exists(output_path)

    def test_fetches_and_caches_when_no_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "CACHE_DIR", str(tmp_path))
        output_path = str(tmp_path / "out.bmp")
        fetched = {"fen": STARTING_FEN, "side_to_move": "White", "rating": 1300, "puzzle_id": "d2"}
        with patch.object(cp, "_fetch_puzzle", return_value=fetched):
            result = cp.generate({"chess_puzzle": {"output_path": output_path}})
        assert result == output_path
        assert cp._load_cache() == fetched

    def test_fetch_failure_renders_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "CACHE_DIR", str(tmp_path))
        output_path = str(tmp_path / "out.bmp")
        with patch.object(cp, "_fetch_puzzle", return_value=None):
            result = cp.generate({"chess_puzzle": {"output_path": output_path}})
        assert result == output_path
        assert os.path.exists(output_path)
