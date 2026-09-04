"""Rendering the report."""

from __future__ import annotations

import json
import os
import sys
from typing import TextIO

from .analyze import Item, Report
from .transcripts import CHARS_PER_TOKEN

VERSION = "0.1.0"

#: Below this many sessions, "never called" is not evidence of anything.
MEANINGFUL_SESSIONS = 5


def share(fraction: float) -> str:
    """Format a ratio without rounding a near-miss up to a flat 100%."""
    percent = fraction * 100
    if 99.0 <= percent < 100.0:
        return f"{percent:.1f}%"
    if 0.0 < percent <= 1.0:
        return f"{percent:.1f}%"
    return f"{percent:.0f}%"


class Style:
    def __init__(self, stream: TextIO) -> None:
        self.enabled = (
            hasattr(stream, "isatty")
            and stream.isatty()
            and os.environ.get("NO_COLOR") is None
            and os.environ.get("TERM") != "dumb"
        )

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, t: str) -> str:
        return self._wrap("1", t)

    def dim(self, t: str) -> str:
        return self._wrap("2", t)

    def red(self, t: str) -> str:
        return self._wrap("31", t)

    def green(self, t: str) -> str:
        return self._wrap("32", t)

    def yellow(self, t: str) -> str:
        return self._wrap("33", t)


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def human_count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def _row(style: Style, item: Item, width: int) -> str:
    cost = f"{item.chars:>6,} chars"
    tokens = style.dim(f"(~{item.tokens:,} tok)")
    if item.is_dead:
        verdict = style.red("never used")
    else:
        verdict = style.green(f"{item.calls:,} calls")
    return f"  {item.label:<11}{item.name:<{width}}  {cost}  {tokens:<18} {verdict}"


