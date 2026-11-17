"""Render the Trusted Train header to HTML, screenshot it, and measure it in headless Chrome.

Talks to Chromium over the DevTools Protocol on a pipe (no extra dependencies).

    uv run python tools/train_preview.py shot --theme fall --mode light -o /tmp/fall.png
    uv run python tools/train_preview.py perf --theme christmas --mode dark --weekday 0
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.trusted_train import train_html  # noqa: E402

_THEME_DATES = {
    "spring": (2026, 4, 15),
    "summer": (2026, 7, 15),
    "fall": (2026, 10, 14),
    "winter": (2027, 2, 3),
    "christmas": (2026, 12, 16),
}


def render_page(args) -> str:
    y, m, d = _THEME_DATES.get(args.theme, (2026, 10, 14))
    day = dt.date(y, m, d)
    if args.date:
        day = dt.date.fromisoformat(args.date)
    if args.weekday is not None:
        while day.weekday() != args.weekday:
            day += dt.timedelta(days=1)
    now = dt.datetime.combine(day, dt.time(args.hour, 20))
    body = train_html(
        args.title,
        mode=args.mode,
        theme=args.theme,
        now=now,
        seed=args.seed,
        still=args.still,
        event=args.event,
    )
    bg = "#0e1117" if args.mode == "dark" else "#ffffff"
    return (
        "<!doctype html><meta charset='utf-8'><body style='margin:0;padding:12px;background:%s'>%s"
        % (bg, body)
    )


class Chrome:
    """Minimal DevTools Protocol client over --remote-debugging-pipe."""

    def __init__(self, width: int, height: int):
        exe = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
        if not exe:
            raise SystemExit("No Chromium/Chrome found on PATH")
        self.size = (width, height)
        self.profile = tempfile.mkdtemp(prefix="tt-chrome-")
        to_r, to_w = os.pipe()
        from_r, from_w = os.pipe()
        self.proc = subprocess.Popen(
            [
                exe,
                "--headless=new",
                "--remote-debugging-pipe",
                "--no-first-run",
                "--hide-scrollbars",
                "--user-data-dir=" + self.profile,
                "--window-size=%d,%d" % (width, height),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=lambda: (os.dup2(to_r, 3), os.dup2(from_w, 4)),
            pass_fds=(3, 4),
            close_fds=True,
        )
        os.close(to_r)
        os.close(from_w)
        self.w, self.r = to_w, from_r
        self.buf = b""
        self.n = 0
        self.session = None

    def call(self, method: str, **params):
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params}
        if self.session:
            msg["sessionId"] = self.session
        os.write(self.w, json.dumps(msg).encode() + b"\0")
        while True:
            while b"\0" not in self.buf:
                chunk = os.read(self.r, 1 << 20)
                if not chunk:
                    raise RuntimeError("Chrome closed the pipe")
                self.buf += chunk
            raw, self.buf = self.buf.split(b"\0", 1)
            reply = json.loads(raw)
            if reply.get("id") == self.n:
                if "error" in reply:
                    raise RuntimeError("%s: %s" % (method, reply["error"]))
                return reply.get("result", {})

    def open(self, url: str, dark: bool) -> None:
        target = self.call("Target.createTarget", url="about:blank")["targetId"]
        self.session = self.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        self.call("Page.enable")
        self.call("Performance.enable")
        self.call(
            "Emulation.setEmulatedMedia",
            features=[{"name": "prefers-color-scheme", "value": "dark" if dark else "light"}],
        )
        self.call(
            "Emulation.setDeviceMetricsOverride",
            width=self.size[0],
            height=self.size[1],
            deviceScaleFactor=1,
            mobile=False,
        )
        self.call("Page.navigate", url=url)
        time.sleep(1.5)

    def metrics(self) -> dict:
        return {m["name"]: m["value"] for m in self.call("Performance.getMetrics")["metrics"]}

    def close(self) -> None:
        try:
            self.call("Browser.close")
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


def measure(chrome: Chrome, seconds: float) -> dict:
    a = chrome.metrics()
    time.sleep(seconds)
    b = chrome.metrics()
    span = b["Timestamp"] - a["Timestamp"]
    return {
        "seconds": round(span, 2),
        "main_thread_busy_pct": round(100 * (b["TaskDuration"] - a["TaskDuration"]) / span, 2),
        "layouts_per_s": round((b["LayoutCount"] - a["LayoutCount"]) / span, 2),
        "style_recalcs_per_s": round((b["RecalcStyleCount"] - a["RecalcStyleCount"]) / span, 2),
        "script_pct": round(100 * (b["ScriptDuration"] - a["ScriptDuration"]) / span, 2),
        "dom_nodes": int(b["Nodes"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("shot", "perf", "html"))
    ap.add_argument("--title", default="Global Home")
    ap.add_argument("--theme", default="auto")
    ap.add_argument("--mode", default="clock")
    ap.add_argument("--date", help="ISO date to render for")
    ap.add_argument("--weekday", type=int, help="advance to this weekday (0=Monday)")
    ap.add_argument("--hour", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--still", action="store_true")
    ap.add_argument("--event", default="auto", help="Village event: auto, none, or a name from EVENTS")
    ap.add_argument("--width", type=int, default=1400)
    ap.add_argument("--wait", type=float, default=0.0, help="extra seconds before the screenshot")
    ap.add_argument("--clip", help="x,y,w,h in CSS px to crop the screenshot")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("-o", "--out", default="train.png")
    args = ap.parse_args()

    page = render_page(args)
    if args.action == "html":
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(page)
        print("wrote %s (%d bytes)" % (args.out, len(page)))
        return

    tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
    tmp.write(page)
    tmp.close()
    height = int(args.width * 0.25) + 30
    chrome = Chrome(args.width, height)
    try:
        chrome.open("file://" + tmp.name, dark=args.mode == "dark")
        if args.action == "shot":
            time.sleep(args.wait)
            clip = {"x": 0, "y": 0, "width": args.width, "height": height, "scale": args.scale}
            if args.clip:
                x, y, w, h = (float(v) for v in args.clip.split(","))
                clip.update(x=x, y=y, width=w, height=h)
            png = chrome.call("Page.captureScreenshot", format="png", clip=clip)["data"]
            with open(args.out, "wb") as fh:
                fh.write(base64.b64decode(png))
            print("wrote %s; page is %d bytes" % (args.out, len(page)))
        else:
            time.sleep(2)
            result = measure(chrome, args.seconds)
            result["page_bytes"] = len(page)
            print(json.dumps(result, indent=2))
    finally:
        chrome.close()
        os.unlink(tmp.name)


if __name__ == "__main__":
    main()
