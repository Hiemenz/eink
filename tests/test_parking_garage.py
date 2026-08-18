"""
Unit tests for modules/parking_garage.py.
"""

import os
import sys
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import parking_garage as pg

pd = pg.pd  # real pandas, imported by the module itself


SAMPLE_API_DATA = {
    "TotalBays": 1000,
    "OccupiedBays": 400,
    "Zones": [
        {
            "Name": "Garage A",
            "TotalBays": 500,
            "OccupiedBays": 200,
            "Zones": [
                {"Name": "Level 1", "TotalBays": 100, "OccupiedBays": 50},
                {"Name": "Level 2", "TotalBays": 100, "OccupiedBays": 90},
                {"Name": "Level 1 ADA", "TotalBays": 10, "OccupiedBays": 2},
                {"Name": "Level 1 EV", "TotalBays": 5, "OccupiedBays": 1},
                {"Name": "2 Hour Timed", "TotalBays": 20, "OccupiedBays": 10},
                {"Name": "Reserved", "TotalBays": 10, "OccupiedBays": 5},
            ],
        },
        {
            "Name": "Garage B",
            "TotalBays": 500,
            "OccupiedBays": 200,
            "Zones": [],
        },
    ],
}


class TestParseGarages:
    def test_basic_fields(self):
        garages = pg._parse_garages(SAMPLE_API_DATA)
        assert len(garages) == 2
        a = garages[0]
        assert a["name"] == "Garage A"
        assert a["total"] == 500
        assert a["occupied"] == 200
        assert a["available"] == 300

    def test_filters_out_non_numbered_levels(self):
        garages = pg._parse_garages(SAMPLE_API_DATA)
        a = garages[0]
        level_names = [lv["name"] for lv in a["levels"]]
        assert "Reserved" not in level_names
        assert "2 Hour Timed" not in level_names

    def test_filters_out_ada_and_ev_levels(self):
        garages = pg._parse_garages(SAMPLE_API_DATA)
        a = garages[0]
        level_names = [lv["name"] for lv in a["levels"]]
        assert "Level 1 ADA" not in level_names
        assert "Level 1 EV" not in level_names

    def test_keeps_numbered_levels(self):
        garages = pg._parse_garages(SAMPLE_API_DATA)
        a = garages[0]
        level_names = [lv["name"] for lv in a["levels"]]
        assert "Level 1" in level_names
        assert "Level 2" in level_names

    def test_available_never_negative(self):
        data = {"Zones": [{"Name": "G", "TotalBays": 10, "OccupiedBays": 999, "Zones": []}]}
        garages = pg._parse_garages(data)
        assert garages[0]["available"] == 0

    def test_empty_zones_returns_empty_list(self):
        assert pg._parse_garages({"Zones": []}) == []
        assert pg._parse_garages({}) == []


class TestPctColor:
    def test_below_half_is_green(self):
        assert pg._pct_color(0.0) == "#2ecc71"
        assert pg._pct_color(0.49) == "#2ecc71"

    def test_between_half_and_075_is_orange(self):
        assert pg._pct_color(0.5) == "#f39c12"
        assert pg._pct_color(0.74) == "#f39c12"

    def test_075_and_above_is_red(self):
        assert pg._pct_color(0.75) == "#e74c3c"
        assert pg._pct_color(1.0) == "#e74c3c"


