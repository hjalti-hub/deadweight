# deadweight

### Your Claude Code setup charges rent. Some of it never shows up for work.

Every skill, subagent and MCP server you install gets loaded into context at the
start of **every single session** — whether you use it or not. You pay for it in
tokens, every time, forever.

Nothing tells you which ones you actually use.

```console
$ deadweight

deadweight 62 sessions · /home/user/my-repo

DEAD WEIGHT loaded every session, never called
  mcp server jira                  11,412 chars  (~2,853 tok)   never used
  mcp server Artlist                2,072 chars  (~518 tok)     never used
  skill      dataviz                1,447 chars  (~361 tok)     never used
  agent      data-migrator          1,097 chars  (~274 tok)     never used
  skill      legacy-deploy            340 chars  (~85 tok)      never used
  … and 12 more
  tools      197 of 201 never called 5,998 chars  (~1,499 tok)  names only; schemas load on demand

  22,410 chars (~5,602 tokens) per session for nothing — 61% of the 36,500 you load

HOOKS wall-clock you pay on every run
  Stop          ~/.claude/stop-hook-git-check.sh   1,842 runs   4m 21s  avg 142ms
  PostToolUse   ~/.claude/format.sh                  611 runs   1m 02s  avg 101ms
```

Then you delete what you never use, and get that context back.

There is nothing to install to find out:

```bash
git clone https://github.com/hjalti-hub/deadweight && cd deadweight
python3 -m deadweight
```

Zero dependencies. Nothing leaves your machine.

---

## How it knows

Claude Code already writes down everything you need. Every session is logged to
`~/.claude/projects/*/*.jsonl`, and those files record two things nobody reads
together:

**What got loaded.** Each session's transcript stores the *actual text* of the
skill listing, the subagent listing, the MCP instruction blocks and the tool
roster. So the cost of each item is **measured, not estimated** — `deadweight`
counts the characters Claude Code actually put in your context.

**What got used.** Every `tool_use` block names the tool that ran. Skills name
themselves when invoked, subagents name themselves when spawned, and MCP tools
carry their server in the name (`mcp__jira__create_issue`).

Join those two and you get the only question that matters: *what am I paying for
that has never once done anything?*

## Privacy

This reads your session history, so it's worth being precise about what it
touches.

**It never reads message text.** Not your prompts, not Claude's replies, not tool
inputs, not file contents. The parser skips them entirely — it collects names,
counts, character totals and durations, and nothing else. There's a test that
[asserts exactly this](tests/test_transcripts.py): parse a transcript containing
secrets, then assert none of them survive anywhere in the result.

**Nothing leaves your machine.** No network calls, no telemetry, no API key. It's
a local file reader.

## Install

A clone is enough — run it as a module from inside the checkout:

```bash
git clone https://github.com/hjalti-hub/deadweight && cd deadweight
python3 -m deadweight
```

To run it from any directory, install it from that clone:

```bash
pip install .        # or: pipx install .
```

Requires Python 3.9+. Not on PyPI yet, so `pip install deadweight` by name does
not work — install from the clone as above.

## Use

Once installed you can drop the `python3 -m` prefix; from a clone, keep it.

```bash
deadweight                     # weigh every session on this machine
deadweight --project my-repo   # only sessions from a matching directory
deadweight --since 30          # only the last 30 days
deadweight --used              # also show what is earning its keep
deadweight --all               # every row, not just the top ones
deadweight --json              # machine-readable
```

`--project` accepts either separator, so `my-repo` and `my/repo` both work — the
on-disk encoding can't tell them apart.

## What it measures

| | |
| :--- | :--- |
| **MCP servers** | Instruction block size per server, and calls resolved from `mcp__server__tool` names. Usually the biggest single win. |
| **Skills** | The description each one contributes to the skill listing, against how often it was invoked. |
| **Subagents** | Listing size against `Task` invocations by `subagent_type`. |
| **Tools** | The deferred roster. Cheap by design — names only, schemas load on demand — so these are collapsed into one row. |
| **Hooks** | Runs, total wall-clock, average duration and error count, from Claude Code's own `hookInfos` records. |

That last row is worth calling out. Hook cost is otherwise completely invisible:
nothing fails, everything just gets slower, gradually. These are the durations
Claude Code itself recorded.

## Honest limits

- **"Never used" is only as good as your history.** In 3 sessions it means
  nothing; in 200 it means a lot. The report tells you which situation you're in
  and warns you below 5 sessions.
- **It measures what the transcript records.** MCP *instruction blocks* are
  measured exactly. An MCP server's tool schemas may cost more on top of that,
  and are not separately itemised.
- **Deferred tools are already cheap.** Claude Code lists them by name and loads
  schemas on demand, so 200 tools cost a few thousand characters, not hundreds of
  thousands. Don't go deleting tools expecting a windfall.
- **Something rarely used may still be worth keeping.** This tells you the price;
  whether it's worth paying is your call. Sort by `cost_per_call` in the JSON if
  you want the borderline cases.
- **Project names are lossy.** Claude Code encodes the working directory with
  dashes, so a real dash and a path separator look identical. Project labels are
  for grouping, not for opening.

## Library use

```python
from deadweight.transcripts import load
from deadweight.analyze import build

report = build(load(since_days=90))
for item in report.dead:
    print(item.kind, item.name, item.chars, "chars/session, never called")
```

## Related

[whyskill](https://github.com/hjalti-hub/whyskill) answers the other half of
the same question. It reads your configuration and proves a skill *can't* fire —
truncated descriptions, name collisions, shadowing. This one reads your history
and shows what *doesn't* fire, and what that costs you.

Static proof, and empirical evidence.

[whyrule](https://github.com/hjalti-hub/whyrule) applies the same static half to
the instructions themselves: the CLAUDE.md and `.claude/rules/` files Claude
loads every session, and the ones it never receives — text stripped before
injection, imports resolving to nothing, rules that contradict each other once
they are concatenated.

## Contributing

The transcript format isn't a published API, so the most useful contribution is a
failing test with a real (redacted) transcript shape that this parser gets wrong.

```bash
python3 -m unittest discover -s tests -t .
```

## License

MIT
