"""Push notifications to Riggy's devices, for the web app installed on his phone's home screen.

The messages that want his attention (a question, a check-in, options to pick from, a job
done, a usage limit) are pushed; news he can read later isn't. This is standard Web Push with VAPID: golem makes its key pair on first
start and keeps it in the database, and each device that turns notifications on (the
inbox page) registers its subscription. A device the push service says is gone is
forgotten.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Callable

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from .config import Settings
from .store import Said, Store
from .talk import INBOX, STATUS

log = logging.getLogger(__name__)

TTL = 24 * 3600  # a phone that's off for a day gets it when it's back
# How a push goes out: pywebpush's webpush(subscription, data, vapid key, claims, ttl); tests pass their own.
Send = Callable[..., object]


class Push:
    def __init__(self, settings: Settings, store: Store, send: Send = webpush):
        self.settings = settings
        self.store = store
        self.send = send
        self._vapid: Vapid | None = None

    @property
    def vapid(self) -> Vapid:
        """Golem's signing key, made the first time and kept."""
        if self._vapid is None:
            pem = self.store.get("vapid_private_key")
            if pem is None:
                key = Vapid()
                key.generate_keys()
                pem = key.private_pem().decode()
                self.store.put("vapid_private_key", pem)
            self._vapid = Vapid.from_pem(pem.encode())
        return self._vapid

    def public_key(self) -> str:
        """For the browser's pushManager.subscribe: the raw public key, base64url."""
        raw = self.vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    async def notify(self, said: Said) -> None:
        """A message that wants Riggy, to every device he's turned notifications on for."""
        project = self.store.project(said.channel) if said.channel not in (INBOX, STATUS) else None
        url = f"/p/{project.slug}#end" if project else "/inbox#end"
        payload = {"title": project.name if project else "golem", "body": said.text[:400] or "A picture", "url": url, "tag": said.channel}
        await self.to_all(payload)

    async def to_all(self, payload: dict) -> int:
        """Send to every device; returns how many took it."""
        sent = 0
        for sub in self.store.subscriptions():
            try:
                await asyncio.to_thread(
                    self.send,
                    subscription_info=sub,
                    data=json.dumps(payload),
                    vapid_private_key=self.vapid,
                    vapid_claims={"sub": self.settings.url("golem")},  # webpush adds to it: a new one each time
                    ttl=TTL,
                )
                sent += 1
            except WebPushException as e:
                status = e.response.status_code if e.response is not None else None
                if status in (404, 410):  # unsubscribed, or the app was removed
                    self.store.forget_subscription(sub["endpoint"])
                else:
                    log.warning("push to %s failed: %s", sub["endpoint"][:60], e)
        return sent
