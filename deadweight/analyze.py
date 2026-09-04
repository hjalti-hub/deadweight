"""Join what is configured against what was actually used.

The whole idea in one sentence: everything in your Claude Code setup charges you
context on every session, and some of it has never once done any work.

An item is *dead weight* when it was loaded into context but never called across
the whole history examined. That is a claim about evidence, not about quality, so
the report always states how many sessions it looked at - "never used in 3
sessions" means very little, "never used in 200" means quite a lot.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from .transcripts import CHARS_PER_TOKEN, HookRun, Session

#: Kinds of thing that occupy context, in the order they are reported.
KINDS = ("mcp", "skill", "agent", "tool")

_LABEL = {
    "mcp": "mcp server",
    "skill": "skill",
    "agent": "agent",
    "tool": "tool",
}


def match_key(name: str) -> str:
    """Normalise a name so a listing entry and a call site compare equal.

    An MCP server called "Claude Code Remote" appears in tool names as
    ``mcp__Claude_Code_Remote__…``, so punctuation and case have to go.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


#: Subagents Claude Code ships with. They occupy context like everything else,
#: but there is no file to delete, so counting them toward "you could remove
#: this" overstates what is actually recoverable. They are still reported - just
#: separately, and not in the headline total.
#:
#: Deliberately short. A name belongs here only if it genuinely cannot be
#: removed, because a wrong entry hides real waste instead of merely adding a
#: row. Anything uncertain is better left as removable.
BUILTIN_AGENTS = frozenset(
    match_key(name) for name in ("general-purpose", "Explore", "Plan", "claude", "statusline-setup")
)


@dataclass
class Item:
    """One configured thing, with what it costs and what it did."""

    kind: str
    name: str
    #: Characters it adds to context in a session that loads it.
    chars: int = 0
    #: Sessions in which it was loaded.
    sessions_present: int = 0
    #: Times it was actually called.
    calls: int = 0
    #: Sessions in which it was called at least once.
    sessions_used: int = 0

    @property
    def label(self) -> str:
        return _LABEL.get(self.kind, self.kind)

    @property
    def tokens(self) -> int:
        return self.chars // CHARS_PER_TOKEN

    @property
    def is_dead(self) -> bool:
        return self.calls == 0 and self.sessions_present > 0

    @property
    def is_builtin(self) -> bool:
        """True when nothing the user can edit would remove this."""
        return self.kind == "agent" and match_key(self.name) in BUILTIN_AGENTS

    @property
    def is_removable_dead(self) -> bool:
        """Unused *and* something the user could actually delete."""
        return self.is_dead and not self.is_builtin

    @property
    def calls_per_session(self) -> float:
        return self.calls / self.sessions_present if self.sessions_present else 0.0

    def cost_per_call(self) -> float:
        """Characters of context paid per actual use. Lower is better."""
        if self.calls == 0:
            return float("inf")
        return (self.chars * self.sessions_present) / self.calls


@dataclass
class HookStat:
    command: str
    event: str
    runs: int = 0
    total_ms: int = 0
    errors: int = 0

    @property
    def average_ms(self) -> float:
        return self.total_ms / self.runs if self.runs else 0.0


@dataclass
class Report:
    sessions: int = 0
    projects: set[str] = field(default_factory=set)
    items: list[Item] = field(default_factory=list)
    hooks: list[HookStat] = field(default_factory=list)
    #: Sessions that recorded no inventory at all, so nothing can be judged.
    sessions_without_inventory: int = 0

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_seconds: float = 0.0
    total_tool_calls: int = 0

    @property
    def dead(self) -> list[Item]:
        """Unused items the user could actually remove.

        Built-ins are excluded here and reported by :attr:`dead_builtins`
        instead. Telling someone to delete a subagent that ships with Claude
        Code wastes their time, and counting it in the total inflates a number
        whose whole value is that it is honest.
        """
        return [i for i in self.items if i.is_removable_dead]

    @property
    def dead_builtins(self) -> list[Item]:
        """Unused, but nothing the user can do about it."""
        return [i for i in self.items if i.is_dead and i.is_builtin]

    @property
    def used(self) -> list[Item]:
        return [i for i in self.items if not i.is_dead]

    @property
    def dead_chars_per_session(self) -> int:
        """Context spent every session on removable things that never did anything.

        Averaged over the sessions that actually loaded each item, so an item
        present in only half the sessions counts for half its size.
        """
        if not self.sessions:
            return 0
        return sum(i.chars * i.sessions_present for i in self.dead) // self.sessions

    @property
    def total_chars_per_session(self) -> int:
        if not self.sessions:
            return 0
        return sum(i.chars * i.sessions_present for i in self.items) // self.sessions

    @property
    def dead_share(self) -> float:
        total = self.total_chars_per_session
        return self.dead_chars_per_session / total if total else 0.0


def _usage_for_kind(session: Session, kind: str) -> dict[str, int]:
    """Calls recorded in ``session``, keyed by normalised name."""
    if kind == "skill":
        source = session.skill_calls
    elif kind == "agent":
        source = session.agent_calls
    elif kind == "mcp":
        source = session.mcp_servers_used()
    else:
        source = session.tool_calls
    return {match_key(name): count for name, count in source.items()}


def build(sessions: Iterable[Session]) -> Report:
    """Aggregate sessions into a single report."""
    report = Report()
    index: dict[tuple[str, str], Item] = {}
    # `sessions` may be a generator over many megabytes of transcript, so
    # everything is accumulated in this single pass.
    hook_runs: list[HookRun] = []

    for session in sessions:
        report.sessions += 1
        hook_runs.extend(session.hook_runs)
        if session.project:
            report.projects.add(session.project)

        report.total_input_tokens += session.input_tokens
        report.total_output_tokens += session.output_tokens
        report.total_cache_read_tokens += session.cache_read_tokens
        report.total_cache_creation_tokens += session.cache_creation_tokens
        report.total_seconds += session.duration_seconds
        report.total_tool_calls += sum(session.tool_calls.values())

        if not session.listings:
            report.sessions_without_inventory += 1

        for kind in KINDS:
            listing = session.listings.get(kind)
            if listing is None:
                continue
            usage = _usage_for_kind(session, kind)

            for name, chars in listing.items.items():
                key = (kind, name)
                item = index.get(key)
                if item is None:
                    item = Item(kind=kind, name=name)
                    index[key] = item
                # An item's size is a property of the item, not of the session,
                # so keep the largest observation rather than summing.
                item.chars = max(item.chars, chars)
                item.sessions_present += 1

                calls = usage.get(match_key(name), 0)
                if calls:
                    item.calls += calls
                    item.sessions_used += 1

    report.items = sorted(
        index.values(), key=lambda i: (-i.chars * i.sessions_present, i.kind, i.name)
    )
    report.hooks = _hook_stats(hook_runs)
    return report


def _hook_stats(runs: Iterable[HookRun]) -> list[HookStat]:
    stats: dict[tuple[str, str], HookStat] = {}
    for run in runs:
        key = (run.event, run.command)
        stat = stats.get(key)
        if stat is None:
            stat = HookStat(command=run.command, event=run.event)
            stats[key] = stat
        stat.runs += 1
        stat.total_ms += run.duration_ms
        if run.errored:
            stat.errors += 1
    return sorted(stats.values(), key=lambda s: -s.total_ms)
