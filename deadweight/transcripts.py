"""Read Claude Code session transcripts.

Claude Code writes every session to ``~/.claude/projects/<encoded-cwd>/<uuid>.jsonl``.
Those files are the only record of what your configuration actually *did*, as
opposed to what it promises to do, and nothing reads them for that purpose.

Two kinds of row matter here:

**Inventory attachments** record what was loaded into context at the start of a
session, with the exact text. ``skill_listing`` carries every skill's
description, ``agent_listing_delta`` every subagent, ``mcp_instructions_delta``
every MCP server's instructions, and ``deferred_tools_delta`` the tool roster.
Because the text itself is stored, the context cost of each item is *measured*
rather than guessed.

**Evidence rows** record what happened: ``tool_use`` blocks name every tool that
was actually called, and ``system`` rows with a ``*_hook_summary`` subtype carry
each hook's command, its wall-clock duration, and whether it errored.

Joining the two answers the question this package exists for: which parts of your
setup charge you context on every session without ever doing anything.

Nothing in this module reads message text. Prompts, code and tool output are
skipped entirely - only names, counts, sizes and durations are collected.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: Rough characters-per-token ratio for English prose. Only ever used to present
#: a secondary estimate; every primary figure in this package is a measured
#: character count.
CHARS_PER_TOKEN = 4

#: A listing line looks like ``- name: description``.
_LISTING_LINE = re.compile(r"^\s*-\s+([^:]+):\s*(.*)$")

#: MCP tools are named ``mcp__<server>__<tool>``.
_MCP_TOOL = re.compile(r"^mcp__([^_]+(?:_[^_]+)*?)__(.+)$")


@dataclass
class HookRun:
    """One hook execution, as recorded by Claude Code itself."""

    command: str
    duration_ms: int
    event: str
    errored: bool = False


@dataclass
class Listing:
    """Something loaded into context at session start, broken down by item."""

    kind: str
    #: item name -> characters that item contributes
    items: dict[str, int] = field(default_factory=dict)
    total_chars: int = 0

    def merge(self, other: Listing) -> None:
        for name, chars in other.items.items():
            self.items[name] = max(self.items.get(name, 0), chars)
        self.total_chars = max(self.total_chars, other.total_chars)


@dataclass
class Session:
    """One recorded Claude Code session."""

    path: Path
    session_id: str = ""
    project: str = ""
    version: str = ""
    started: datetime | None = None
    ended: datetime | None = None
    turns: int = 0

    tool_calls: dict[str, int] = field(default_factory=dict)
    skill_calls: dict[str, int] = field(default_factory=dict)
    agent_calls: dict[str, int] = field(default_factory=dict)
    hook_runs: list[HookRun] = field(default_factory=list)
    listings: dict[str, Listing] = field(default_factory=dict)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def duration_seconds(self) -> float:
        if not self.started or not self.ended:
            return 0.0
        return max(0.0, (self.ended - self.started).total_seconds())

    @property
    def context_overhead_chars(self) -> int:
        """Characters of inventory loaded before any work happened."""
        return sum(listing.total_chars for listing in self.listings.values())

    def mcp_servers_used(self) -> dict[str, int]:
        """Calls per MCP server, derived from ``mcp__server__tool`` names."""
        counts: dict[str, int] = {}
        for name, calls in self.tool_calls.items():
            match = _MCP_TOOL.match(name)
            if match:
                counts[match.group(1)] = counts.get(match.group(1), 0) + calls
        return counts


def claude_home() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".claude"


def decode_project(directory_name: str) -> str:
    """Turn ``-home-user-my-repo`` back into a path-ish label.

    Claude Code encodes the working directory by replacing separators with
    dashes, which is lossy: a real dash in a directory name is indistinguishable
    from a separator. The result is therefore a label for grouping and display,
    never a path to open.
    """
    return "/" + directory_name.lstrip("-").replace("-", "/") if directory_name else ""


def _project_key(text: str) -> str:
    """Fold a project name so dashes and slashes compare equal.

    The on-disk encoding cannot distinguish a separator from a literal dash, so
    ``my-repo`` and ``/home/user/my/repo`` have to match each other. Treating
    both characters as the same separator makes the filter work whichever form
    the user types.
    """
    return re.sub(r"[/\-]+", "-", text.lower()).strip("-")


def find_transcripts(root: Path | None = None, project: str | None = None) -> list[Path]:
    """Every session transcript, newest first."""
    base = (root or claude_home()) / "projects"
    if not base.is_dir():
        return []
    files = [p for p in base.glob("*/*.jsonl") if p.is_file()]
    if project:
        needle = _project_key(project)
        files = [p for p in files if needle in _project_key(p.parent.name)]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _split_listing(kind: str, lines: list[str]) -> Listing:
    """Attribute a listing's characters to the item each line describes.

    Two shapes occur in practice and both must be handled:

    * ``- name: description`` entries, where a description containing newlines
      spills onto following lines. Those continuations belong to the entry above
      them, not to an item of their own - reading them as separate items
      invents skills that do not exist.
    * a bare list of names, one per line, which is how the deferred tool roster
      is recorded. Here every line really is its own item.
    """
    listing = Listing(kind=kind)
    entries: dict[str, int] = {}
    dashed = any(line.lstrip().startswith("- ") for line in lines)
    current: str | None = None

    for line in lines:
        if not line.strip():
            continue
        listing.total_chars += len(line)

        if not dashed:
            name = line.strip()
            entries[name] = entries.get(name, 0) + len(line)
            continue

        if line.lstrip().startswith("- "):
            match = _LISTING_LINE.match(line)
            current = match.group(1).strip() if match else line.strip()[2:][:60]
            entries[current] = entries.get(current, 0) + len(line)
        elif current is not None:
            entries[current] = entries.get(current, 0) + len(line)
        else:
            # Text before the first entry; it costs context but belongs to no
            # single item, so it is counted in the total and nowhere else.
            continue

    listing.items = entries
    return listing


def _listing_from_content(kind: str, content: str) -> Listing:
    return _split_listing(kind, content.splitlines())


def _read_attachment(session: Session, attachment: dict) -> None:
    kind = attachment.get("type")

    if kind == "skill_listing":
        content = attachment.get("content")
        if isinstance(content, str):
            found = _listing_from_content("skill", content)
            session.listings.setdefault("skill", Listing("skill")).merge(found)

    elif kind == "agent_listing_delta":
        lines = attachment.get("addedLines")
        if isinstance(lines, list):
            found = _split_listing("agent", [str(line) for line in lines])
            session.listings.setdefault("agent", Listing("agent")).merge(found)

    elif kind == "deferred_tools_delta":
        lines = attachment.get("addedLines")
        names = attachment.get("addedNames")
        if isinstance(lines, list) and lines:
            found = _split_listing("tool", [str(line) for line in lines])
        elif isinstance(names, list):
            # Some versions record only the names; their length is still the
            # honest measure of what they cost.
            found = _split_listing("tool", [f"- {n}" for n in names])
        else:
            found = Listing("tool")
        session.listings.setdefault("tool", Listing("tool")).merge(found)

    elif kind == "mcp_instructions_delta":
        names = attachment.get("addedNames")
        blocks = attachment.get("addedBlocks")
        listing = Listing("mcp")
        if isinstance(names, list) and isinstance(blocks, list):
            for name, block in zip(names, blocks):
                chars = len(str(block))
                listing.items[str(name)] = chars
                listing.total_chars += chars
        session.listings.setdefault("mcp", Listing("mcp")).merge(listing)


def _event_from_subtype(subtype: str) -> str:
    """``stop_hook_summary`` -> ``Stop``."""
    stem = subtype[: -len("_hook_summary")] if subtype.endswith("_hook_summary") else subtype
    return "".join(part.capitalize() for part in stem.split("_")) or "Hook"


def _read_system(session: Session, row: dict) -> None:
    subtype = row.get("subtype")
    infos = row.get("hookInfos")
    if not isinstance(subtype, str) or not isinstance(infos, list):
        return
    errored = bool(row.get("hookErrors"))
    event = _event_from_subtype(subtype)
    for info in infos:
        if not isinstance(info, dict):
            continue
        command = str(info.get("command", "")).strip()
        if not command:
            continue
        duration = info.get("durationMs")
        session.hook_runs.append(
            HookRun(
                command=command,
                duration_ms=int(duration) if isinstance(duration, (int, float)) else 0,
                event=event,
                errored=errored,
            )
        )


def _read_message(session: Session, row: dict) -> None:
    message = row.get("message")
    if not isinstance(message, dict):
        return

    usage = message.get("usage")
    if isinstance(usage, dict):
        session.turns += 1
        for key, attribute in (
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("cache_read_input_tokens", "cache_read_tokens"),
            ("cache_creation_input_tokens", "cache_creation_tokens"),
        ):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                setattr(session, attribute, getattr(session, attribute) + int(value))

    content = message.get("content")
    if not isinstance(content, list):
        return

    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if not isinstance(name, str) or not name:
            continue
        session.tool_calls[name] = session.tool_calls.get(name, 0) + 1

        payload = block.get("input")
        if not isinstance(payload, dict):
            continue

        # A skill invoked through the Skill tool names itself in the input; this
        # is the only place a skill's *use* is recorded.
        if name == "Skill":
            skill = payload.get("skill")
            if isinstance(skill, str) and skill:
                session.skill_calls[skill] = session.skill_calls.get(skill, 0) + 1
        elif name in ("Task", "Agent"):
            agent = payload.get("subagent_type")
            if isinstance(agent, str) and agent:
                session.agent_calls[agent] = session.agent_calls.get(agent, 0) + 1


def parse(path: Path) -> Session:
    """Read one transcript. Unreadable or partial files yield what was parsed."""
    session = Session(path=path, project=decode_project(path.parent.name))
    session.session_id = path.stem

    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return session

    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                # A session still being written can end mid-line; the rows
                # before it are still worth having.
                continue
            if not isinstance(row, dict):
                continue

            stamp = _parse_time(row.get("timestamp"))
            if stamp:
                if session.started is None or stamp < session.started:
                    session.started = stamp
                if session.ended is None or stamp > session.ended:
                    session.ended = stamp

            version = row.get("version")
            if isinstance(version, str) and version:
                session.version = version
            sid = row.get("sessionId")
            if isinstance(sid, str) and sid:
                session.session_id = sid

            row_type = row.get("type")
            if row_type == "system":
                _read_system(session, row)
                continue

            attachment = row.get("attachment")
            if isinstance(attachment, dict):
                _read_attachment(session, attachment)
                continue

            if row_type in ("assistant", "user"):
                _read_message(session, row)

    return session


def load(
    root: Path | None = None,
    *,
    project: str | None = None,
    limit: int | None = None,
    since_days: int | None = None,
) -> Iterator[Session]:
    """Parse transcripts newest-first, optionally narrowed."""
    cutoff = None
    if since_days is not None:
        cutoff = datetime.now(timezone.utc).timestamp() - since_days * 86400

    count = 0
    for path in find_transcripts(root, project):
        if cutoff is not None and path.stat().st_mtime < cutoff:
            continue
        yield parse(path)
        count += 1
        if limit is not None and count >= limit:
            return
