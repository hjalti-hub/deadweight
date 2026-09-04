"""Tests for transcript parsing.

The transcript format is not a published API, so these tests pin the shapes this
package actually relies on, taken from real Claude Code output. If a future
version changes one, a test here should be the thing that notices.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from deadweight.transcripts import HookRun, decode_project, find_transcripts, parse

SKILL_LISTING = (
    "- summarize-diff: Summarizes uncommitted changes. Use when the user asks what changed.\n"
    "- claude-api: Reference for the Claude API.\n"
    "TRIGGER - read this before opening the file; it continues the entry above.\n"
    "SKIP only when another provider is in play.\n"
    "- dataviz: Use this before writing any chart code.\n"
)


def rows_for(**overrides) -> list[dict]:
    """A minimal but realistic session."""
    base: list[dict] = [
        {
            "type": "user",
            "timestamp": "2026-09-01T10:00:00.000Z",
            "sessionId": "abc123",
            "version": "2.1.260",
            "message": {"role": "user", "content": "please do the thing"},
        },
        {
            "attachment": {
                "type": "skill_listing",
                "isInitial": True,
                "skillCount": 3,
                "names": ["summarize-diff", "claude-api", "dataviz"],
                "content": SKILL_LISTING,
            }
        },
        {
            "attachment": {
                "type": "deferred_tools_delta",
                "addedNames": ["CronCreate", "CronDelete", "WebSearch"],
                "addedLines": ["CronCreate", "CronDelete", "WebSearch"],
            }
        },
        {
            "attachment": {
                "type": "mcp_instructions_delta",
                "addedNames": ["github", "Claude Code Remote"],
                "addedBlocks": ["## github\nUse it for PRs.", "## remote\nSpawn sessions."],
            }
        },
        {
            "attachment": {
                "type": "agent_listing_delta",
                "addedTypes": ["Explore", "Plan"],
                "addedLines": ["- Explore: Read-only search agent.", "- Plan: Architect agent."],
            }
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-01T10:05:00.000Z",
            "message": {
                "role": "assistant",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 50,
                },
                "content": [
                    {"type": "text", "text": "a secret plan nobody should read"},
                    {"type": "tool_use", "id": "t1", "name": "WebSearch", "input": {"query": "x"}},
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "mcp__Claude_Code_Remote__add_repo",
                        "input": {},
                    },
                    {
                        "type": "tool_use",
                        "id": "t3",
                        "name": "Skill",
                        "input": {"skill": "summarize-diff"},
                    },
                    {
                        "type": "tool_use",
                        "id": "t4",
                        "name": "Task",
                        "input": {"subagent_type": "Explore", "prompt": "look"},
                    },
                ],
            },
        },
        {
            "type": "system",
            "subtype": "stop_hook_summary",
            "timestamp": "2026-09-01T10:06:00.000Z",
            "hookCount": 2,
            "hookInfos": [
                {"command": "~/.claude/fmt.sh", "durationMs": 120},
                {"command": "~/.claude/lint.sh", "durationMs": 80},
            ],
            "hookErrors": [],
        },
    ]
    base.extend(overrides.get("extra", []))
    return base


class TranscriptCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.projects = self.root / "projects" / "-home-user-my-repo"
        self.projects.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rows: list, name: str = "session.jsonl") -> Path:
        path = self.projects / name
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write((row if isinstance(row, str) else json.dumps(row)) + "\n")
        return path


class Listings(TranscriptCase):
    def test_multiline_description_stays_one_item(self):
        """Regression: continuation lines were becoming phantom skills."""
        session = parse(self.write(rows_for()))
        skills = session.listings["skill"]
        self.assertEqual(sorted(skills.items), ["claude-api", "dataviz", "summarize-diff"])

    def test_continuation_text_is_charged_to_its_entry(self):
        session = parse(self.write(rows_for()))
        skills = session.listings["skill"]
        # claude-api's own line is short; its two continuation lines are not.
        self.assertGreater(skills.items["claude-api"], 100)

    def test_bare_name_listing_gives_one_item_per_line(self):
        """Regression: the deferred tool roster is names, not `- name: desc`."""
        session = parse(self.write(rows_for()))
        tools = session.listings["tool"]
        self.assertEqual(sorted(tools.items), ["CronCreate", "CronDelete", "WebSearch"])

    def test_mcp_blocks_are_measured_per_server(self):
        session = parse(self.write(rows_for()))
        mcp = session.listings["mcp"]
        self.assertEqual(sorted(mcp.items), ["Claude Code Remote", "github"])
        self.assertEqual(mcp.items["github"], len("## github\nUse it for PRs."))

    def test_agents_are_parsed(self):
        session = parse(self.write(rows_for()))
        self.assertEqual(sorted(session.listings["agent"].items), ["Explore", "Plan"])

    def test_overhead_is_the_sum_of_listings(self):
        session = parse(self.write(rows_for()))
        expected = sum(listing.total_chars for listing in session.listings.values())
        self.assertEqual(session.context_overhead_chars, expected)
        self.assertGreater(session.context_overhead_chars, 0)


class Evidence(TranscriptCase):
    def test_tool_calls_are_counted(self):
        session = parse(self.write(rows_for()))
        self.assertEqual(session.tool_calls["WebSearch"], 1)
        self.assertEqual(session.tool_calls["mcp__Claude_Code_Remote__add_repo"], 1)

    def test_skill_invocation_is_attributed(self):
        session = parse(self.write(rows_for()))
        self.assertEqual(session.skill_calls, {"summarize-diff": 1})

    def test_subagent_invocation_is_attributed(self):
        session = parse(self.write(rows_for()))
        self.assertEqual(session.agent_calls, {"Explore": 1})

    def test_mcp_server_is_derived_from_the_tool_name(self):
        session = parse(self.write(rows_for()))
        self.assertEqual(session.mcp_servers_used(), {"Claude_Code_Remote": 1})

    def test_hook_runs_carry_command_duration_and_event(self):
        session = parse(self.write(rows_for()))
        self.assertEqual(len(session.hook_runs), 2)
        run = session.hook_runs[0]
        self.assertIsInstance(run, HookRun)
        self.assertEqual(run.event, "Stop")
        self.assertEqual(run.duration_ms, 120)
        self.assertFalse(run.errored)

    def test_hook_errors_are_flagged(self):
        rows = rows_for()
        rows[-1]["hookErrors"] = ["boom"]
        session = parse(self.write(rows))
        self.assertTrue(all(run.errored for run in session.hook_runs))

    def test_tokens_and_timing(self):
        session = parse(self.write(rows_for()))
        self.assertEqual(session.output_tokens, 20)
        self.assertEqual(session.cache_read_tokens, 100)
        self.assertEqual(session.turns, 1)
        self.assertEqual(session.duration_seconds, 360.0)

    def test_metadata(self):
        session = parse(self.write(rows_for()))
        self.assertEqual(session.session_id, "abc123")
        self.assertEqual(session.version, "2.1.260")


class Privacy(TranscriptCase):
    """No message text may survive parsing - not prompts, not tool inputs."""

    def test_no_prompt_or_tool_input_is_retained(self):
        session = parse(self.write(rows_for()))
        blob = repr(session)
        for secret in (
            "please do the thing",
            "a secret plan nobody should read",
            "look",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, blob)


class Robustness(TranscriptCase):
    def test_malformed_lines_are_skipped(self):
        rows = rows_for()
        path = self.write([*rows[:2], "{ this is not json", *rows[2:]])
        session = parse(path)
        self.assertIn("skill", session.listings)

    def test_truncated_final_line_is_tolerated(self):
        rows = rows_for()
        path = self.write([*rows, '{"type": "assis'])
        session = parse(path)
        self.assertEqual(session.tool_calls["WebSearch"], 1)

    def test_missing_file_yields_an_empty_session(self):
        session = parse(self.projects / "nope.jsonl")
        self.assertEqual(session.tool_calls, {})
        self.assertEqual(session.listings, {})

    def test_empty_file(self):
        session = parse(self.write([]))
        self.assertEqual(session.turns, 0)

    def test_rows_that_are_not_objects(self):
        session = parse(self.write(["[1,2,3]", "null", *[json.dumps(r) for r in rows_for()]]))
        self.assertEqual(session.tool_calls["WebSearch"], 1)


class Discovery(TranscriptCase):
    def test_finds_transcripts_under_projects(self):
        self.write(rows_for(), "one.jsonl")
        self.write(rows_for(), "two.jsonl")
        found = find_transcripts(self.root)
        self.assertEqual(len(found), 2)

    def test_project_filter_accepts_either_separator(self):
        """The on-disk encoding loses the difference between - and /."""
        self.write(rows_for())
        for needle in ("my-repo", "my/repo", "/home/user/my-repo", "USER"):
            with self.subTest(needle=needle):
                self.assertEqual(len(find_transcripts(self.root, project=needle)), 1)
        self.assertEqual(len(find_transcripts(self.root, project="other")), 0)

    def test_missing_projects_directory(self):
        self.assertEqual(find_transcripts(Path("/nonexistent/xyz")), [])

    def test_decode_project(self):
        self.assertEqual(decode_project("-home-user-repo"), "/home/user/repo")
        self.assertEqual(decode_project(""), "")


if __name__ == "__main__":
    unittest.main()
