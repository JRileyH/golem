"""The conversation: everything Riggy and golem say to each other, kept in one place.

Each message is recorded by channel: a project's slug, "inbox" for new ideas, or "status"
for golem-wide news. The web app shows those conversations and takes Riggy's messages,
and the ones that want his attention (a question, a check-in, "done") are pushed to his
devices (push.py).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from .store import Said, Store

log = logging.getLogger(__name__)

INBOX, STATUS = "inbox", "status"
# Called with each message that wants Riggy's attention.
Notify = Callable[[Said], Awaitable[None]]


class Talk:
    def __init__(self, store: Store, notify: Notify | None = None):
        self.store = store
        self.notify = notify

    async def heard(self, channel: str, text: str, job: int | None = None, step: int | None = None) -> Said:
        """Something Riggy said: to a job (a reply, a note) if `job`."""
        return self.store.add_message(channel, "riggy", text, job_id=job, step_id=step)

    async def send(self, channel: str, text: str, notice: bool = False, job: int | None = None, step: int | None = None) -> Said:
        """Not a notice, it wants Riggy's attention (it's pushed); a notice is news he can read later.
        About a job (`job`, and the step it's about, `step`), it's that job's activity."""
        return await self._record(self.store.add_message(channel, "golem", text, notify=not notice, job_id=job, step_id=step))

    async def send_image(self, channel: str, path: Path, caption: str = "", job: int | None = None, step: int | None = None) -> Said:
        return await self._record(self.store.add_message(channel, "golem", caption, image=_url(path), job_id=job, step_id=step))

    async def _record(self, said: Said) -> Said:
        if said.notify and self.notify:
            try:
                await self.notify(said)
            except Exception:
                log.exception("couldn't push message %s", said.id)
        return said


def _url(path: Path) -> str | None:
    """A picture's address on the status page: options' pictures are the only ones it serves."""
    jobs = path.parent.parent.parent
    if path.parent.name == "options" and jobs.name == "jobs" and path.parent.parent.name.isdigit():
        return f"/jobs/{path.parent.parent.name}/options/{path.name}"
    return None
