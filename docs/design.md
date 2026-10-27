# Hook contract

Python 3.11 or later and a POSIX system are required: deadlines use SIGALRM,
locks use flock, and child cleanup uses process groups. Runtime code uses only
the standard library. Pytest is needed only for tests.

Run `python3 -B hook.py session_resume --config examples/session_resume.json`
with a Claude Code event object on stdin. The event must contain `session_id`,
`cwd` and the matching `hook_event_name`. `cwd` is the project root for local
data and state. Configuration paths are supplied by the caller; paths inside
configuration are relative to that project root. Absolute paths, traversal and
symlink escapes in project data paths are rejected. Configuration and local
data must be regular UTF-8 files of at most 65,536 bytes.

`examples/settings.json` assumes these files are at the project root. Merge its
`hooks` object into project settings manually. An Agent brief uses `tool_input`
with `subagent_type`, `model` and `prompt`. Successful permission checks emit
nothing. Denials use `hookSpecificOutput.permissionDecision = "deny"` and
`permissionDecisionReason`; Stop refusals use `decision = "block"` and `reason`.
Context hooks use `hookSpecificOutput.additionalContext` and the event name.

# Execution boundary

The default wall-clock budget is 1.2 seconds; set
`GUARDRAILS_TIMEOUT_SECONDS` to a finite value from 0.01 to 60. The timer starts
before importing the dispatcher and policies, and covers stdin, configuration,
locks, file access, serialization, audit and output. Interpreter startup precedes
this timer. Keep the Claude Code command timeout above the chosen budget.
Input is capped at 65,536 bytes. JSON output is capped at the smaller of 4,096
bytes and the platform's atomic pipe-write limit, including the newline;
oversized responses pass silently. Character limits are additional
bounds, so a large Unicode context may exceed the byte limit and pass silently.
Nonblocking pipe output is committed in one atomic write. Accidental stdout and
stderr from handlers are discarded. Errors, invalid input and deadlines exit
with status zero and no output. No partial response is printed.

`Runtime.run` accepts an argument list, never a shell string, and caps captured
stdout. It shares the remaining deadline and kills the child process group on
success, error or timeout, including descendants in that group. Extensions must
use this helper for subprocesses. The example hooks do not launch commands.
Deliberately detached descendants are outside the process-group contract.

Audit defaults to `.guardrails/audit.jsonl`, 65,536 bytes and one backup.
An optional `audit` object sets `path`, `max_bytes` (256 to 5,000,000) and
`backups` (1 to 5). Rotation retains only that many bounded files. Oversized
preexisting logs are discarded instead of retained. Records contain only a
timestamp, a fixed hook name, a fixed outcome and elapsed milliseconds; never
prompts, briefs, session identifiers, file contents or exception messages.
Lock contention or audit failure causes a silent pass. Error auditing is best
effort within the original budget; an exhausted deadline may leave no record.

# Policy data

`session_resume` reads `state_file`, strips surrounding whitespace and truncates
to `max_chars`. `prompt_context` reads optional `usage_file` and `tasks_file`,
then truncates to `max_chars`. Usage is `{"used_percent": 42}` with a finite
percentage from 0 to 100. Tasks use a `tasks` array with unique nonempty `id`,
`title` and `status` strings. Only `in_progress` tasks appear in prompt context.

`agent_guard` requires its configurable `justification_prefix` at the start of
a separate line, followed by `min_justification_chars` characters. `exceptions`
contains exact agent types exempt from justification only; explicit model,
forbidden model and usage rules still apply. `forbidden_models` maps exact
agent types (or `*`) to model names, compared without case. Usage at or above
`usage_threshold` is denied. Omitting `usage_file` disables the usage check;
a configured but missing or invalid file causes a silent pass.

`stop_guard` checks all tasks whose status is exactly `done`. Evidence is either
`{"file": "relative/proof.txt"}` naming a readable, nonempty regular text file,
or `{"command": ["python3", "-m", "unittest"], "exit_code": 0}` recording a
referenced test command and successful result. Commands are never executed and
recorded results are trusted; this is a workflow check, not proof verification.
The stop flag `stop_hook_active: true` always passes before touching task state.
Session counters allow at most two blocks, or a lower `max_blocks` value.
Each `UserPromptSubmit` resets the counter through `prompt_context`; both hooks
must use the same `state_dir`. Hashed session filenames and atomic writes under
nonblocking locks isolate concurrent sessions. No prompt is persisted. State
files remain until the project owner removes them.

`routing.select_executor` returns the first declared executor below its quota
threshold, or `None` if all are exhausted. Missing quotas mean zero use; unknown
task types use an optional `*` rule. Per-executor thresholds override the shared
threshold. Rules are data, inputs are not mutated and no executor is launched.

Run tests with `python -m pytest -q`. Temporary test files stay in `.test-tmp`.
