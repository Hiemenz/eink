"""Home-state screen: your Governor, Senate and House district races in detail.

Config (election:): home_state (postal, default TN), home_district (default 5).
"""

from modules import election_common as ec


def _find_all(data, postal, district=None):
    """Every matching race (a state can have a regular and a special Senate race)."""
    out = []
    for r in data["races"]:
        s = ec.race_summary(r)
        if s["postal"] == postal and (district is None or str(s["seat"]) == str(district)):
            out.append(s)
    return out


def _card(d, config, y, h, title, s):
    d.rectangle([14, y, ec.WIDTH - 14, y + h], outline=ec.BLACK, width=2)
    hf = ec.get_font(16, bold=True, config=config)
    d.text((22, y + 4), title, fill=ec.BLACK, font=hf)
    if not s:
        d.text((22, y + 28), "No race", fill=ec.BLACK, font=ec.get_font(14, config=config))
        return
    status = ("CALLED" + (" - FLIP" if s["flip"] else "")) if s["called"] else (
        f"{s['reporting']}% reporting" if s["total"] else ec.rating_label(s["rating"]))
    sw = ec.get_font(13, config=config)
    d.text((ec.WIDTH - 22 - d.textlength(status, font=sw), y + 6), status, fill=ec.BLACK, font=sw)
    nf, pf = ec.get_font(15, bold=True, config=config), ec.get_font(14, config=config)
    ry = y + 28
    for c in (s["cands"][:3] or []):
        pct = 100 * c["votes"] / s["total"] if s["total"] else 0
        col = ec.PARTY_COLOR[c["party"]]
        d.rectangle([22, ry + 1, 36, ry + 15], fill=col, outline=ec.BLACK)
        d.text((44, ry), c["name"][:16] + (" *" if s["winner"] and s["winner"]["id"] == c["id"] else ""),
               fill=ec.BLACK, font=nf)
        bx = 230
        d.rectangle([bx, ry + 2, bx + 360, ry + 14], outline=ec.BLACK)
        d.rectangle([bx, ry + 2, bx + int(360 * pct / 100), ry + 14], fill=col)
        d.text((bx + 372, ry), f"{pct:.1f}%  {c['votes']:,}", fill=ec.BLACK, font=pf)
        ry += 22


def generate(config):
    cfg = config.get("election", {})
    state, district = cfg.get("home_state", "TN"), cfg.get("home_district", 5)
    out = cfg.get("local_output_path", "images/election_local.bmp")
    gov, sen, house = (ec.fetch_results(o, config) for o in ("governor", "senate", "house"))
    img, d, y = ec.canvas(config, f"{state} RACES", f"district {district}", stamp=ec.feed_time(sen))
    cards = []
    for title, data, dist in ((f"{state} GOVERNOR", gov, None), (f"{state} U.S. SENATE", sen, None),
                              (f"{state}-{district} U.S. HOUSE", house, district)):
        found = _find_all(data, state, dist) or [None]
        for i, race in enumerate(found):
            cards.append((title + (f" (seat {race['seat']})" if len(found) > 1 and race else ""), race))
    h = (ec.HEIGHT - y - 30) // len(cards) - 6
    for title, race in cards:
        _card(d, config, y, h, title, race)
        y += h + 8
    ec.legend(d, config)
    return ec.finish(img, out)
