"""Where a graph's blocks sit when nobody has placed them: golem's built-ins, and jobs from before
workflows. Down the page along each block's first output (the usual way on, readable on a
phone), with the blocks only other outputs reach (a fix loop, a branch) in columns beside it.
Riggy moves them in the editor; what he sets is kept."""

from __future__ import annotations

from . import engine

ROW = 110  # px between rows
COLUMN = 280  # px between columns


def place(graph: dict) -> dict:
    """Give every block without a position one, in place; returns the graph."""
    nodes = engine.by_id(graph)
    wired = engine.wires(graph)
    if all("x" in n and "y" in n for n in nodes.values()):
        return graph
    try:
        start = engine.entry(graph)["id"]
    except engine.GraphError:
        start = next(iter(nodes), None)
    spots: dict[str, tuple[int, int]] = {}
    taken: set[tuple[int, int]] = set()

    def spine(at: str | None, column: int, row: int) -> None:
        """Follow first outputs from `at`, down `column`, from `row`."""
        while at is not None and at in nodes and at not in spots:
            while (column, row) in taken:
                row += 1
            spots[at] = (column, row)
            taken.add((column, row))
            outs = _ports(nodes[at], wired)
            at = wired.get(at, {}).get(outs[0]) if outs else None
            row += 1

    spine(start, 0, 0)
    for _ in range(len(nodes)):  # blocks reached only by other outputs: a column to the right of what leads there
        placed = False
        for nid, (column, row) in sorted(spots.items(), key=lambda kv: (kv[1][1], kv[1][0])):
            for p in _ports(nodes[nid], wired)[1:]:
                to = wired.get(nid, {}).get(p)
                if to in nodes and to not in spots:
                    spine(to, column + 1, row)
                    placed = True
        if not placed:
            break
    for nid in nodes:  # anything nothing reaches
        if nid not in spots:
            spine(nid, max((c for c, _ in spots.values()), default=-1) + 1, 0)
    for nid, (column, row) in spots.items():
        nodes[nid].setdefault("x", column * COLUMN)
        nodes[nid].setdefault("y", row * ROW)
    return graph


def _ports(node: dict, wired: dict[str, dict[str, str]]) -> list[str]:
    """A block's outputs in order; a container's are whatever it's wired by."""
    if node["type"] == "container":
        return list(wired.get(node["id"], {}))
    return engine.ports(node)
