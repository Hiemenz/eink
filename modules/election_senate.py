"""Senate display: seat bar, balance of power, race-call banner, map or list."""

from modules import election_common as ec


def generate(config):
    out = config.get("election_senate", {}).get("output_path", "images/election_senate.bmp")
    data = ec.fetch_results("senate", config)
    c = ec.control(data, "S")
    sums = [ec.race_summary(r) for r in data["races"]]
    img, d, y = ec.canvas(config, "U.S. SENATE",
                          f"{c['num_races_called']}/{c['open_seats']} called{ec.flip_text(sums)}",
                          stamp=ec.feed_time(data))
    pb = c["party_balance"]
    label = lambda s: f"{s['state']}{' ' + s['seat'] if s['postal'] in ('GA', 'FL', 'NE') and s['seat'] else ''} Senate"  # noqa: E731
    recent = ec.track_calls(sums, label, config, "senate")

    # Projection: seats not up + (winner | leader | rating) for every race
    proj = {"DEM": pb["DEM"]["no_election"], "GOP": pb["GOP"]["no_election"]}
    rows, info = [], {}
    for s in sorted(sums, key=lambda s: s["state"]):
        lead = s["winner"] or (s["cands"][0] if s["total"] else None)
        party = lead["party"] if lead else (
            "DEM" if "dem" in (s["rating"] or "") else "GOP" if "rep" in (s["rating"] or "") else None)
        if party in proj:
            proj[party] += 1
        col = ec.PARTY_COLOR[lead["party"]] if lead else ec.rating_color(s["rating"])
        solid = s["called"] or not s["total"]
        rows.append({"color": col, "solid": solid, "label": label(s).replace(" Senate", ""),
                     "detail": (f"{lead['name']} {100 * lead['votes'] / s['total']:.0f}%"
                                + (" WON" + (" FLIP" if s["flip"] else "") if s["called"] else f" ({s['reporting']}% in)"))
                     if lead and s["total"] else ec.rating_label(s["rating"])})
        info[s["postal"]] = {"color": col, "solid": solid,
                             "sub": lead["name"][:7] if lead else ""}
    y = ec.compact_bar(d, config, y + 8, pb["DEM"]["seats"], pb["GOP"]["seats"], pb["OTH"]["seats"],
                       c["to_win"]["DEM"],
                       ec.balance_text(pb["DEM"]["seats"], pb["GOP"]["seats"], proj["DEM"], proj["GOP"],
                                       c["to_win"]["DEM"], c["to_win"]["GOP"] + 1))
    y = ec.banner(d, config, y, recent)
    if ec.view(config) == "list":
        ec.race_list(d, config, y + 2, ec.HEIGHT - 30, rows)
    else:
        ec.state_map(d, config, y + 2, ec.HEIGHT - 30, info)
    ec.legend(d, config)
    return ec.finish(img, out)
