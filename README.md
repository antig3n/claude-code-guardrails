# claude-code-guardrails

Deterministic guardrails for [Claude Code](https://docs.claude.com/en/docs/claude-code) agents, built as small, fail-open hooks with explicit policies.

When AI agents write and ship code all day, the costly failures are rarely the model's intelligence. They are workflow failures: a task declared "done" with nothing to show for it, an expensive sub-agent spawned for a job a cheaper one could do, a session that forgets where it was. Prompts alone do not prevent these. Hooks do, because they run outside the model and cannot be talked out of a rule.

## The four hooks

| Hook | Event | What it enforces |
|---|---|---|
| `agent_guard` | `PreToolUse` (Agent) | No sub-agent without a written justification line, an explicit model, a model allowed for that role, and quota left. Denies with a reason the agent can act on. |
| `stop_guard` | `Stop` | No turn can end with a task marked `done` unless it references evidence: a non-empty proof file or a test command that exited 0. Blocks at most twice per turn, so it can never trap the session. |
| `prompt_context` | `UserPromptSubmit` | Injects current quota usage and in-progress tasks into every prompt, so the agent always knows its budget and its open work. |
| `session_resume` | `SessionStart` | Restores a short state note when a session starts or resumes. |

Plus `routing.select_executor`, a data-driven router that picks the first executor (local model, budget model, strong model) still below its quota threshold for a given task type.

## Engineering choices

A hook runs on every agent action, so it must never become the failure itself:

- **Fail open, always.** Errors, invalid input and timeouts exit 0 with no output. A broken guard lets work through, it never freezes the session.
- **Hard time budget.** 1.2 s by default, started before imports and covering stdin, config, locks, file I/O and output. Child processes are killed by process group.
- **Bounded everything.** 64 KB input cap, atomic single-write output under the pipe limit, path traversal and symlink escapes rejected.
- **Private audit trail.** Rotating JSONL with only timestamp, hook name, outcome and duration. It never stores prompts, briefs or session ids.
- **Concurrency-safe state.** Per-session files with hashed names, atomic writes under non-blocking locks.
- **Standard library only.** No runtime dependency.

Full contract in [docs/design.md](docs/design.md).

## Quick start

```bash
# Try a hook by hand: an agent call without justification is denied
echo '{"session_id":"demo","cwd":".","hook_event_name":"PreToolUse",
       "tool_name":"Agent","tool_input":{"subagent_type":"builder","model":"strong-model","prompt":"Refactor the parser"}}' \
  | python3 -B hook.py agent_guard --config examples/agent_guard.json
```

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
  "permissionDecisionReason": "Add a separate line: Why not the cheaper executor: <reason of at least 12 characters>."}}
```

Add the line `Why not the cheaper executor: needs cross-file reasoning` to the prompt and the same call passes silently.

To use it in a project, copy `hook.py` and `guardrails/` to the project root, adapt the JSON policies in `examples/`, and merge the `hooks` block of [`examples/settings.json`](examples/settings.json) into `.claude/settings.json`.

## Tests

```bash
python -m pytest -q     # 159 tests
```

The tests cover each policy decision, the fail-open paths (malformed JSON, oversized input, missing files, deadline exhaustion), output size limits, audit rotation, lock contention and process-group cleanup.

Requires Python 3.11+ on a POSIX system (SIGALRM, flock).

## About

Generic rewrite of the guardrail layer I run around a multi-agent development pipeline (23 agent roles, cloud and local models). Designed and reviewed by me; implementation produced through that same supervised pipeline (specification → generation → independent review → tests rerun before acceptance).

MIT licence.
