"""Tests for the inventory/usage join.

The claim "never used" is the whole product, so the risk that matters is a false
positive: telling someone to delete something they rely on because a name did
not compare equal. Most of these tests are about name matching.
"""

from __future__ import annotations

import io
import json
import unittest

from deadweight.analyze import build, match_key
from deadweight.report import MEANINGFUL_SESSIONS, render, render_json, share
from deadweight.transcripts import HookRun, Listing, Session


def session(**kwargs) -> Session:
    base = Session(path=None, project="/home/user/repo")  # type: ignore[arg-type]
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


def listing(kind: str, items: dict[str, int]) -> Listing:
    return Listing(kind=kind, items=dict(items), total_chars=sum(items.values()))


class NameMatching(unittest.TestCase):
    def test_punctuation_and_case_are_ignored(self):
        self.assertEqual(match_key("Claude Code Remote"), match_key("claude_code_remote"))
        self.assertEqual(match_key("Google-Drive"), match_key("google_drive"))

    def test_distinct_names_stay_distinct(self):
        self.assertNotEqual(match_key("github"), match_key("gitlab"))


class DeadDetection(unittest.TestCase):
    def test_unused_item_is_dead(self):
        report = build([session(listings={"skill": listing("skill", {"unused": 500})})])
        self.assertEqual([i.name for i in report.dead], ["unused"])

    def test_used_item_is_not_dead(self):
        report = build(
            [
                session(
                    listings={"skill": listing("skill", {"used": 500})},
                    skill_calls={"used": 3},
                )
            ]
        )
        self.assertEqual(report.dead, [])
        self.assertEqual(report.items[0].calls, 3)

    def test_mcp_server_matched_through_its_tool_names(self):
        """The listing says "Claude Code Remote"; calls say mcp__Claude_Code_Remote__x."""
        report = build(
            [
                session(
                    listings={"mcp": listing("mcp", {"Claude Code Remote": 2000})},
                    tool_calls={"mcp__Claude_Code_Remote__add_repo": 4},
                )
            ]
        )
        self.assertEqual(report.dead, [])
        self.assertEqual(report.items[0].calls, 4)

    def test_unused_mcp_server_is_still_reported(self):
        report = build(
            [
                session(
                    listings={"mcp": listing("mcp", {"github": 1800, "jira": 9000})},
                    tool_calls={"mcp__github__search_code": 2},
                )
            ]
        )
        self.assertEqual([i.name for i in report.dead], ["jira"])

    def test_agent_matched_through_task_calls(self):
        report = build(
            [
                session(
                    listings={"agent": listing("agent", {"Explore": 400})},
                    agent_calls={"Explore": 1},
                )
            ]
        )
        self.assertEqual(report.dead, [])

    def test_usage_accumulates_across_sessions(self):
        skills = {"skill": listing("skill", {"deploy": 300})}
        report = build(
            [
                session(listings=skills),
                session(listings=skills, skill_calls={"deploy": 2}),
                session(listings=skills),
            ]
        )
        item = report.items[0]
        self.assertEqual(item.sessions_present, 3)
        self.assertEqual(item.sessions_used, 1)
        self.assertEqual(item.calls, 2)
        self.assertFalse(item.is_dead)


class BuiltIns(unittest.TestCase):
    """Unused built-in agents are reported, but never as recoverable savings."""

    def test_builtin_agent_is_not_counted_as_removable(self):
        report = build([session(listings={"agent": listing("agent", {"Explore": 500})})])
        self.assertEqual(report.dead, [])
        self.assertEqual([i.name for i in report.dead_builtins], ["Explore"])
        self.assertEqual(report.dead_chars_per_session, 0)

    def test_builtin_names_are_matched_loosely(self):
        for name in ("general-purpose", "General Purpose", "general_purpose"):
            with self.subTest(name=name):
                report = build([session(listings={"agent": listing("agent", {name: 100})})])
                self.assertEqual(report.dead, [])

    def test_a_user_agent_is_still_removable(self):
        report = build([session(listings={"agent": listing("agent", {"Paid Media Auditor": 400})})])
        self.assertEqual([i.name for i in report.dead], ["Paid Media Auditor"])

    def test_a_skill_named_like_a_builtin_agent_is_still_removable(self):
        """The exemption is scoped to agents; skills are always deletable."""
        report = build([session(listings={"skill": listing("skill", {"Explore": 400})})])
        self.assertEqual([i.name for i in report.dead], ["Explore"])

    def test_a_used_builtin_is_neither_dead_nor_listed(self):
        report = build(
            [
                session(
                    listings={"agent": listing("agent", {"Explore": 500})},
                    agent_calls={"Explore": 2},
                )
            ]
        )
        self.assertEqual(report.dead, [])
        self.assertEqual(report.dead_builtins, [])

    def test_share_excludes_builtins_from_the_numerator(self):
        report = build(
            [
                session(
                    listings={
                        "agent": listing("agent", {"Explore": 500}),
                        "skill": listing("skill", {"mine": 500}),
                    }
                )
            ]
        )
        self.assertAlmostEqual(report.dead_share, 0.5)


