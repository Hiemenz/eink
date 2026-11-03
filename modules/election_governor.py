"""Governor display: race-call banner, flips, map or list."""

from modules import election_common as ec


def generate(config):
    out = config.get("election_governor", {}).get("output_path", "images/election_governor.bmp")
    data = ec.fetch_results("governor", config)
    sums = [ec.race_summary(r) for r in data["races"]]
    called = sum(s["called"] for s in sums)
    img, d, y = ec.canvas(config, "GOVERNORS", f"{called}/{len(sums)} called{ec.flip_text(sums)}",
                          stamp=ec.feed_time(data))
    recent = ec.track_calls(sums, lambda s: f"{s['state']} Governor", config, "governor")
    y = ec.banner(d, config, y, recent)
    info, rows = {}, []
    for s in sorted(sums, key=lambda s: s["state"]):
        lead = s["winner"] or (s["cands"][0] if s["total"] else None)
        col = ec.PARTY_COLOR[lead["party"]] if lead else ec.rating_color(s["rating"])
        solid = s["called"] or not s["total"]
        rows.append({"color": col, "solid": solid, "label": s["state"],
                     "detail": (f"{lead['name']} {100 * lead['votes'] / s['total']:.0f}%"
                                + (" WON" + (" FLIP" if s["flip"] else "") if s["called"] else f" ({s['reporting']}% in)"))
                     if lead and s["total"] else ec.rating_label(s["rating"])})
        info[s["postal"]] = {"color": col, "solid": solid,
                             "sub": f"{100 * lead['votes'] / s['total']:.0f}%" if lead and s["total"] else ""}
    if ec.view(config) == "list":
        ec.race_list(d, config, y + 4, ec.HEIGHT - 30, rows)
    else:
        ec.state_map(d, config, y + 4, ec.HEIGHT - 30, info)
    ec.legend(d, config)
    return ec.finish(img, out)