class TestFetchData:
    @patch("modules.parking_garage.requests.get")
    def test_success_returns_json(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"ok": True}
        mock_get.return_value = resp
        assert pg._fetch_data("http://example.com") == {"ok": True}

    @patch("modules.parking_garage.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert pg._fetch_data("http://example.com") is None

    @patch("modules.parking_garage.requests.get")
    def test_http_error_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("500")
        mock_get.return_value = resp
        assert pg._fetch_data("http://example.com") is None


class TestSaveHistory:
    def test_creates_new_file_with_expected_rows(self, tmp_path):
        history_file = str(tmp_path / "history.parquet")
        garages = pg._parse_garages(SAMPLE_API_DATA)

        with patch.object(pd.DataFrame, "to_parquet", autospec=True) as mock_to_parquet:
            pg._save_history(garages, history_file)
            assert mock_to_parquet.called
            saved_df = mock_to_parquet.call_args[0][0]

        # 2 numbered levels for Garage A + 1 TOTAL row + 1 TOTAL row for Garage B
        assert len(saved_df) == 4
        assert set(saved_df["garage_name"]) == {"Garage A", "Garage B"}
        assert "TOTAL" in set(saved_df["level"])

    def test_appends_to_existing_file(self, tmp_path):
        history_file = str(tmp_path / "history.parquet")
        garages = pg._parse_garages(SAMPLE_API_DATA)

        existing = pd.DataFrame([{
            "timestamp": datetime.now(), "garage_name": "Garage A",
            "level": "TOTAL", "total_bays": 500, "occupied_bays": 100,
        }])

        with patch("modules.parking_garage.os.path.exists", return_value=True), \
             patch.object(pd, "read_parquet", return_value=existing), \
             patch.object(pd.DataFrame, "to_parquet", autospec=True) as mock_to_parquet:
            pg._save_history(garages, history_file)
            saved_df = mock_to_parquet.call_args[0][0]

        # existing row + new rows
        assert len(saved_df) == 1 + 4

    def test_skips_when_pandas_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pg, "pd", None)
        # Should not raise even though there's nothing to do.
        pg._save_history([], str(tmp_path / "history.parquet"))


class TestGetPrediction:
    def test_returns_none_when_file_missing(self, tmp_path):
        assert pg._get_prediction(str(tmp_path / "nope.parquet"), "Garage A") is None

    def test_returns_none_with_insufficient_rows(self, tmp_path):
        history_file = str(tmp_path / "history.parquet")
        df = pd.DataFrame([
            {"timestamp": datetime.now(), "garage_name": "Garage A", "level": "TOTAL",
             "total_bays": 100, "occupied_bays": 50},
        ])
        with patch("modules.parking_garage.os.path.exists", return_value=True), \
             patch.object(pd, "read_parquet", return_value=df):
            assert pg._get_prediction(history_file, "Garage A") is None

    def test_computes_average_for_matching_dow_and_hour(self, tmp_path):
        history_file = str(tmp_path / "history.parquet")
        now = datetime.now()
        # Build >=5 rows, with >=2 matching current dow/hour for Garage A.
        rows = []
        for i in range(6):
            ts = now.replace(minute=i)  # same dow/hour as "now"
            rows.append({
                "timestamp": ts, "garage_name": "Garage A", "level": "TOTAL",
                "total_bays": 100, "occupied_bays": 50 + i,
            })
        df = pd.DataFrame(rows)
        with patch("modules.parking_garage.os.path.exists", return_value=True), \
             patch.object(pd, "read_parquet", return_value=df):
            pred = pg._get_prediction(history_file, "Garage A")
        assert pred is not None
        assert 0.0 <= pred <= 1.0

    def test_filters_by_garage_name(self, tmp_path):
        history_file = str(tmp_path / "history.parquet")
        now = datetime.now()
        rows = [
            {"timestamp": now, "garage_name": "Garage B", "level": "TOTAL",
             "total_bays": 100, "occupied_bays": 90},
        ] * 6
        df = pd.DataFrame(rows)
        with patch("modules.parking_garage.os.path.exists", return_value=True), \
             patch.object(pd, "read_parquet", return_value=df):
            # No rows at all for "Garage A"
            assert pg._get_prediction(history_file, "Garage A") is None

    def test_returns_none_when_pandas_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pg, "pd", None)
        assert pg._get_prediction(str(tmp_path / "history.parquet"), "Garage A") is None

    def test_read_error_returns_none(self, tmp_path):
        history_file = str(tmp_path / "history.parquet")
        with patch("modules.parking_garage.os.path.exists", return_value=True), \
             patch.object(pd, "read_parquet", side_effect=Exception("corrupt")):
            assert pg._get_prediction(history_file, "Garage A") is None


