# btq-rcose: bound the shim's hook input in size and time

Bead: btq-rcose, a follow-up to PR #72. Base: main 093ede5. ADR 0001 r15 is unchanged. This is a hardening of the shim (§3.1, plan 4 D14) inside rules the ADR already sets: anything the agent can shape is untrusted, and a tool call fails closed. It needs no amendment.

## Problem

`hook_main` (`src/heterodyne/session/shim.py`) runs `run_hook(sys.stdin.buffer.read(), os.environ)`. That read has no size limit and no deadline. `status_main` had the same problem, which PR #72 r1 fixed with `read_bounded(fd, MAX_LINE, STATUS_WAIT)`.

`shim.py hook` is registered for SessionStart, UserPromptSubmit, Stop and PreToolUse, plus StopFailure for Claude (`agents/base.py`, `HOOK_EVENTS`). It runs as the agent inside the sandbox. Its stdin is the payload the CLI writes, and the agent shapes most of it: the tool input, the prompt and the last message. That gives two failure modes:

- **Memory.** An unbounded payload is read whole. It is then held two or three more times, as the parsed object, the projection and the encoded line. A runaway tool input, or a CLI bug, can push the shim's memory to whatever the sandbox allows.
- **Time, and this one fails open.** If the CLI never closes the hook's stdin, the read blocks. For PreToolUse, `PRE_TOOL_COMMAND` turns a shim that *can't run* into exit 2, but a shim that hangs is a different case. The CLI cancels it at `PRE_TOOL_TIMEOUT` (120 s), and `base.py` notes that a cancelled hook lets its tool run. A hung read is therefore the one way a PreToolUse hook can end up allowing a tool that wsd never saw.

## How big hook payloads get

These are measured locally, from this host's own transcripts (the sizes of the JSON-encoded fields only, no contents read out):

| Source | Count | Largest | p99 |
|---|---|---|---|
| Claude Code `tool_use` inputs (`~/.claude/projects/*/*.jsonl`) | 5955 | 188 KB (a Write) | 6.3 KB |
| Claude Code user prompts (same) | 1941 | 356 KB | 19 KB |
| Codex tool-call arguments (`~/.codex/sessions/**/*.jsonl`) | 25030 | about 45 KB (exec and apply_patch) | 7.4 KB |
| Codex user messages (same) | — | 37 KB | — |

The hook payload is that field plus an envelope of under 1 KB (session ID, transcript path, cwd, event and tool names). No registered event carries a tool response: there is no PostToolUse, so tool outputs and base64 images never reach the shim.

Theoretical ceilings:
- A tool input can't exceed one model response. That is at most 128k output tokens, about 0.5 MB of text, or a few MB once JSON-escaped.
- A prompt or last message is bounded by the context window. At 1M tokens that is about 4 MB of text.

## Decision

### The bound: `MAX_HOOK_INPUT = 16 MiB`

- It is about 45 times the largest payload seen, and four times the 1M-token ceiling. So no real event is cut.
- It costs little. `json_depth` plus a full `run_hook` with no config, on a Write payload, takes 0.02 s at 1 MB, 0.07 s at 4 MB, 0.10 s at 8 MB and 0.20 s at 16 MB (measured on this host). Memory is a small multiple of the bound.
- It is separate from `MAX_LINE` (64 KiB), which bounds one request line on the socket. Events between the two bounds keep today's behaviour: they go to wsd as their projection, and the local `worktree_edit` check still reads the full event.

### The deadline: `HOOK_INPUT_WAIT = 5.0` s

- The CLI writes the payload when it spawns the hook and then closes stdin. Even 16 MiB goes through a pipe in milliseconds, so 5 s only matters for a writer that has stalled.
- It keeps the PreToolUse total well inside the CLI's cancel point. 5 s for the input plus `hook_wait_seconds` (at most 60, enforced in `sandbox/settings.py`, named `MAX_HOOK_WAIT` by this change) plus the reply is under 70 s, against `PRE_TOOL_TIMEOUT` = 120 s. A test pins `HOOK_INPUT_WAIT + 60 < PRE_TOOL_TIMEOUT`, so changing either constant can't silently reopen the cancel-and-run path.
- A payload that is late returns at the deadline, before wsd is contacted, so `hook_wait_seconds` is never added on top of a late payload. An on-time payload uses only milliseconds of the 5 s.

### Reading: `read_bounded` drains past the bound

`read_bounded` gains a keyword, `drain: bool = False`:

- **Unchanged without `drain`.** It reads to EOF, stops at `limit + 1` bytes, and returns b"" if EOF hasn't come by the deadline. `status_main` keeps exactly this behaviour.
- **With `drain=True`, once `limit + 1` bytes are held,** it keeps reading and discards everything else until EOF or the deadline, and then returns the `limit + 1` bytes. Memory stays bounded. Draining matters because otherwise the CLI's write to a closed pipe fails (EPIPE, BrokenPipe). How Claude (Node) and Codex (Rust) treat a hook whose stdin write failed isn't specified, and it shouldn't be what decides a tool call. With the drain, the CLI's write completes and the shim's decision on stdout is what counts. A writer still sending at the deadline is cut off then, the same as a stalled one.
- **No more quadratic copying.** Chunks go into a list and are joined once. With `data += chunk`, 16 MiB arriving in 64 KiB pipe reads would copy about 2 GB.

`hook_main` becomes:

