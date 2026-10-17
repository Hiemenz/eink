"""Pure helpers behind the Discord !menu / !radar / !model selectors."""
import discord_bot as D


def test_chunks_respect_discord_dropdown_limit():
    chunks = D.chunk_list(D.ALL_MODULES)
    assert all(len(c) <= D.SELECT_MAX for c in chunks)
    assert [m for c in chunks for m in c] == D.ALL_MODULES


def test_every_module_is_reachable_from_the_menu():
    view = D.ControlView({"active_module": "weather", "radar_mode": "panel",
                          "display_model": "epd7in3f"})
    offered = {o.label for sel in view.children[:-2] for o in sel.options}
    assert offered == set(D.ALL_MODULES)


def test_menu_fits_discords_five_row_limit():
    view = D.ControlView({})
    assert len(view.children) <= 5


def test_radar_and_model_choices_match_what_the_renderer_accepts():
    assert {"panel", "full", "dynamic"} <= set(D.RADAR_MODES)
    assert set(D.DISPLAY_MODELS) == {"epd7in3f", "epd7in5_V2"}


def test_current_choice_is_preselected():
    view = D.ControlView({"active_module": "stocks", "radar_mode": "dynamic",
                          "display_model": "epd7in5_V2"})
    defaults = {o.label for sel in view.children for o in sel.options if o.default}
    assert defaults == {"stocks", "dynamic", "epd7in5_V2"}
