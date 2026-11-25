"""`golem`: what a worker can ask of golem from its shell, a subcommand each.

    golem report KIND IMPACT "what got in your way" [--detail TEXT | --detail -]
    golem deploy status | logs [service] [tail]

Each subcommand is a `Command` in COMMANDS: its name, a line of help, its arguments and what
it does. Standard library only, and nothing a command does needs golem's web app or database:
`report` appends to a file golem reads after the run. Shaped so each could become an MCP tool.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from . import deploy_cli, reports


@dataclass(frozen=True)
class Command:
    name: str
    help: str
    configure: Callable[[argparse.ArgumentParser], None]
    run: Callable[[argparse.Namespace, Mapping[str, str]], int]


# --- golem report ---------------------------------------------------------------

REPORT_ABOUT = """\
Tell whoever improves golem about something golem itself did that got in your way: its
instructions, tools, environment or flow, not the project you're building. Report it when it
blocked you, cost you real time or turns, or golem told you something wrong. Ordinary hard work
isn't a report, and at most two are kept from a step."""


def configure_report(parser: argparse.ArgumentParser) -> None:
    parser.description = REPORT_ABOUT
    parser.epilog = "kinds:\n" + "\n".join(f"  {k:<13} {h}" for k, h in reports.KIND_HELP.items())
    parser.add_argument("kind", choices=reports.KINDS)
    parser.add_argument(
        "impact", choices=reports.IMPACTS, help="blocked: you couldn't go on without a workaround; costly: it cost real time or turns; minor"
    )
    parser.add_argument("summary", help=f"one line, at most {reports.SUMMARY_CHARS} characters: what got in your way")
    parser.add_argument(
        "--detail", default="", help="what happened, what it cost you, what would have helped (- reads it from stdin)"
    )


def run_report(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    path, run = env.get("GOLEM_REPORTS", ""), env.get("GOLEM_RUN", "")
    if not path or not run:
        print("golem report only works inside one of golem's agent steps (GOLEM_REPORTS and GOLEM_RUN aren't set).", file=sys.stderr)
        return 1
    summary = args.summary.strip()
    if "\n" in summary or len(summary) > reports.SUMMARY_CHARS:
        print(f"The summary is one line of at most {reports.SUMMARY_CHARS} characters: put the rest in --detail.", file=sys.stderr)
        return 2
    detail = sys.stdin.read() if args.detail == "-" else args.detail
    try:
        made = reports.make(args.kind, args.impact, summary, detail)
    except reports.Invalid as e:
        print(f"golem report: {e}", file=sys.stderr)
        return 2
    if filed(path, run) >= reports.MAX_PER_RUN:
        print(
            f"You've filed {reports.MAX_PER_RUN} reports in this step, as many as golem keeps: "
            "report only what cost you most.",
            file=sys.stderr,
        )
        return 1
    with open(path, "a") as out:  # one write, so reports never interleave
        out.write(json.dumps({**made, "run": run}) + "\n")
    cut = f" Its detail was cut to {reports.DETAIL_CHARS} characters." if len(detail.strip()) > reports.DETAIL_CHARS else ""
    print(f"Filed. golem reads it in once this step ends.{cut}")
    return 0


def filed(path: str, run: str) -> int:
    """The reports already filed in this run."""
    try:
        with open(path) as f:
            return sum(1 for line in f if _run_of(line) == run)
    except FileNotFoundError:
        return 0


def _run_of(line: str) -> str:
    try:
        return str(json.loads(line).get("run", ""))
    except (ValueError, AttributeError):
        return ""


# --- golem deploy ---------------------------------------------------------------


def configure_deploy(parser: argparse.ArgumentParser) -> None:
    parser.description = deploy_cli.__doc__.replace("golem-deploy", "golem deploy")
    parser.add_argument("what", nargs=argparse.REMAINDER, help="status | logs [service] [tail]")


def run_deploy(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return deploy_cli.main(args.what)


COMMANDS = (
    Command("report", "report something golem did that got in your way", configure_report, run_report),
    Command("deploy", "your project's deployment: what's live, and its logs", configure_deploy, run_deploy),
)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="golem", description="What a worker can ask of golem.")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    for command in COMMANDS:
        sub.add_parser(command.name, help=command.help, formatter_class=argparse.RawDescriptionHelpFormatter).set_defaults(
            run=command.run
        )
        command.configure(sub.choices[command.name])
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:  # a bad argument, or --help: argparse has said why
        return e.code if isinstance(e.code, int) else 2
    return args.run(args, os.environ if env is None else env)


if __name__ == "__main__":
    sys.exit(main())
