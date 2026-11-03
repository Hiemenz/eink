"""One-time builder for data/us_districts.json (120th Congress districts).

Reads the Census TIGER2026 per-state shapefiles (cd120), projects them with an
Albers equal-area conic (Alaska/Hawaii as insets), simplifies, and writes a small
JSON the election_house module draws.

    uv run --with pyshp --with shapely --with pyproj python tools/build_district_map.py <dir>

<dir> holds one unzipped folder per state (sNN/tl_2026_NN_cd120.shp), fetched from
https://www2.census.gov/geo/tiger/TIGER2026/CD/ . Re-run if the maps change again.
"""

import glob
import json
import sys

import shapefile
from pyproj import Transformer
from shapely.affinity import scale as sscale, translate
from shapely.geometry import shape
from shapely.ops import transform, unary_union

FIPS_POSTAL = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT",
    "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL",
    "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD",
    "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE",
    "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV",
    "55": "WI", "56": "WY",
}
PROJ = {
    "conus": "+proj=aea +lat_1=29.5 +lat_2=45.5 +lat_0=37.5 +lon_0=-96 +datum=NAD83",
    "AK": "+proj=aea +lat_1=55 +lat_2=65 +lat_0=50 +lon_0=-154 +datum=NAD83",
    "HI": "+proj=aea +lat_1=8 +lat_2=18 +lat_0=3 +lon_0=-157 +datum=NAD83",
}
OUT_WIDTH = 1600  # output units across the lower 48


def region(postal):
    return postal if postal in ("AK", "HI") else "conus"


def main(root):
    geoms = {}  # (postal, seat) -> shapely geometry in meters, per-region projection
    for fips, postal in FIPS_POSTAL.items():
        files = glob.glob(f"{root}/s{fips}/*.shp")
        if not files:
            sys.exit(f"missing shapefile for {postal} ({fips})")
        tf = Transformer.from_crs("EPSG:4269", PROJ[region(postal)], always_xy=True)
        for sr in shapefile.Reader(files[0]).shapeRecords():
            seat = sr.record["CD120FP"]
            if seat in ("ZZ",):  # water-only placeholder
                continue
            geoms[(postal, seat)] = transform(tf.transform, shape(sr.shape.__geo_interface__))

    conus = unary_union([g for (p, _), g in geoms.items() if region(p) == "conus"])
    cx0, cy0, cx1, cy1 = conus.bounds
    k = OUT_WIDTH / (cx1 - cx0)

    def place(postal, g):
        if postal == "AK":
            g = sscale(g, 0.35, 0.35, origin=(0, 0))
            x0, y0, _, _ = g.bounds
            return translate(g, cx0 + 0.02 * (cx1 - cx0) - x0, cy0 - y0 - 0.02 * (cy1 - cy0))
        if postal == "HI":
            x0, y0, _, _ = g.bounds
            return translate(g, cx0 + 0.22 * (cx1 - cx0) - x0, cy0 - y0 - 0.02 * (cy1 - cy0))
        return g

    def finish(g, tol):
        g = sscale(g, k, -k, origin=(0, 0))      # flip y for screen coords
        g = g.simplify(tol, preserve_topology=True)
        return g

    placed = {key: place(key[0], g) for key, g in geoms.items()}
    allb = unary_union(list(placed.values())).bounds
    minx, miny = allb[0] * k, -allb[3] * k       # screen-space origin (y flipped)

    def rings(g, min_area):
        polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
        out = []
        for p in polys:
            if p.area < min_area:
                continue
            out.append([round(c - o, 1) for xy in p.exterior.coords for c, o in zip(xy, (minx, miny))])
        return out

    districts, states = [], {}
    for (postal, seat), g in sorted(placed.items()):
        fg = finish(g, 0.6)
        r = rings(fg, 1.5)
        if r:
            districts.append([postal, int(seat), r])
        states.setdefault(postal, []).append(fg)
    out_states = {p: rings(unary_union(gs), 1.5) for p, gs in states.items()}
    w = round((allb[2] - allb[0]) * k, 1)
    h = round((allb[3] - allb[1]) * k, 1)
    doc = {"width": w, "height": h, "districts": districts, "states": out_states}
    with open("data/us_districts.json", "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    print(f"{len(districts)} districts, {w}x{h}")


if __name__ == "__main__":
    main(sys.argv[1])
