"""`golem-deploy`: the workers' view of the deployer, for their own project only.

    golem-deploy status                    what's deployed, at which commit
    golem-deploy logs [service] [tail]     the running containers' logs

The project comes from GOLEM_SLUG, which golem sets for each job. Deploying is one of
golem's own steps (after CI passes on main), so workers can't.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def call(method: str, path: str, body: dict | None = None) -> int:
    url = os.environ.get("DEPLOYER_URL", "http://deployer:8000") + path
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"X-Deployer-Secret": os.environ["DEPLOYER_SECRET"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as e:
        payload = json.load(e)
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def main(argv: list[str]) -> int:
    slug = os.environ.get("GOLEM_SLUG")
    if not slug or not argv:
        print(__doc__, file=sys.stderr)
        return 2
    match argv:
        case ["status"]:
            return call("GET", "/status?" + urllib.parse.urlencode({"slug": slug}))
        case ["logs", *rest] if len(rest) <= 2:
            query = {"slug": slug}
            if rest:
                query["service"] = rest[0]
            if len(rest) > 1:
                query["tail"] = rest[1]
            return call("GET", "/logs?" + urllib.parse.urlencode(query))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
