"""Generate synthetic election-night results to dry-run the displays.

    uv run python tools/election_replay.py 0.4 /tmp/replay     # 40% reporting
    # then run with election.replay_dir=/tmp/replay (e.g. in bot_state.json / config)

Starts from the cached pre-election feeds in data/election_*.json and fills in
votes from each race's rating (deterministic noise per race), marks clear leaders
as called (some against the rating, i.e. flips), and updates party control.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

BASE = {"solid-dem": .66, "likely-dem": .59, "lean-dem": .53, "toss-up": .50, "tossup": .50,
        "lean-rep": .47, "likely-rep": .41, "solid-rep": .34}


def _noise(key, span):
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return (h - 0.5) * 2 * span


def fill(data, progress, office_id):
    called = {"DEM": 0, "GOP": 0, "OTH": 0}
    n_called = 0
    for r in data["races"]:
        u, meta = r["top_reporting_unit"], r["candidate_metadata"]
        rating = r.get("rating")
        dem_share = BASE.get(rating, .5) + _noise(r["nyt_id"], .10)
        expected = u.get("total_expected_vote") or 200000
        counted = int(expected * progress)
        dem_pct = dem_share
        for c in u["candidates"]:
            party = meta[c["nyt_id"]]["party"]["nyt_id"]
            share = dem_pct if party == "DEM" else (1 - dem_pct) * (.96 if party == "GOP" else .04 / max(len(u["candidates"]) - 2, 1))
            c["votes"] = {"total": int(counted * share)}
            c["leader"] = False
        u["total_votes"], u["eevp"] = counted, int(progress * 100)
        lead = max(u["candidates"], key=lambda c: c["votes"]["total"])
        lead["leader"] = True
        margin = abs(dem_share - .5) * 2
        if progress >= .6 and margin > .06:
            lp = meta[lead["nyt_id"]]["party"]["nyt_id"]
            r["outcome"]["won"] = [lead["nyt_id"]]
            expected_party = "DEM" if rating and "dem" in rating else "GOP" if rating and "rep" in rating else None
            r["outcome"]["gained"] = {"party": lp} if expected_party and lp != expected_party else None
            called[lp if lp in called else "OTH"] += 1
            n_called += 1
    office = data["partyControlData"]["results"][0]["offices"][office_id]
    office["num_races_called"] = n_called
    for pid, b in office["party_balance"].items():
        b["seats"] = b.get("no_election", 0) + called.get(pid, 0)
        b["winner"] = b["seats"] >= office["to_win"].get(pid, 10**6) and pid != "OTH"
    data["resultsMeta"]["resultsUpdatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return data


def main(progress, out):
    os.makedirs(out, exist_ok=True)
    for office, oid in (("house", "H"), ("senate", "S"), ("governor", "S")):
        with open(f"data/election_{office}.json") as f:
            data = json.load(f)
        data = fill(data, progress, oid) if office != "governor" else fill_gov(data, progress)
        with open(os.path.join(out, f"election_{office}.json"), "w") as f:
            json.dump(data, f)
    print(f"wrote replay at {progress:.0%} reporting to {out}")


def fill_gov(data, progress):
    """Governor feed has no chamber control block that matters; reuse fill() on a stub."""
    stub = data["partyControlData"]["results"][0]["offices"]
    if "S" not in stub:
        stub["S"] = {"party_balance": {}, "to_win": {}}
    return fill(data, progress, "S")


if __name__ == "__main__":
    main(float(sys.argv[1]), sys.argv[2])