class CostMath(unittest.TestCase):
    def test_size_is_not_summed_across_sessions(self):
        """An item's size is a property of the item, not of how often it loads."""
        skills = {"skill": listing("skill", {"deploy": 300})}
        report = build([session(listings=skills) for _ in range(5)])
        self.assertEqual(report.items[0].chars, 300)

    def test_dead_cost_is_weighted_by_presence(self):
        # Present in one of two sessions, so it costs half as much on average.
        report = build(
            [
                session(listings={"skill": listing("skill", {"a": 400})}),
                session(listings={}),
            ]
        )
        self.assertEqual(report.dead_chars_per_session, 200)

    def test_share_of_inventory(self):
        report = build(
            [
                session(
                    listings={"skill": listing("skill", {"dead": 750, "alive": 250})},
                    skill_calls={"alive": 1},
                )
            ]
        )
        self.assertAlmostEqual(report.dead_share, 0.75)

    def test_cost_per_call(self):
        report = build(
            [
                session(
                    listings={"skill": listing("skill", {"a": 100})},
                    skill_calls={"a": 4},
                )
            ]
        )
        self.assertAlmostEqual(report.items[0].cost_per_call(), 25.0)

    def test_sessions_without_inventory_are_counted(self):
        report = build([session(listings={}), session(listings={})])
        self.assertEqual(report.sessions_without_inventory, 2)
        self.assertEqual(report.dead_chars_per_session, 0)

    def test_empty_input(self):
        report = build([])
        self.assertEqual(report.sessions, 0)
        self.assertEqual(report.items, [])
        self.assertEqual(report.dead_share, 0.0)


class Hooks(unittest.TestCase):
    def test_runs_are_aggregated_by_command_and_event(self):
        report = build(
            [
                session(
                    hook_runs=[
                        HookRun("fmt.sh", 100, "Stop"),
                        HookRun("fmt.sh", 200, "Stop"),
                        HookRun("lint.sh", 50, "PostToolUse", errored=True),
                    ]
                )
            ]
        )
        by_command = {h.command: h for h in report.hooks}
        self.assertEqual(by_command["fmt.sh"].runs, 2)
        self.assertEqual(by_command["fmt.sh"].total_ms, 300)
        self.assertEqual(by_command["fmt.sh"].average_ms, 150)
        self.assertEqual(by_command["lint.sh"].errors, 1)

    def test_sorted_by_total_cost(self):
        report = build(
            [session(hook_runs=[HookRun("cheap.sh", 5, "Stop"), HookRun("slow.sh", 900, "Stop")])]
        )
        self.assertEqual(report.hooks[0].command, "slow.sh")


class Rendering(unittest.TestCase):
    def render(self, report, **kwargs) -> str:
        buffer = io.StringIO()
        render(report, buffer, **kwargs)
        return buffer.getvalue()

    def test_no_sessions_explains_itself(self):
        self.assertIn("No Claude Code sessions found", self.render(build([])))

    def test_small_sample_is_flagged(self):
        report = build([session(listings={"skill": listing("skill", {"a": 100})})])
        self.assertIn("means little at this sample size", self.render(report))

    def test_large_sample_is_not_flagged(self):
        skills = {"skill": listing("skill", {"a": 100})}
        report = build([session(listings=skills) for _ in range(MEANINGFUL_SESSIONS)])
        self.assertNotIn("sample size", self.render(report))

    def test_all_used_says_so(self):
        report = build(
            [
                session(
                    listings={"skill": listing("skill", {"a": 100})},
                    skill_calls={"a": 1},
                )
            ]
        )
        self.assertIn("Everything you load gets used", self.render(report))

    def test_tools_are_collapsed_into_one_row(self):
        tools = {f"tool{i}": 20 for i in range(50)}
        report = build([session(listings={"tool": listing("tool", tools)})])
        output = self.render(report)
        self.assertIn("50 of 50 never called", output)
        self.assertNotIn("tool7 ", output)

    def test_share_never_rounds_a_near_miss_to_100(self):
        self.assertEqual(share(0.999), "99.9%")
        self.assertEqual(share(1.0), "100%")
        self.assertEqual(share(0.5), "50%")

    def test_builtin_line_is_shown_but_kept_out_of_the_total(self):
        report = build(
            [
                session(
                    listings={
                        "agent": listing("agent", {"Explore": 500}),
                        "skill": listing("skill", {"mine": 100}),
                    }
                )
            ]
        )
        output = self.render(report)
        self.assertIn("built-in agent(s) also went unused", output)
        self.assertIn("cannot be removed", output)
        # The headline saving counts only the removable skill.
        self.assertIn("100 chars", output)

    def test_duration_is_labelled_as_a_span_not_as_time_worked(self):
        report = build([session(listings={"skill": listing("skill", {"a": 100})})])
        output = self.render(report)
        self.assertIn("spanning", output)
        self.assertNotIn("of session time", output)

    def test_json_shape(self):
        report = build(
            [
                session(
                    listings={"skill": listing("skill", {"dead": 400, "alive": 100})},
                    skill_calls={"alive": 2},
                    hook_runs=[HookRun("fmt.sh", 10, "Stop")],
                )
            ]
        )
        buffer = io.StringIO()
        render_json(report, buffer)
        payload = json.loads(buffer.getvalue())

        self.assertEqual(payload["summary"]["sessions"], 1)
        self.assertEqual(payload["summary"]["dead_items"], 1)
        names = {i["name"]: i for i in payload["items"]}
        self.assertTrue(names["dead"]["dead"])
        self.assertFalse(names["alive"]["dead"])
        self.assertEqual(payload["hooks"][0]["command"], "fmt.sh")


if __name__ == "__main__":
    unittest.main()
