"""Tests for deciding which dead weight is actually removable.

The report's promise is "delete what you never use, and get that context back".
That promise is only honest for things that exist as files. Most of what a
session loads does not: it arrives from the app at runtime, the same way the
built-in subagents do.

Measured on one real setup: 121 unused items reported as recoverable, of which
exactly one - an orphaned MCP server - actually was. The rest had no file
anywhere under the Claude directory.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from deadweight.analyze import Item, Report
from deadweight.locate import Inventory, annotate, find


class Locating(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp()) / ".claude"
        self.home.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.home.parent, ignore_errors=True)

    def touch(self, relative: str) -> Path:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nname: x\n---\n", encoding="utf-8")
        return path

    def test_finds_personal_skills_and_agents(self):
        self.touch("skills/deploy/SKILL.md")
        self.touch("agents/reviewer.md")

        inventory = find(self.home)
        self.assertEqual(inventory.holds("skill", "deploy"), True)
        self.assertEqual(inventory.holds("agent", "reviewer"), True)
        self.assertEqual(inventory.holds("skill", "never-installed"), False)

    def test_finds_skills_inside_installed_plugins(self):
        """Plugin payloads live at cache/<marketplace>/<plugin>/<version>/."""
        self.touch("plugins/cache/mkt/toolkit/1.0.0/skills/deploy/SKILL.md")
        self.assertEqual(find(self.home).holds("skill", "deploy"), True)

    def test_finds_skills_in_an_unversioned_plugin(self):
        self.touch("plugins/cache/mkt/toolkit/skills/deploy/SKILL.md")
        self.assertEqual(find(self.home).holds("skill", "deploy"), True)

    def test_a_marketplace_catalogue_is_not_searched(self):
        """Catalogues hold plugins you have not installed.

        Counting them would make an uninstalled plugin look removable, which
        is the mirror image of the bug this module fixes.
        """
        self.touch("plugins/marketplaces/mkt/plugins/thing/skills/deploy/SKILL.md")
        self.assertEqual(find(self.home).holds("skill", "deploy"), False)

    def test_project_skills_are_found_when_the_path_resolves(self):
        project = Path(tempfile.mkdtemp())
        try:
            skill = project / ".claude" / "skills" / "local" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("---\nname: local\n---\n", encoding="utf-8")
            inventory = find(self.home, {str(project)})
            self.assertEqual(inventory.holds("skill", "local"), True)
        finally:
            shutil.rmtree(project, ignore_errors=True)

    def test_an_unresolvable_project_is_skipped_not_guessed(self):
        """Project labels are lossy, so a name that is not a directory is
        skipped. That understates what is removable, which is the safe way to
        be wrong here."""
        inventory = find(self.home, {"-home-user-some-repo"})
        self.assertEqual(inventory.holds("skill", "anything"), False)

    def test_names_are_matched_loosely(self):
        self.touch("skills/Deploy_App/SKILL.md")
        self.assertEqual(find(self.home).holds("skill", "deploy-app"), True)

    def test_mcp_servers_are_never_unlocatable(self):
        """They live in a settings file, not a skills directory, so removing
        one is always possible even though no SKILL.md exists for it."""
        self.touch("skills/deploy/SKILL.md")
        self.assertIsNone(find(self.home).holds("mcp", "some-server"))

    def test_nothing_searched_means_no_claim(self):
        """With no Claude directory there is no evidence either way, and
        silently marking everything unremovable would hide real waste."""
        empty = Inventory()
        self.assertFalse(empty.usable)
        self.assertIsNone(empty.holds("skill", "deploy"))


class Annotating(unittest.TestCase):
    def report(self, *items: Item) -> Report:
        report = Report(sessions=30, items=list(items))
        return report

    def dead(self, kind: str, name: str, chars: int) -> Item:
        return Item(kind=kind, name=name, chars=chars, sessions_present=30, calls=0)

    def test_the_headline_counts_only_what_can_be_removed(self):
        """The shape of the real finding: one removable server, and a pile of
        app-provided skills that were being counted as recoverable."""
        on_disk = self.dead("skill", "mine", 1_000)
        server = self.dead("mcp", "d14bb057-2546-41a6-8aea-55b2ad4130ff", 2_088)
        provided = [
            self.dead("skill", "anthropic-skills", 4_483),
            self.dead("skill", "firecrawl-research-index", 1_034),
            self.dead("agent", "Podcast Strategist", 357),
        ]
        report = self.report(on_disk, server, *provided)

        annotate(report, Inventory(frozenset({"mine"}), frozenset(), (Path("/x"),)))

        self.assertEqual(
            sorted(i.name for i in report.dead),
            ["d14bb057-2546-41a6-8aea-55b2ad4130ff", "mine"],
        )
        self.assertEqual(
            sorted(i.name for i in report.dead_elsewhere),
            ["Podcast Strategist", "anthropic-skills", "firecrawl-research-index"],
        )
        self.assertEqual(report.dead_chars_per_session, 3_088)

    def test_nothing_moves_when_no_directory_was_searched(self):
        items = [self.dead("skill", "a", 100), self.dead("agent", "b", 200)]
        report = self.report(*items)

        annotate(report, Inventory())

        self.assertEqual(len(report.dead), 2)
        self.assertEqual(report.dead_elsewhere, [])

    def test_built_in_agents_stay_in_their_own_bucket(self):
        """A built-in has no file either, but it already had a home in the
        report and should not be double-counted into the new one."""
        report = self.report(self.dead("agent", "Explore", 300))

        annotate(report, Inventory(frozenset(), frozenset(), (Path("/x"),)))

        self.assertEqual([i.name for i in report.dead_builtins], ["Explore"])
        self.assertEqual(report.dead_elsewhere, [])
        self.assertEqual(report.dead, [])


if __name__ == "__main__":
    unittest.main()
