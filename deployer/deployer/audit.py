"""Append-only JSON-lines log of every request, decision and rejection reason."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path


class Audit:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, action: str, outcome: str, **fields: object) -> None:
        entry = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "action": action, "outcome": outcome}
        entry.update(fields)
        line = json.dumps(entry, sort_keys=False) + "\n"
        with self.lock, self.path.open("a") as f:
            f.write(line)