class TestRenderAndErrorImage:
    def test_error_image_creates_file(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        result = pg._error_image(output_path)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_render_creates_file_with_requested_dimensions(self, tmp_path):
        from PIL import Image
        output_path = str(tmp_path / "out.bmp")
        garages = pg._parse_garages(SAMPLE_API_DATA)
        result = pg._render(garages, {"TotalBays": 1000, "OccupiedBays": 400}, {}, output_path,
                             width=400, height=300)
        assert result == output_path
        img = Image.open(output_path)
        assert img.size == (400, 300)

    def test_render_with_predictions_does_not_crash(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        garages = pg._parse_garages(SAMPLE_API_DATA)
        predictions = {"Garage A": 0.62}
        result = pg._render(garages, {"TotalBays": 1000, "OccupiedBays": 400}, predictions, output_path)
        assert os.path.exists(result)

    def test_render_zero_total_bays_does_not_divide_by_zero(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        garages = [{"name": "Empty Garage", "total": 0, "occupied": 0, "available": 0, "levels": []}]
        result = pg._render(garages, {"TotalBays": 0, "OccupiedBays": 0}, {}, output_path)
        assert os.path.exists(result)

    def test_render_more_than_two_garages_only_draws_first_two(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        garages = pg._parse_garages(SAMPLE_API_DATA) + [
            {"name": "Garage C", "total": 100, "occupied": 50, "available": 50, "levels": []}
        ]
        # Two-column layout only ever draws garages[:2] -- must not raise for a 3rd.
        result = pg._render(garages, {"TotalBays": 1000, "OccupiedBays": 400}, {}, output_path)
        assert os.path.exists(result)

    def test_render_more_than_two_garages_notes_the_overflow(self, tmp_path):
        """Regression: garages beyond the first 2 used to be dropped with no
        on-screen indication at all -- now a small note flags the overflow
        instead of silently discarding it."""
        output_path = str(tmp_path / "out.bmp")
        garages = pg._parse_garages(SAMPLE_API_DATA) + [
            {"name": "Garage C", "total": 100, "occupied": 50, "available": 50, "levels": []}
        ]
        from PIL import Image

        img_before = pg._render(garages[:2], {"TotalBays": 1000, "OccupiedBays": 400}, {}, output_path)
        pixels_before = list(Image.open(img_before).convert("RGB").getdata())

        result = pg._render(garages, {"TotalBays": 1000, "OccupiedBays": 400}, {}, output_path)
        pixels_after = list(Image.open(result).convert("RGB").getdata())

        assert pixels_before != pixels_after


class TestGenerate:
    def _config(self, tmp_path):
        return {
            "parking_garage": {
                "output_path": str(tmp_path / "out.bmp"),
                "history_file": str(tmp_path / "history.parquet"),
            },
        }

    @patch("modules.parking_garage.requests.get")
    def test_fetch_failure_produces_error_image(self, mock_get, tmp_path):
        mock_get.side_effect = ConnectionError("boom")
        result = pg.generate(self._config(tmp_path))
        assert os.path.exists(result)

    @patch("modules.parking_garage.requests.get")
    def test_no_garages_produces_error_image(self, mock_get, tmp_path):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"Zones": []}
        mock_get.return_value = resp
        result = pg.generate(self._config(tmp_path))
        assert os.path.exists(result)

    @patch("modules.parking_garage.requests.get")
    def test_success_renders_and_saves_history(self, mock_get, tmp_path):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = SAMPLE_API_DATA
        mock_get.return_value = resp

        result = pg.generate(self._config(tmp_path))

        assert os.path.exists(result)
        if pd is not None:
            assert os.path.exists(str(tmp_path / "history.parquet"))