def render(
    report: Report,
    stream: TextIO | None = None,
    *,
    limit: int = 15,
    show_used: bool = False,
) -> None:
    stream = stream or sys.stdout
    st = Style(stream)

    if report.sessions == 0:
        stream.write(
            "No Claude Code sessions found.\n"
            "Transcripts live in ~/.claude/projects/. If you have used Claude Code "
            "on this machine, try --project to widen the filter.\n"
        )
        return

    scope = f"{report.sessions} session{'s' if report.sessions != 1 else ''}"
    if len(report.projects) == 1:
        scope += f" · {next(iter(report.projects))}"
    elif report.projects:
        scope += f" · {len(report.projects)} projects"
    stream.write(f"{st.bold('deadweight')} {st.dim(scope)}\n")

    if not report.items:
        stream.write(
            "\nNo inventory was recorded in these sessions, so there is nothing to "
            "weigh.\nThis usually means the transcripts predate the Claude Code "
            "version that records what it loads.\n"
        )
        return

    # Individual tools are listed by name only - their schemas load on demand -
    # so each costs a few dozen characters. Printing two hundred such rows would
    # bury the handful of items that actually cost something.
    dead_named = [i for i in report.dead if i.kind != "tool"]
    dead_tools = [i for i in report.dead if i.kind == "tool"]
    all_tools = [i for i in report.items if i.kind == "tool"]

    if report.dead:
        stream.write(f"\n{st.bold('DEAD WEIGHT')} {st.dim('loaded every session, never called')}\n")
        shown = dead_named[:limit]
        summary = f"{len(dead_tools)} of {len(all_tools)} never called"
        width = max([len(i.name) for i in shown] + [len(summary)]) + 2

        for item in shown:
            stream.write(_row(st, item, width) + "\n")
        hidden = len(dead_named) - len(shown)
        if hidden > 0:
            stream.write(st.dim(f"  … and {hidden} more\n"))

        if dead_tools:
            chars = sum(i.chars for i in dead_tools)
            stream.write(
                f"  {'tools':<11}{summary:<{width}}  {chars:>6,} chars  "
                f"{st.dim(f'(~{chars // CHARS_PER_TOKEN:,} tok)'):<18} "
                f"{st.dim('names only; schemas load on demand')}\n"
            )

        per_session = report.dead_chars_per_session
        total = report.total_chars_per_session
        stream.write(
            "\n  "
            + st.bold(
                f"{per_session:,} chars (~{per_session // CHARS_PER_TOKEN:,} tokens) "
                f"per session for nothing"
            )
            + st.dim(f" — {share(report.dead_share)} of the {total:,} you load\n")
        )
    else:
        stream.write(f"\n{st.green('Everything you load gets used.')}\n")

    # Reported, but kept out of the total above: there is no file to delete, so
    # counting them would inflate a saving nobody can actually collect.
    builtins = report.dead_builtins
    if builtins:
        chars = sum(i.chars for i in builtins)
        names = ", ".join(i.name for i in sorted(builtins, key=lambda i: -i.chars)[:4])
        if len(builtins) > 4:
            names += f", and {len(builtins) - 4} more"
        stream.write(
            st.dim(
                f"\n{len(builtins)} built-in agent(s) also went unused "
                f"({chars:,} chars): {names}.\n"
                "Not counted above — these ship with Claude Code and cannot be removed.\n"
            )
        )

    if report.sessions < MEANINGFUL_SESSIONS:
        stream.write(
            st.yellow(
                f"\nOnly {report.sessions} session"
                f"{'s' if report.sessions != 1 else ''} examined. "
                '"Never called" means little at this sample size — come back '
                "after a week of normal use.\n"
            )
        )

    if show_used:
        used = sorted(report.used, key=lambda i: -i.calls)[:limit]
        if used:
            stream.write(f"\n{st.bold('EARNING THEIR KEEP')}\n")
            width = max(len(i.name) for i in used) + 2
            for item in used:
                stream.write(_row(st, item, width) + "\n")

    if report.hooks:
        stream.write(f"\n{st.bold('HOOKS')} {st.dim('wall-clock you pay on every run')}\n")
        width = max(len(h.command) for h in report.hooks) + 2
        for hook in report.hooks[:limit]:
            errors = st.red(f"  {hook.errors} errored") if hook.errors else ""
            stream.write(
                f"  {hook.event:<14}{hook.command:<{width}}"
                f"{hook.runs:>5} runs  {human_duration(hook.total_ms / 1000):>8}"
                f"  {st.dim(f'avg {hook.average_ms:.0f}ms')}{errors}\n"
            )

    # "of session time" read as time spent working; it is the span from each
    # session's first message to its last, summed, so an overnight gap counts in
    # full. "spanning" is the honest word for that.
    stream.write(
        st.dim(
            f"\n{human_count(report.total_tool_calls)} tool calls · "
            f"{human_count(report.total_output_tokens)} output tokens · "
            f"{report.sessions} session(s) spanning {human_duration(report.total_seconds)}\n"
        )
    )
    if report.sessions_without_inventory:
        stream.write(
            st.dim(
                f"{report.sessions_without_inventory} session(s) recorded no "
                "inventory and were counted only for usage.\n"
            )
        )


def render_json(report: Report, stream: TextIO | None = None) -> None:
    stream = stream or sys.stdout
    payload = {
        "version": VERSION,
        "summary": {
            "sessions": report.sessions,
            "projects": sorted(report.projects),
            "dead_items": len(report.dead),
            "dead_builtin_items": len(report.dead_builtins),
            "dead_chars_per_session": report.dead_chars_per_session,
            "dead_tokens_per_session": report.dead_chars_per_session // CHARS_PER_TOKEN,
            "total_chars_per_session": report.total_chars_per_session,
            "dead_share": round(report.dead_share, 4),
            "tool_calls": report.total_tool_calls,
            "output_tokens": report.total_output_tokens,
            "cache_read_tokens": report.total_cache_read_tokens,
            # Elapsed span per session, summed. Includes idle time.
            "session_span_seconds": round(report.total_seconds, 1),
        },
        "items": [
            {
                "kind": i.kind,
                "name": i.name,
                "chars": i.chars,
                "tokens": i.tokens,
                "sessions_present": i.sessions_present,
                "sessions_used": i.sessions_used,
                "calls": i.calls,
                "dead": i.is_dead,
                "builtin": i.is_builtin,
            }
            for i in report.items
        ],
        "hooks": [
            {
                "event": h.event,
                "command": h.command,
                "runs": h.runs,
                "total_ms": h.total_ms,
                "average_ms": round(h.average_ms, 1),
                "errors": h.errors,
            }
            for h in report.hooks
        ],
    }
    json.dump(payload, stream, indent=2)
    stream.write("\n")