```python
def hook_main() -> int:
    try:
        raw = read_bounded(sys.stdin.fileno(), MAX_HOOK_INPUT, HOOK_INPUT_WAIT, drain=True)
    except (OSError, ValueError):       # no stdin at all (closed, not a pipe the shim can poll)
        raw = b""
    rc, out = run_hook(raw, os.environ)
    ...
```

### What the shim answers

`run_hook` gains one check before it parses anything: input longer than `MAX_HOOK_INPUT` is answered with `_deny(TOO_LARGE_REASON)` and exit 0. Late input arrives as b"", which already fails to parse and is answered with `_deny(DENY_REASON)` and exit 0. That is today's path for any unreadable input (`test_unreadable_input_is_denied`).

- **Exit 0 with the deny decision on stdout is the answer both CLIs accept.** It is the shim's existing deny, which Claude Code 2.1.286 and codex-cli 0.160 honour today. Exit 2 is deliberately not used here:
  - for Stop, exit 2 means "don't stop", so the agent would keep going;
  - for UserPromptSubmit, exit 2 erases the prompt.
- **The reasons are the existing fixed strings.**
  - Oversize gives `this tool call is too large for the control plane to check`, which tells the model to split the call.
  - Late gives `control plane unavailable; retry shortly`.
  - Neither repeats any of the payload.
- **Nothing is sent to wsd and nothing is spooled** for input that is oversize or late. The shim never parsed it, so it has nothing trustworthy to send.
- **The event isn't known, so the answer is always the PreToolUse deny.** An oversize or late payload can't be parsed, so the shim can't tell a tool call from a Stop. Printing the deny for every event is the fail-closed choice: a tool call is denied, and for other events the CLI ignores a decision for a different event.

  The cost is that an oversize or late non-tool event (over 16 MiB, or stalled 5 s) is lost to wsd, much as a cancelled hook's event already is. wsd keeps its turn state from events it does receive.

  Rejected alternative: sniff `hook_event_name` from the unparsed prefix and stay silent for non-tool events. A scanner aware of string escapes can find the top-level key, but any bug in it would turn an oversize tool call into an allowed one, and all it buys is silence for an event that essentially never happens.

## Tests

In `tests/test_session_shim.py`, using the standalone entry (`python3 -I shim.py hook`) wherever the bead asks for it:

1. `test_an_oversized_hook_input_is_denied_and_drained` (standalone): a PreToolUse Write of `MAX_HOOK_INPUT + 1` bytes on stdin. The parent's write of the whole payload completes with no BrokenPipeError. The shim exits 0, and its stdout is the deny with `TOO_LARGE_REASON`. The live server served nothing (`live.served == 0`), and no spool file exists.
2. `test_hook_input_that_never_ends_never_holds_the_shim` (standalone, like `test_status_input_that_never_ends_never_holds_the_shim`): a well-formed PreToolUse is written, and stdin is left open. The shim exits 0 within `HOOK_INPUT_WAIT` plus a margin, with the deny and `DENY_REASON`. `live.served == 0`.
3. `test_a_large_hook_input_under_the_bound_is_still_read_whole` (standalone): a 1 MiB Write inside the worktree, with wsd down and `worktree_edit` auto-approved. It is allowed silently, so the full event still reaches the local check. With wsd up, a 1 MiB Stop is recorded as a stop (its projection). This keeps `test_an_oversized_stop_records_a_stop`'s promise at the new input size.
4. `test_a_hook_with_no_stdin_is_denied` (standalone): run through `sh -c 'exec 0<&-; exec python3 -I shim.py hook'`. Exit 0, with the deny.
5. `test_run_hook_denies_input_over_the_bound_unparsed`: `run_hook` given `MAX_HOOK_INPUT + 1` bytes of *valid* JSON (a Stop) answers with the `TOO_LARGE_REASON` deny, never contacts the socket and spools nothing. This pins that the size check comes before parsing and covers every event.
6. `test_read_bounded_drains_past_the_bound`:
   - a writer thread sends `limit` × 8 bytes and closes. `read_bounded(..., drain=True)` returns exactly `limit + 1` bytes, and the writer finishes without an error.
   - with a fake clock, a stream that never ends returns at the deadline: b"" while under the bound, and `limit + 1` bytes once past it.
   - the existing `test_read_bounded_cuts_a_flood_and_drops_a_stall` still passes unchanged, which pins that `status_main`'s behaviour doesn't change.
7. `test_the_hook_input_deadline_sits_inside_the_cli_timeout`: `shim.HOOK_INPUT_WAIT + 60 < base.PRE_TOOL_TIMEOUT`. The 60 is written inline in `sandbox/settings.py` today. The implementation names it `MAX_HOOK_WAIT = 60` there, uses it in the range check, and the test reads it, so the two can't drift.

Every existing shim test runs unchanged. They call `run_hook` directly or pipe small payloads through the standalone entry.

## Docs

- The `shim.py` module docstring gets one paragraph covering the input bound, the deadline, the drain and the deny.
- The `PRE_TOOL_TIMEOUT` comment in `agents/base.py` names `HOOK_INPUT_WAIT` beside `hook_wait_seconds`.
- `docs/wsd.md` doesn't describe the shim's limits, so it is unchanged.

## Out of scope

- The server side, `SessionServer`, already bounds its request line (`MAX_LINE + 1`) and its deadline.
- `request_main` reads argv, not stdin.
