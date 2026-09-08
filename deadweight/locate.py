"""Find which of the things in a report exist as files you could delete.

Transcripts record what got loaded, never where it came from. That is fine for
measuring cost and useless for acting on it: the report's whole promise is
"delete what you never use", and a name alone does not tell you whether there is
anything to delete.

Most of what loads into a session is not a file in your Claude directory. It is
delivered by the app - skills and subagents that arrive at runtime, the same way
the built-in subagents do. On one real setup, 121 unused items were reported as
recoverable when only one of them was. That is the same failure as telling
someone their skill collides with a copy of itself inside a marketplace
catalogue: a true measurement wrapped in advice they cannot follow.

So the names are looked up on disk. What is found is removable and counted in
the headline. What is not found is still reported - it is real context, really
being spent - but under a heading that says there is nothing to delete.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .analyze import match_key

#: Depth of ``plugins/cache/<marketplace>/<plugin>/<version>/`` before the
#: plugin's own ``skills/`` and ``agents/`` directories appear. Versions are not
#: always present, so both depths are searched.
_CACHE_GLOBS = ("plugins/cache/*/*/*", "plugins/cache/*/*")


@dataclass(frozen=True)
class Inventory:
    """Normalised names of skills and agents found on disk."""

    skills: frozenset[str] = frozenset()
    agents: frozenset[str] = frozenset()
    #: Directories actually searched. Empty means nothing could be checked, in
    #: which case no claim is made either way.
    searched: tuple[Path, ...] = ()

    @property
    def usable(self) -> bool:
        return bool(self.searched)

    def holds(self, kind: str, name: str) -> bool | None:
        """Whether ``name`` was found. ``None`` when no claim can be made.

        MCP servers are always removable - they live in a settings file, not in
        a skills directory - so they are never reported as unlocatable. Tools
        are not files at all.
        """
        if not self.usable or kind not in ("skill", "agent"):
            return None
        pool = self.skills if kind == "skill" else self.agents
        return match_key(name) in pool


def _skill_names(root: Path) -> set[str]:
    """Skill directory names under a ``skills/`` root."""
    found = set()
    for path in root.glob("*/SKILL.md"):
        if path.is_file():
            found.add(match_key(path.parent.name))
    return found


def _agent_names(root: Path) -> set[str]:
    found = set()
    for path in root.glob("*.md"):
        if path.is_file():
            found.add(match_key(path.stem))
    return found


def find(config_dir: Path, projects: set[str] | None = None) -> Inventory:
    """Collect skill and agent names from every place a file could live.

    ``projects`` are the working directories recorded in the transcripts. They
    are lossy - Claude Code encodes them with dashes, so a real dash and a path
    separator are indistinguishable - and one that does not resolve is skipped
    rather than guessed at. A project skill that cannot be located therefore
    lands in the unlocatable pile, which understates what you could remove.
    Understating it is the safe direction: the alternative is the bug this
    module exists to fix.
    """
    skills: set[str] = set()
    agents: set[str] = set()
    searched: list[Path] = []

    def sweep(base: Path) -> None:
        if not base.is_dir():
            return
        searched.append(base)
        skills.update(_skill_names(base / "skills"))
        agents.update(_agent_names(base / "agents"))

    config_dir = config_dir.expanduser()
    sweep(config_dir)
    for pattern in _CACHE_GLOBS:
        for plugin_dir in sorted(config_dir.glob(pattern)):
            sweep(plugin_dir)

    for project in sorted(projects or ()):
        candidate = Path(project).expanduser()
        if candidate.is_dir():
            sweep(candidate / ".claude")

    return Inventory(frozenset(skills), frozenset(agents), tuple(searched))


def annotate(report, inventory: Inventory) -> None:
    """Record on each item whether a file for it was found."""
    for item in report.items:
        item.on_disk = inventory.holds(item.kind, item.name)
