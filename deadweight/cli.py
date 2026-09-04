"""Command line interface."""

from __future__ import annotations

import argparse
from pathlib import Path

from .analyze import build
from .report import VERSION, render, render_json
from .transcripts import load

EPILOG = """\
examples:
  deadweight                       weigh every session on this machine
  deadweight --project my-repo     only sessions from a matching directory
  deadweight --since 30            only the last 30 days
  deadweight --used                also show what is earning its keep
  deadweight --json                machine-readable output

Everything is read locally from ~/.claude/projects. No message text is read,
and nothing leaves your machine.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deadweight",
        description=(
            "Find the parts of your Claude Code setup that cost context on every "
            "session and never do any work."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"deadweight {VERSION}")
    parser.add_argument(
        "--project",
        default=None,
        help="Only sessions whose working directory matches this substring.",
    )
    parser.add_argument(
        "--since",
        type=int,
        default=None,
        metavar="DAYS",
        help="Only sessions from the last DAYS days.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Read at most N sessions, newest first.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=15,
        metavar="N",
        help="Rows to show per section (default: 15).",
    )
    parser.add_argument(
        "--used", action="store_true", help="Also list what is actually being called."
    )
    parser.add_argument("--all", action="store_true", help="Show every row, not just the top ones.")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Claude config directory (default: ~/.claude, or $CLAUDE_CONFIG_DIR).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    sessions = load(
        args.root,
        project=args.project,
        limit=args.limit,
        since_days=args.since,
    )
    report = build(sessions)

    if args.json:
        render_json(report)
        return 0

    render(
        report,
        limit=10**6 if args.all else args.top,
        show_used=args.used,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
