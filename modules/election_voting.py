"""Voting info: when polls close at home, how much of the vote is counted,
and a QR code to look up your polling place.

Config (election:): home_state (default TN), voting_url (default vote.gov).
The image only changes when a count or closing-time state changes (no ticking clock).
"""

from datetime import datetime, timezone

import qrcode
from PIL import Image
import qrcode.constants

from modules import election_common as ec

DEFAULT_URL = "https://www.vote.gov/"
TN_URL = "https://tnmap.tn.gov/voterlookup/"


def _qr(url, size):
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=4, border=2)
    q.add_data(url)
    q.make(fit=True)
    return q.make_image(fill_color="black", back_color="white").convert("RGB").resize((size, size), Image.NEAREST)


def counted(data):
    """(votes_counted, expected_total) summed over all races in a feed."""
    got = exp = 0
    for r in data["races"]:
        u = r["top_reporting_unit"]
        got += u.get("total_votes") or 0
        exp += u.get("total_expected_vote") or 0
    return got, exp


def generate(config):
    cfg = config.get("election", {})
    state = cfg.get("home_state", "TN")
    url = cfg.get("voting_url") or (TN_URL if state == "TN" else DEFAULT_URL)
    out = cfg.get("voting_output_path", "images/election_voting.bmp")
    gov = ec.fetch_results("governor", config)
    sen = ec.fetch_results("senate", config)
    img, d, y = ec.canvas(config, "VOTING INFO", state, stamp=ec.feed_time(sen))
    now = datetime.now(timezone.utc)
    close = next((datetime.fromisoformat(t["poll_closing_time"].replace("Z", "+00:00"))
                  for t in sen.get("pollClosingTimes", [])
                  if t["reporting_unit"]["state_postal"] == state), None)
    big, mid, small = (ec.get_font(34, bold=True, config=config), ec.get_font(20, bold=True, config=config),
                       ec.get_font(16, config=config))
    if close is None:
        line = "Poll hours: see your state election site"
    elif close > now:
        line = f"{state} polls close {close.astimezone().strftime('%-I:%M %p %Z')}"
    else:
        line = f"{state} polls are CLOSED"
    d.rectangle([14, y, ec.WIDTH - 14, y + 70], fill=ec.YELLOW if close and close > now else ec.BLACK,
                outline=ec.BLACK, width=3)
    fg = ec.BLACK if close and close > now else ec.WHITE
    d.text((28, y + 16), line, fill=fg, font=big)
    y += 90
    # Vote counted so far (statewide governor race if there is one, else Senate)
    src = gov if any(r["top_reporting_unit"]["state_postal"] == state for r in gov["races"]) else sen
    race = next((r for r in src["races"] if r["top_reporting_unit"]["state_postal"] == state), None)
    d.text((14, y), f"{state} VOTE COUNTED", fill=ec.BLACK, font=mid)
    if race:
        u = race["top_reporting_unit"]
        got, exp = u.get("total_votes") or 0, u.get("total_expected_vote") or 0
        pct = min(100 * got / exp, 100) if exp else 0
        d.rectangle([14, y + 32, 14 + 500, y + 62], outline=ec.BLACK, width=3)
        d.rectangle([14, y + 32, 14 + int(500 * pct / 100), y + 62], fill=ec.BLUE)
        d.text((530, y + 36), f"{pct:.0f}%  {got:,}", fill=ec.BLACK, font=small)
    else:
        d.text((14, y + 32), "No statewide race", fill=ec.BLACK, font=small)
    y += 84
    ng, ne = counted(ec.fetch_results("house", config))
    d.text((14, y), "NATIONAL HOUSE VOTE COUNTED", fill=ec.BLACK, font=mid)
    pct = min(100 * ng / ne, 100) if ne else 0
    d.rectangle([14, y + 32, 14 + 500, y + 62], outline=ec.BLACK, width=3)
    d.rectangle([14, y + 32, 14 + int(500 * pct / 100), y + 62], fill=ec.RED)
    d.text((530, y + 36), f"{pct:.0f}%", fill=ec.BLACK, font=small)
    # QR to the polling place / voter lookup
    qr = _qr(url, 130)
    img.paste(qr, (ec.WIDTH - 14 - 130, ec.HEIGHT - 30 - 130 - 8))
    d.text((14, ec.HEIGHT - 30 - 52), "Find your polling place / check your registration:",
           fill=ec.BLACK, font=small)
    d.text((14, ec.HEIGHT - 30 - 30), url, fill=ec.BLACK, font=ec.get_font(15, bold=True, config=config))
    ec.legend(d, config)
    return ec.finish(img, out)
