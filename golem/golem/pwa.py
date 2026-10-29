"""What makes the web app installable on a phone's home screen, with notifications: the
manifest, its icon (drawn here: a line through three steps, as on the status page), the
service worker that shows pushed messages and opens them, and the panel that turns
notifications on for a device.

iPhones only allow notifications for web apps added to the Home Screen (Share, then Add
to Home Screen), and opened from there.
"""

from __future__ import annotations

import struct
import zlib
from functools import cache

BACKGROUND = (15, 23, 42)  # the header's navy
DOTS = ((42, 120, 214), (27, 175, 122), (237, 161, 0))  # the first three project colors

MANIFEST = {
    "name": "golem",
    "short_name": "golem",
    "description": "Your projects, what golem is doing, and what it needs from you.",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#0f172a",
    "theme_color": "#0f172a",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
}
ICON_SIZES = (180, 192, 512)

SERVICE_WORKER = """
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('push', e => {
  let m = {};
  try { m = e.data.json(); } catch (err) { m = {title: 'golem', body: e.data ? e.data.text() : ''}; }
  e.waitUntil(self.registration.showNotification(m.title || 'golem', {
    body: m.body || '', tag: m.tag, renotify: true, icon: '/icon-192.png', badge: '/icon-192.png', data: {url: m.url || '/'},
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
def icon(size: int) -> bytes:
    """golem's icon as a PNG: three steps on a line, on the header's navy."""
    rows = []
    mid, line = size / 2, size * 0.022
    centers = [size * x for x in (0.2, 0.5, 0.8)]
    radius = size * 0.085
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            color = BACKGROUND
            if centers[0] <= x <= centers[-1] and abs(y - mid) <= line:
                color = (241, 245, 249)
            for cx, dot in zip(centers, DOTS):
                d = (x - cx) ** 2 + (y - mid) ** 2
                if d <= (radius + size * 0.025) ** 2:
                    color = BACKGROUND if d > radius**2 else dot  # a gap around each dot
            row += bytes(color)
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")
