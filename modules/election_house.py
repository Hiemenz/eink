"""House election display: control bar, rating boxes, closest/toss-up races."""

from modules import election_common as ec


def _box(d, config, x, y, w, h, col, big, label):
    d.rectangle([x, y, x + w, y + h], fill=col, outline=ec.BLACK, width=2)
    fg = ec.BLACK if col == ec.YELLOW else ec.WHITE
    bf, lf = ec.get_font(46, bold=True, config=config), ec.get_font(15, bold=True, config=config)
    d.text((x + (w - d.textlength(str(big), font=bf)) // 2, y + 4), str(big), fill=fg, font=bf)
    d.text((x + (w - d.textlength(label, font=lf)) // 2, y + h - 24), label, fill=fg, font=lf)


def _key_races(d, config, sums, x, y):
    """Side panel: closest uncalled races (or toss-ups before results)."""
    f, sf = ec.get_font(14, bold=True, config=config), ec.get_font(13, config=config)
    live = [s for s in sums if s["total"] and not s["called"] and len(s["cands"]) > 1]
    if live:
        live.sort(key=lambda s: (s["cands"][0]["votes"] - s["cands"][1]["votes"]) / s["total"])
        title, rows = "CLOSEST RACES", live
    else:
        rows = [s for s in sums if "toss" in (s["rating"] or "")]
        title = "TOSS-UP RACES"
    d.text((x, y), title, fill=ec.BLACK, font=f)
    y += 22
    for s in rows[:int((ec.HEIGHT - 34 - y) // 20)]:
        lead = s["cands"][0] if s["total"] else None
        col = ec.PARTY_COLOR[lead["party"]] if lead else ec.YELLOW
        d.rectangle([x, y + 2, x + 12, y + 14], fill=col, outline=ec.BLACK)
        txt = f"{s['postal']}-{s['seat']}"
        if lead:
            txt += f"  {100 * lead['votes'] / s['total']:.0f}% ({s['reporting']}% in)"
        d.text((x + 18, y), txt, fill=ec.BLACK, font=sf)
        y += 20


def generate(config):
    out = config.get("election_house", {}).get("output_path", "images/election_house.bmp")
    data = ec.fetch_results("house", config)
    c = ec.control(data, "H")
    pb = c["party_balance"]
    sums = [ec.race_summary(r) for r in data["races"]]
    img, d, y = ec.canvas(config, "U.S. HOUSE", f"{c['num_races_called']}/435 called{ec.flip_text(sums)}",
                          stamp=ec.feed_time(data))
    recent = ec.track_calls(sums, lambda s: f"{s['postal']}-{s['seat']} House")
    dem, gop = pb["DEM"]["seats"], pb["GOP"]["seats"]
    pre = not (dem or gop)

    # Projection: winner | leader | rating for every district
    proj = {"DEM": 0, "GOP": 0}
    for s in sums:
        lead = s["winner"] or (s["cands"][0] if s["total"] else None)
        party = lead["party"] if lead else (
            "DEM" if "dem" in (s["rating"] or "") else "GOP" if "rep" in (s["rating"] or "") else None)
        if party in proj:
            proj[party] += 1
    if pre:
        dem, gop = proj["DEM"], proj["GOP"]
    need = c["to_win"]["DEM"]
    y = ec.compact_bar(d, config, y + 8, dem, gop, pb["OTH"]["seats"], need,
                       ec.balance_text(dem, gop, proj["DEM"], proj["GOP"], need, need))
    y = ec.banner(d, config, y, recent)
    # Per-district shading: winner | leader | pre-election rating
    info = {}
    for s in sums:
        lead = s["winner"] or (s["cands"][0] if s["total"] else None)
        col = ec.PARTY_COLOR[lead["party"]] if lead else (
            ec.rating_color(s["rating"]) if s["rating"] else None)   # unrated: stay white
        info[(s["postal"], int(s["seat"] or 1))] = {"color": col, "solid": s["called"]}
    if ec.view(config) == "list":
        def key(s):
            if s["total"] and len(s["cands"]) > 1:
                return (0, (s["cands"][0]["votes"] - s["cands"][1]["votes"]) / s["total"])
            return (1, 0 if "toss" in (s["rating"] or "") else 1 if "lean" in (s["rating"] or "") else 2)
        rows = []
        for s in sorted((s for s in sums if not s["called"]), key=key)[:34]:
            lead = s["cands"][0] if s["total"] else None
            col = ec.PARTY_COLOR[lead["party"]] if lead else ec.rating_color(s["rating"])
            rows.append({"color": col, "solid": False, "label": f"{s['postal']}-{s['seat']}",
                         "detail": (f"{lead['name']} {100 * lead['votes'] / s['total']:.0f}% ({s['reporting']}% in)"
                                    if lead else ec.rating_label(s["rating"]))})
        ec.race_list(d, config, y + 2, ec.HEIGHT - 30, rows)
    else:
        ec.district_map(img, d, config, 14, y + 2, 560, ec.HEIGHT - 30, info)
        _key_races(d, config, sums, 576, y + 4)
    ec.legend(d, config, "solid = called  |  striped = leading / rated" if ec.view(config) != "list" else None)
    return ec.finish(img, out)
