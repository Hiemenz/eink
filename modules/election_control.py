"""Chamber-control takeover screen: 'REPUBLICANS WIN THE HOUSE' etc."""

from modules import election_common as ec

NAMES = {"DEM": "DEMOCRATS", "GOP": "REPUBLICANS", "OTH": "INDEPENDENTS"}


def generate(config):
    out = config.get("election", {}).get("control_output_path", "images/election_control.bmp")
    house, senate = ec.fetch_results("house", config), ec.fetch_results("senate", config)
    img, d, y = ec.canvas(config, "CONTROL OF CONGRESS", "", stamp=ec.feed_time(house))
    big = ec.get_font(44, bold=True, config=config)
    mid = ec.get_font(22, bold=True, config=config)
    small = ec.get_font(16, config=config)
    for label, data, oid in (("HOUSE", house, "H"), ("SENATE", senate, "S")):
        c = ec.control(data, oid)
        pb = c["party_balance"]
        win = ec.control_winner(data, oid)
        h = 190
        col = ec.PARTY_COLOR[win] if win else ec.WHITE
        d.rectangle([14, y, ec.WIDTH - 14, y + h], fill=col, outline=ec.BLACK, width=3)
        fg = ec.WHITE if win else ec.BLACK
        head = f"{NAMES[win]} WIN THE {label}" if win else f"{label}: CONTROL UNDECIDED"
        d.text(((ec.WIDTH - d.textlength(head, font=big)) // 2, y + 18), head, fill=fg, font=big)
        tally = f"DEM {pb['DEM']['seats']}   -   GOP {pb['GOP']['seats']}"
        d.text(((ec.WIDTH - d.textlength(tally, font=mid)) // 2, y + 90), tally, fill=fg, font=mid)
        need = c["to_win"]["DEM"]
        sub = f"{need} needed for control  |  {c['num_races_called']} races called"
        d.text(((ec.WIDTH - d.textlength(sub, font=small)) // 2, y + 130), sub, fill=fg, font=small)
        y += h + 12
    return ec.finish(img, out)
