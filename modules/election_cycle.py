"""Rotate the election screens (House/Senate/Governor map+list, home-state races,
poll-closing schedule while any polls are open).

The rotation only advances when a feed reports newer results (or a group of
polls closes); when nothing changed it re-renders the same screen, which is
byte-identical, so main.py skips the ~50 s panel refresh. All screens render to
one shared file so main's unchanged-image check tracks what is on the panel.

`election.pin` (set from Discord) locks the display to one screen, e.g.
"senate:list", "local", "polls", "control", "voting".
"""

import importlib
import json
import os
from datetime import datetime, timezone

from modules import election_common as ec
from modules import election_polls
from utils import get_logger

logger = get_logger("election_cycle")
OFFICES = ("house", "senate", "governor")
BASE = [(o, v) for o in OFFICES for v in ("map", "list")] + [("local", "map"), ("voting", "map")]
STATE_FILE = "data/election_cycle_state.json"
OUTPUT = "images/election_cycle.bmp"


def _load():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"index": -1, "versions": {}}


def _parse_pin(pin):
    name, _, view = str(pin).partition(":")
    return name.replace("election_", ""), view or "map"


def generate(config):
    ecfg = config.get("election", {})
    state, versions, senate = _load(), {}, None
    pin = ecfg.get("pin")
    for o in OFFICES:
        try:
            data = ec.fetch_results(o, config)
            versions[o] = data["resultsMeta"]["resultsUpdatedAt"]
            senate = data if o == "senate" else senate
        except Exception as exc:
            logger.warning("Feed %s unavailable: %s", o, exc)
            versions[o] = state["versions"].get(o)
    order = list(BASE)
    ctrl = ""
    for o, oid in (("house", "H"), ("senate", "S")):
        try:
            w = ec.control_winner(ec.fetch_results(o, config), oid)
        except Exception:
            w = None
        ctrl += f"{oid}:{w or '-'},"
    has_control = any(part.split(":")[1] != "-" for part in ctrl.strip(",").split(","))
    if has_control:
        order.append(("control", "map"))
    versions["control"] = ctrl
    if senate is not None and election_polls.polls_open(senate):
        order.append(("polls", "map"))
        now = datetime.now(timezone.utc)
        versions["polls_closed"] = sum(1 for ts, _ in election_polls.groups(senate) if ts <= now)
    if versions != state["versions"]:
        state["index"] = (state["index"] + 1) % len(order)
        if has_control and versions["control"] != state["versions"].get("control"):
            state["index"] = order.index(("control", "map"))  # a chamber was just called: show it now
        state["versions"] = versions
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    name, view = _parse_pin(pin) if pin else order[max(state["index"], 0) % len(order)]
    logger.info("Election display: %s (%s)%s", name, view, " [pinned]" if pin else "")
    shared = ecfg.get("output_path", OUTPUT)
    cfg = dict(config, _election_view=view)
    cfg[f"election_{name}"] = {"output_path": shared}
    cfg["election"] = dict(ecfg, local_output_path=shared, polls_output_path=shared,
                           control_output_path=shared, voting_output_path=shared)
    return importlib.import_module(f"modules.election_{name}").generate(cfg)
