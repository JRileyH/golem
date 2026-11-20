"""What makes the web app installable on a phone's home screen, with notifications: the
manifest, its icon (drawn here from the mark, brand.py), the service worker that shows pushed
messages and opens them, and the panel that turns notifications on for a device.

iPhones only allow notifications for web apps added to the Home Screen (Share, then Add
to Home Screen), and opened from there. The icon is the mark on ember, dark or light: iOS
can't switch a web app's icon with the phone's appearance, so the page hands it the one that
matches the phone at the moment it's added (touch_icon).
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
import zlib
from functools import cache

from . import brand

ICON_SIZES = (180, 192, 512)


@cache
def version() -> str:
    """A hash of the icons, put in their URLs: a changed icon gets a URL no cache has seen. At a
    fixed URL, Safari, and Cloudflare in front of golem, kept handing out the old one."""
    return hashlib.sha256(b"".join(icon(s, light) for s in ICON_SIZES for light in (False, True))).hexdigest()[:10]


def icon_url(size: int, light: bool = False) -> str:
    return f"/icon-{size}{'-light' if light else ''}.png?v={version()}"


@cache
def manifest() -> dict:
    return {
        "name": "Golem",
        "short_name": "Golem",
        "description": "Your projects, what golem is doing, and what it needs from you.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#302b28",  # ember's granite, behind the icon while it opens
        "theme_color": "#283035",  # the header's slate
        "icons": [
            {"src": icon_url(192), "sizes": "192x192", "type": "image/png"},
            {"src": icon_url(512), "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }


@cache
def touch_icon() -> str:
    """The home-screen icon, swapped for the light one on a phone in light mode as the page loads."""
    return (
        f"<link rel=apple-touch-icon href='{icon_url(180)}' id=touch-icon><script>"
        f"if (matchMedia('(prefers-color-scheme: light)').matches) document.getElementById('touch-icon').href = '{icon_url(180, True)}';"
        "</script>"
    )


@cache
def service_worker() -> str:
    return SERVICE_WORKER.replace("ICON", f"'{icon_url(192)}'")


SERVICE_WORKER = """
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('push', e => {
  let m = {};
  try { m = e.data.json(); } catch (err) { m = {title: 'golem', body: e.data ? e.data.text() : ''}; }
  e.waitUntil(self.registration.showNotification(m.title || 'golem', {
    body: m.body || '', tag: m.tag, renotify: true, icon: ICON, badge: ICON, data: {url: m.url || '/'},
  }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL(e.notification.data.url || '/', self.location.origin).href;
  e.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(all => {
    const open = all.find(c => c.url.startsWith(self.location.origin));
    return open ? open.focus().then(c => c.navigate(url)) : self.clients.openWindow(url);
  }));
});
"""

# Registers the service worker on every page, and runs the notifications panel if there is one.
CLIENT = """
(() => {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('/sw.js');
  const panel = document.getElementById('notify');
  if (!panel) return;
  const say = text => { panel.querySelector('.state').textContent = text; };
  const show = on => panel.querySelectorAll('[data-on]').forEach(b => b.hidden = (b.dataset.on === '1') !== on);
  const post = (path, body) => fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  if (!('PushManager' in window) || !('Notification' in window)) {
    say(/iPhone|iPad/.test(navigator.userAgent)
      ? 'On iPhone: tap Share, then Add to Home Screen, then open golem from there and turn them on.'
      : 'This browser can’t get notifications.');
    show(null); return;
  }
  const key = b64 => { const s = atob((b64 + '='.repeat((4 - b64.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(s, c => c.charCodeAt(0)); };
  async function current() { const reg = await navigator.serviceWorker.ready; return reg.pushManager.getSubscription(); }
  current().then(sub => { show(!!sub); say(sub ? 'On for this device.' : 'Off for this device.'); });
  panel.querySelector('[data-act=on]').addEventListener('click', async () => {
    try {
      if (await Notification.requestPermission() !== 'granted') { say('Notifications are blocked: allow them in the settings for golem.'); return; }
      const reg = await navigator.serviceWorker.ready;
      const {key: publicKey} = await (await fetch('/push/key')).json();
      const sub = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: key(publicKey)});
      const r = await post('/push/subscribe', {...sub.toJSON(), label: navigator.userAgent});
      if (!r.ok) throw new Error(await r.text());
      show(true); say('On for this device.');
    } catch (err) { say('Couldn’t turn them on: ' + err.message); }
  });
  panel.querySelector('[data-act=test]').addEventListener('click', async () => {
    const r = await post('/push/test', {}); say(r.ok ? 'Sent a test. It should arrive in a moment.' : 'Couldn’t send a test.');
  });
  panel.querySelector('[data-act=off]').addEventListener('click', async () => {
    const sub = await current();
    if (sub) { await post('/push/unsubscribe', {endpoint: sub.endpoint}); await sub.unsubscribe(); }
    show(false); say('Off for this device.');
  });
})();
"""


def panel() -> str:
    """Turning notifications on and off for the device it's shown on (CLIENT does the work)."""
    return (
        "<section class=notify-panel id=notify><h2>Notifications</h2>"
        "<p class=sub>When golem needs you (a question, a check-in, options to pick from) or a job is done.</p>"
        "<p class=state>Checking…</p><div class=row-form>"
        "<button type=button data-act=on data-on=0 class=primary>Turn on for this device</button>"
        "<button type=button data-act=test data-on=1 hidden>Send a test</button>"
        "<button type=button data-act=off data-on=1 hidden class=danger>Turn off</button></div></section>"
    )


@cache
def icon(size: int, light: bool = False) -> bytes:
    """golem's icon as a PNG: the mark on ember, full bleed (iOS rounds the corners itself, and
    the mark sits well inside the middle 80% Android's masks keep). 60% of the height, a touch high."""
    stops, ink = (brand.EMBER_LIGHT, brand.EMBER_LIGHT_INK) if light else (brand.EMBER, brand.EMBER_INK)
    ink = _rgb(ink)
    # At 135deg a pixel's color depends only on x + y: one strip of colors, a window of it per row.
    strip = b"".join(bytes(_at(stops, (d + 1) / (2 * size))) for d in range(2 * size))
    scale = 0.6 * size / 312
    cover = _coverage(_edges(brand.MARK, scale, (size - 276 * scale) / 2 - 118 * scale,
                             (size - 312 * scale) / 2 - size * 0.015 - 128 * scale), size)  # fmt: skip
    rows = []
    for y in range(size):
        row = bytearray(strip[3 * y : 3 * (y + size)])
        for x, c in cover.get(y, {}).items():
            row[3 * x : 3 * x + 3] = bytes(round(b + (i - b) * c) for b, i in zip(row[3 * x : 3 * x + 3], ink))
        rows.append(b"\0" + bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


def _at(stops: tuple[tuple[float, str], ...], t: float) -> tuple[int, ...]:
    """The gradient's color at `t` (0 to 1)."""
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if t <= b:
            f = (t - a) / (b - a) if b > a else 0
            return tuple(round(x + (y - x) * f) for x, y in zip(_rgb(ca), _rgb(cb)))
    return _rgb(stops[-1][1])


def _edges(d: str, scale: float, dx: float, dy: float) -> list[tuple[float, float, float, float]]:
    """A path of M, L, A and Z (absolute, circular arcs) as straight edges in pixels, arcs cut
    into short chords."""
    edges, points, start = [], [], (0.0, 0.0)
    tokens = re.findall(r"[MLAZ]|-?[\d.]+", d)
    at = lambda: float(tokens.pop(0))  # noqa: E731
    while tokens:
        cmd = tokens.pop(0)
        if cmd == "M":
            points = [(at(), at())]
            start = points[0]
        elif cmd == "L":
            points.append((at(), at()))
        elif cmd == "A":
            r, _, _, large, sweep, x, y = (at() for _ in range(7))
            points += _arc(points[-1], (x, y), r, bool(large), bool(sweep))
        elif cmd == "Z":
            points.append(start)
            px = [(dx + x * scale, dy + y * scale) for x, y in points]
            edges += [(*p, *q) for p, q in zip(px, px[1:]) if p[1] != q[1]]
    return edges


def _arc(p0, p1, r, large, sweep):
    """Points along a circular arc from p0 to p1 (SVG's endpoint form), ending at p1."""
    hx, hy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    half = hx * hx + hy * hy
    r = max(r, math.sqrt(half))
    k = math.sqrt(max(0.0, (r * r - half) / half)) * (-1 if large == sweep else 1)
    cx, cy = k * hy + (p0[0] + p1[0]) / 2, -k * hx + (p0[1] + p1[1]) / 2
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    turn = math.atan2(p1[1] - cy, p1[0] - cx) - a0
    if sweep and turn < 0:
        turn += 2 * math.pi
    elif not sweep and turn > 0:
        turn -= 2 * math.pi
    n = max(2, math.ceil(abs(turn) / (math.pi / 24)))
    return [(cx + r * math.cos(a0 + turn * i / n), cy + r * math.sin(a0 + turn * i / n)) for i in range(1, n)] + [p1]


def _coverage(edges, size: int, rows: int = 8) -> dict[int, dict[int, float]]:
    """How much of each pixel the shape covers (even-odd), 0 to 1, by row then column: exact
    across, `rows` samples down each pixel."""
    found: dict[int, dict[int, float]] = {}
    for y in range(size):
        live = [e for e in edges if min(e[1], e[3]) < y + 1 and max(e[1], e[3]) > y]
        if not live:
            continue
        part, step = [0.0] * (size + 1), [0.0] * (size + 1)
        for k in range(rows):
            sy = y + (k + 0.5) / rows
            xs = sorted(x0 + (sy - y0) * (x1 - x0) / (y1 - y0) for x0, y0, x1, y1 in live if min(y0, y1) <= sy < max(y0, y1))
            for a, b in zip(xs[::2], xs[1::2]):
                a, b = max(a, 0.0), min(b, float(size))
                ia, ib = int(a), int(b)
                if ia == ib:
                    part[ia] += (b - a) / rows
                    continue
                part[ia] += (ia + 1 - a) / rows
                part[min(ib, size)] += (b - ib) / rows
                step[ia + 1] += 1 / rows
                step[ib] -= 1 / rows
        run, row = 0.0, {}
        for x in range(size):
            run += step[x]
            if (c := min(1.0, run + part[x])) > 0.002:
                row[x] = c
        found[y] = row
    return found
