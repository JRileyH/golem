"""What a stage step is about: the section golem adds to its text (stages.compose), worked
out from where the job has been, by rules a person would use.

- A Review or Docs step is about the work since the last of its own stage, else since the job
  last shipped, else everything the job has done. A Review right after a Review reviews the
  first one's fixes (its `fixes` part); a Review of only planning work reviews the plan (its
  `plan` part). The
  range is a commit to diff from (the job's branch's head after that step, steps.head), so the
  worker sees exactly that work, merges squashed or not; with none recorded, all that isn't on
  main yet. A Review holds it to Riggy's request and what earlier blocks left for that (a spec,
  the design guide, his pick).
- A Build step is about the nearest real block before it: Riggy's pick, his reply to a check-in
  (which comes first), the plan, or else his request.
- A Design step that writes the guide is about his pick.
- A Fix step is about what failed (its problem, which golem adds anyway).
- A block can say which earlier block it's about (`about`), and then it's that block's work.

Pure: from the job's copy of its workflow (blocks by flattened id), its steps, and the step.
`hints` says the same of a workflow's blocks in a word or two, for the editor.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import stages
from .store import Step

SHIPPED = ("push", "push-main")  # the work up to here went out
LOOK_PAST = ("review", "docs")  # what a Build looks past for what it's building from: a plan's review, say
SHIP_TYPES = (*SHIPPED, "pr", "checks", "merge", "deploy")


@dataclass
class About:
    text: str  # the section's body; "" for nothing to add
    variant: str = ""  # a Review's: "plan", of only planning work; "fixes", of the last review's fixes


def about(nodes: dict[str, dict], trace: list[Step], step: Step, request: str = "") -> About:
    node = nodes.get(step.node_id) or {}
    stage = node.get("stage") or ""
    earlier = [s for s in sorted(trace, key=lambda s: s.position) if s.id and s.position < step.position]
    done = [s for s in earlier if s.state in ("done", "skipped", "failed")]
    if node.get("about"):
        target = _sibling(step.node_id, node["about"])
        visits = [s for s in done if s.node_id == target and s.state == "done"]
        if visits:
            return About(_their_work(nodes, done, visits[-1]))
    if stage in ("review", "docs"):
        return _since(nodes, done, stage, request)
    if stage == "build" or (stage == "design" and node.get("makes") == "guide"):
        return About(_built_from(nodes, done))
    if stage == "fix" and not step.problem:
        failed = next((s for s in reversed(done) if s.state == "failed"), None)
        if failed:
            return About(f"What failed: {_label(nodes, failed)}: {_line(failed.error or failed.summary or '')}")
    return About("")


def _since(nodes: dict, done: list[Step], stage: str, request: str) -> About:
    work = [s for s in done if s.kind == "agent" and s.state == "done"]
    last = work[-1] if work else None
    # Right after one of its own stage, a Review reviews that one's fixes.
    if stage == "review" and last and _stage(nodes, last) == "review":
        before = _head_before(done, last)
        if before and last.head:
            text = (f"The last review's fixes: `git log --oneline {before[:12]}..{last.head[:12]}` and `git diff {before[:12]}..{last.head[:12]}`."
                    + (f" It said: {_line(last.summary or '')}" if last.summary else ""))  # fmt: skip
            return About(text + _hold(nodes, done, request), "fixes")
    anchor, since = None, ""
    same = [s for s in done if s.kind == "agent" and s.state == "done" and _stage(nodes, s) == stage]
    if same:
        anchor, since = same[-1], f"the last {stages.catalog()[stage].label.lower()} ({_label(nodes, same[-1])})"
    elif shipped := [s for s in done if s.kind in SHIPPED and s.state == "done"]:
        anchor, since = shipped[-1], "the job last shipped"
    elif setup := [s for s in done if s.kind in ("setup", "refresh") and s.state == "done"]:
        anchor, since = setup[0], "the job began"
    after = [s for s in work if anchor is None or s.position > anchor.position]
    variant = "plan" if stage == "review" and after and all(_stage(nodes, s) == "plan" for s in after) else ""
    what = "The plan" if variant else "The work"
    if anchor is not None and anchor.head:
        sha = anchor.head[:12]
        text = f"{what} since {since}: `git log --oneline {sha}..HEAD` and `git diff {sha}..HEAD`."
    else:
        text = f"{what} on this branch that isn't on `main` yet: `git log --oneline origin/main..HEAD` and `git diff origin/main...HEAD`."
    if after:
        text += "\n\nWhat each step since said:\n" + "\n".join(f"- {_label(nodes, s)}: {_line(s.summary or 'done')}" for s in after)
    return About(text + (_hold(nodes, done, request) if stage == "review" else ""), variant)


def _built_from(nodes: dict, done: list[Step]) -> str:
    for s in reversed(done):
        if s.state != "done":
            continue
        if s.kind == "choose":
            return f"What Riggy picked: {_line(s.summary or 'his pick')} Build that, from its files."
        if s.kind == "checkin":
            return "Riggy tried it, and his reply is under \"Riggy's feedback\": what he asked for comes first."
        if s.kind == "agent" and _stage(nodes, s) == "plan":
            made = _produces(nodes, s)
            return "The plan" + (f" ({', '.join(made)})" if made else "") + f": build from it. It said: {_line(s.summary or '')}".rstrip(" :")
        if s.kind == "agent" and _stage(nodes, s) not in LOOK_PAST:
            break  # other work before it: nothing more to say than the request
    return "Riggy's request (above)."


def _their_work(nodes: dict, done: list[Step], visit: Step) -> str:
    before = _head_before(done, visit)
    text = f"The work of {_label(nodes, visit)}"
    if before and visit.head:
        text += f": `git log --oneline {before[:12]}..{visit.head[:12]}` and `git diff {before[:12]}..{visit.head[:12]}`"
    return text + "." + (f" It said: {_line(visit.summary)}" if visit.summary else "")


def _hold(nodes: dict, done: list[Step], request: str) -> str:
    held = ["Riggy's request (above)"]
    for s in done:
        if s.kind == "choose" and s.state == "done":
            held.append("what he picked, with its files (under \"Done so far\")")
        for made in _produces(nodes, s) if s.state == "done" else []:
            if made not in held:
                held.append(f"`{made}`" if "/" in made and " " not in made else made)
    return "\n\nHold it to: " + ", ".join(dict.fromkeys(held)) + "."


def _produces(nodes: dict, s: Step) -> list[str]:
    node = nodes.get(s.node_id) or {}
    stage = stages.catalog().get(node.get("stage") or "")
    return stage.produces(node) if stage else []


def _head_before(done: list[Step], step: Step) -> str:
    return next((s.head for s in reversed(done) if s.position < step.position and s.head), "")


def _stage(nodes: dict, s: Step) -> str:
    return (nodes.get(s.node_id) or {}).get("stage") or ""


def _label(nodes: dict, s: Step) -> str:
    return (nodes.get(s.node_id) or {}).get("name") or s.name


def _sibling(node_id: str, other: str) -> str:
    """A block's id at the same level of the job's copy as `node_id` (paths: "ship-2/fix")."""
    return f"{node_id.rsplit('/', 1)[0]}/{other}" if "/" in node_id else other


def _line(text: str, limit: int = 240) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- for the editor -----------------------------------------------------------------------


def hints(order: list[dict]) -> dict[str, str]:
    """What each stage block of a workflow is about, in a few words, from the order a job meets its
    blocks (plan.blocks_in_order): "the work since review", "Riggy's pick"."""
    found: dict[str, str] = {}
    by_id = {n["id"]: n for n in order}
    for i, node in enumerate(order):
        stage = node.get("stage")
        if node.get("type") != "agent" or not stage:
            continue
        before = order[:i]
        if node.get("about") and node["about"] in by_id:
            found[node["id"]] = f"{by_id[node['about']].get('name') or node['about']}'s work"
        elif stage in ("review", "docs"):
            work = [n for n in before if n.get("type") == "agent"]
            same = [n for n in work if n.get("stage") == stage]
            if stage == "review" and work and work[-1].get("stage") == "review":
                found[node["id"]] = f"{work[-1].get('name')}'s fixes"
            elif same:
                found[node["id"]] = f"the work since {same[-1].get('name')}"
            elif any(_ships(n) for n in before):
                found[node["id"]] = "the work since it last shipped"
            else:
                found[node["id"]] = "everything the job did"
            since = work[work.index(same[-1]) + 1 :] if same else work
            if stage == "review" and since and all(n.get("stage") == "plan" for n in since):
                found[node["id"]] = "the plan"
        elif stage == "build" or (stage == "design" and node.get("makes") == "guide"):
            found[node["id"]] = "Riggy's request"
            for n in reversed(before):
                if n.get("type") == "choose":
                    found[node["id"]] = "Riggy's pick"
                elif n.get("type") == "checkin":
                    found[node["id"]] = "his reply, first"
                elif n.get("type") == "agent" and n.get("stage") == "plan":
                    found[node["id"]] = "the plan"
                elif n.get("type") != "agent" or n.get("stage") in LOOK_PAST:
                    continue
                break
        elif stage == "fix":
            found[node["id"]] = "what failed"
    return found


def _ships(node: dict) -> bool:
    return node.get("type") in SHIP_TYPES or (node.get("type") == "container" and node.get("workflow") == "ship")
