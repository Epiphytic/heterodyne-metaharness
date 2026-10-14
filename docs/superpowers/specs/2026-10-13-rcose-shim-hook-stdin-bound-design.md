# btq-rcose: bound the shim's hook input in size and time

Bead: btq-rcose (follow-up to PR #72). Base: main 093ede5. ADR 0001 r15 unchanged: this hardens the shim (§3.1, plan 4 D14) inside rules the ADR already sets (what the agent can shape is untrusted; a tool call fails closed). It needs no amendment. Revision r2 answers Codex review r1 (see "Changes in r2" at the end).

## Problem

`hook_main` (`src/heterodyne/session/shim.py`) runs `run_hook(sys.stdin.buffer.read(), os.environ)`: no size bound and no deadline. `status_main` had the same gap, which PR #72 r1 fixed with `read_bounded(fd, MAX_LINE, STATUS_WAIT)`.

`shim.py hook` is registered for SessionStart, UserPromptSubmit, Stop and PreToolUse, plus StopFailure on Claude (`agents/base.py` `HOOK_EVENTS`, `claude_code.py` `CLAUDE_HOOK_EVENTS`). It runs as the agent inside the sandbox, and its stdin is the payload the CLI writes. The agent shapes most of that payload (tool input, prompt, last message). Two failures follow:

- **Memory.** An unbounded payload is read whole and then held two or three more times (parsed object, projection, encoded line). A runaway tool input or a CLI bug can grow the shim to whatever the sandbox allows.
- **Time, and this one fails open.** If the CLI never closes the hook's stdin, the read blocks. `PRE_TOOL_COMMAND` turns a shim that *can't run* into exit 2, but a shim that hangs is cancelled at `PRE_TOOL_TIMEOUT` (120 s), and the CLI then runs the tool. This was confirmed against Claude Code 2.1.286 (experiment E7 below): a hook that outlives its timeout leaves the tool to run. A hung read is the one way a PreToolUse hook ends in an allowed tool that wsd never saw.

## How big hook payloads get

These sizes were measured locally from this host's own transcripts. Only the size of each JSON-encoded field was read, never its content.

| Source | Count | Largest | p99 |
|---|---|---|---|
| Claude Code `tool_use` inputs (`~/.claude/projects/*/*.jsonl`) | 5955 | 188 KB (a Write) | 6.3 KB |
| Claude Code user prompts (same) | 1941 | 356 KB | 19 KB |
| Codex tool-call arguments (`~/.codex/sessions/**/*.jsonl`) | 25030 | about 45 KB (exec and apply_patch) | 7.4 KB |
| Codex user messages (same) | — | 37 KB | — |

The hook payload is that field plus an envelope under 1 KB: session ID, transcript path, cwd, event and tool names. No registered event carries a tool response. There is no PostToolUse, so tool outputs and images never reach the shim.

Ceilings:
- **Tool input:** a tool input can't exceed one model response. At most 128k output tokens is about 0.5 MB of text, or a few MB once escaped.
- **Prompt or last message:** these are bounded by the context window. 1M tokens is about 4 MB.

## Design

### 1. The event is passed as an argument

Each installed hook command names its event: `python3 -I /run/hz/shim.py hook <Event>`. For PreToolUse that sits inside the existing `sh -c '… || { …; exit 2; }'` wrapper.
- **base.py:** `hook_group(event)` builds the command from `event`. `HOOK_COMMAND` and `PRE_TOOL_COMMAND` become `hook_command(event)` and `PRE_TOOL_COMMAND = pre_tool_command()`.
- **Fixed per event:** each command is still the same on every launch, so plan 4 D5 holds: Codex's trusted hash stays valid from launch to launch, and the token is never in argv. Event names are fixed identifiers (`[A-Za-z]+`), so they need no quoting.
- **Codex trust:** `codex_trust.py` already lists every hook in `hooks.json` with its own command and hash, so per-event commands need no change there.

**Why the argument can be trusted:** it is exactly as trusted as the hook's existence. On Claude the settings file is in the run directory, which is read-only inside the sandbox. On Codex, `hooks.json` is in the agent's home, but a hook whose command changes no longer matches its `trusted_hash` and doesn't run. Anyone able to rewrite the argument could already delete the hook. The argument is never taken from the payload.

The shim reads the event like this:
- `hook <Event>`, where `<Event>` is one of the registered events, gives that event.
- `hook` with no argument, an unknown argument or extra words is treated as **PreToolUse**, so it fails closed. That way a settings file written by an older harness, or a typo, still denies tool calls on bad input.

A parsed payload is a tool call if the argument says PreToolUse **or** its `hook_event_name` does. If the two disagree (argument `Stop`, payload `PreToolUse`, or the reverse), the payload is treated as unreadable for whichever event is a tool call: denied, nothing sent. This needs no new code path: `tool_call` is the OR of the two, and a mismatch takes the existing unreadable-input branch.

This drops r1's trade-off. Because the shim knows the event without parsing untrusted input, it can fail closed for tools and stay silent for lifecycle events. It doesn't have to print a PreToolUse decision on every event. That printing would have been invalid output on Codex 0.160: Stop rejects `hookSpecificOutput`, and SessionStart and UserPromptSubmit reject the permission fields ([schema.rs](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/schema.rs#L395)). It also drops r1's rejected alternative, sniffing the event from a truncated prefix.

### 2. The bound: `MAX_HOOK_INPUT = 16 MiB`

- **Headroom:** it is about 45 times the largest payload observed and four times the 1M-token ceiling, so no real event is cut.
- **Cost:** `json_depth` plus a full `run_hook` on a Write payload takes 0.02 s at 1 MB, 0.07 s at 4 MB, 0.10 s at 8 MB and 0.20 s at 16 MB (measured on this host). Memory is a small multiple of the bound.
- **Relation to `MAX_LINE`:** it is separate from `MAX_LINE` (64 KiB), which bounds one request line on the socket. Events between the two bounds keep today's behaviour: wsd gets their projection, and the local `worktree_edit` check still reads the full event.

### 3. The deadline: `HOOK_INPUT_WAIT = 5.0` s

- **Who it can affect:** the CLI writes the payload from memory right after spawning the hook. Draining 16 MiB through a pipe takes milliseconds, so 5 s can only cut a writer that has stalled. The agent decides what the payload contains, not how fast the CLI writes it.
- **The budget:** 5 s of input, plus `hook_wait_seconds` (at most 60, enforced in `sandbox/settings.py`), plus the reply comes to under 70 s. That is well inside `PRE_TOOL_TIMEOUT` = 120 s, the point at which the CLI cancels the hook and runs the tool. A test pins `HOOK_INPUT_WAIT + MAX_HOOK_WAIT < PRE_TOOL_TIMEOUT`. `MAX_HOOK_WAIT = 60` names the literal that `settings.py:81` checks today, and that check then uses it.
- **Late input never waits for wsd:** a late payload returns at the deadline, before wsd is contacted, so `hook_wait_seconds` is never added on top of it.

### 4. Reading: `read_bounded(..., drain=True)`

`read_bounded` gains a keyword, `drain: bool = False`:

- **Without `drain`** it behaves as today. It reads up to `limit + 1` bytes until EOF and returns b"" if EOF hasn't come by the deadline. `status_main` keeps exactly this behaviour.
- **Accumulation:** reads go into a `bytearray` with `extend`, and each read asks for at most `limit + 1 - len(buf)` bytes. Nothing is ever copied again, and the memory held is the bytes kept, never more. That holds even for a writer that sends one byte at a time.
- **With `drain=True`, once `limit + 1` bytes are held,** each further read goes into one fixed 64 KiB scratch read that is thrown away, until EOF or the deadline. Then it returns the `limit + 1` bytes.
- **The deadline is checked before every poll and every read,** in both phases, including input that is always readable. So a writer flooding at full speed still stops at the deadline.
- **Results:**

  | Input | Returns |
  |---|---|
  | EOF within the bound | the data |
  | Over the bound (EOF or deadline) | `limit + 1` bytes |
  | Deadline reached while still under the bound | b"" |
  | Closed or invalid fd | raises `OSError`, unchanged |

Why drain, and why stopping at the deadline is still safe:
- **Draining means the CLI's write completes normally** for any payload the CLI does finish writing. That is the normal case, and nothing then depends on how the CLI handles a broken pipe.
- **When the shim stops before the writer has finished** (the deadline, or a crash), the CLI's remaining write hits a closed pipe.
  - **Codex 0.160** tolerates BrokenPipe on hook stdin and still collects stdout ([command_runner.rs](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/engine/command_runner.rs#L259)).
  - **Claude Code 2.1.286**, from its bundle and the offline experiment below:
    - **The write never waits.** The hook runner's stdin-write promise calls `stdin.write(input + "\n")` and `stdin.end()`, then resolves straight away. A stdin `error` arriving later finds the promise already settled and is ignored, and the result is built from the exit status and stdout.
    - **The EPIPE branch is out of reach.** The runner's own EPIPE branch ("Hook command closed stdin before hook input was fully written") returns empty stdout with status 1, which would fail open. It runs only if the write fails *synchronously*. The write call comes right after spawn, while the hook process holds the read end, so a shim that closes its stdin later can't trigger it.
- **Confirmed** by experiments E2–E5 below: with 4 MB of input and the hook closing stdin after 0 or 64 KiB, Claude still honours the deny on stdout, and the tool doesn't run.

### 5. `hook_main` and the answers

```python
def hook_main(args: Sequence[str]) -> int:
    event = args[0] if len(args) == 1 and args[0] in KNOWN_EVENTS else "PreToolUse"
    try:
        raw = read_bounded(0, MAX_HOOK_INPUT, HOOK_INPUT_WAIT, drain=True)
    except OSError:                     # no stdin: closed (sys.stdin is None under `exec 0<&-`) or invalid
        raw = b""
    rc, out = run_hook(raw, os.environ, event=event)
    ...
```

The shim reads fd 0 directly, not `sys.stdin.fileno()`, because `sys.stdin` is None when the fd is closed. `main` passes `argv[1:]` to `hook_main`.

`run_hook(raw, env, *, event="PreToolUse", ...)` gains one check before parsing anything. Input longer than `MAX_HOOK_INPUT` is answered without parsing, and its answer depends only on `event`. Input that is late, empty, unreadable, not JSON, too deeply nested, or for the wrong event takes the existing unreadable-input path, which now also depends on `event`:

| Input | `event` is PreToolUse | `event` is a lifecycle event |
|---|---|---|
| Over `MAX_HOOK_INPUT` | `_deny(TOO_LARGE_REASON)`, exit 0 | empty stdout, exit 0 |
| Late, empty, closed stdin, unreadable, or mismatched | `_deny(DENY_REASON)`, exit 0 | empty stdout, exit 0 |

In every row nothing is sent to wsd and nothing is spooled. The shim never parsed the input into anything it could trust.

- **Why exit 0 with the deny JSON** rather than exit 2 for tools: it is the shim's existing deny, and both pinned CLIs honour it. It carries the fixed reason as the decision reason. Claude also honours it when stdin was closed early (E3, E4). Exit 2 would block too (E6). The `sh` wrapper keeps exit 2 for a shim that can't run.
- **Lifecycle events stay silent.** Empty stdout with exit 0 is valid output for every event on both CLIs. Exit 2 is out for these: on Stop it means "don't stop", and on UserPromptSubmit it erases the prompt.
- **The reasons are the existing fixed strings,** and neither repeats anything from the payload:
  - `this tool call is too large for the control plane to check`, which tells the model to split the call;
  - `control plane unavailable; retry shortly`.
- **Lost events:** a lifecycle event that is over 16 MiB or 5 s late is lost to wsd, just as an event a cancelled hook carried is lost today. wsd keeps its turn state from the events it does receive. Recording a bare event named only by the argument is left out of this change, because wsd's handling of an event with no payload hasn't been designed.

## Evidence: an offline protocol experiment against the pinned Claude CLI

The test ran the real Claude Code 2.1.286 binary (`@anthropic-ai/claude-code/bin/claude.exe`, the pinned version) with `-p`. It used a throwaway `HOME` and `CLAUDE_CONFIG_DIR`, a dummy API key, and `ANTHROPIC_BASE_URL` pointing at a local mock of the streaming Messages API on 127.0.0.1. There was no login and no network.
- **The mock:** its first answer is one Write tool call whose `file_path` is a marker file and whose `content` is N bytes of padding. Once it sees a tool_result, it answers "done".
- **The hook:** a fake PreToolUse hook (matcher `Write`) reads stdin in one of three ways, closes fd 0, waits 0.5 s so the CLI's queued write meets the closed pipe, then answers.
- **The check:** "tool ran" means the marker file exists. The mock logs the tool_result.

| # | Input | Hook reads | Hook answers | Tool ran | tool_result |
|---|---|---|---|---|---|
| E1 | 1 KB, 200 KB, 4 MB | all | nothing, exit 0 | yes (control) | File created |
| E2 | 200 KB, 4 MB | nothing | nothing, exit 0 | yes (closing stdin early isn't itself an error) | File created |
| E3 | 200 KB, 4 MB | nothing | deny JSON, exit 0 | **no** | `PreToolUse:Write hook error: test deny` |
| E4 | 200 KB, 4 MB | first 64 KiB | deny JSON, exit 0 | **no** | same |
| E5 | 200 KB, 4 MB | all | deny JSON, exit 0 | **no** | same |
| E6 | 4 MB | nothing | stderr, exit 2 | **no** | hook error with the stderr |
| E7 | 1 KB | nothing; sleeps past a 3 s timeout | — | **yes** (a cancelled hook fails open) | File created |

E3 and E4 are the deadline case: the shim answers and exits while the CLI still has megabytes queued, and the decision holds. E7 is why the shim has a deadline at all, and why it has to sit well under the CLI's timeout.

Claude's Bash tool refuses commands this large on its own (4 MB and 200 KB Bash calls never ran even when the hook allowed them), so the experiment uses Write.

The harness isn't part of the test suite: it needs the CLI binary, and suites stay offline and binary-free. It should be rerun whenever `version_pin` moves. The plan for the next Claude pin bump gets a line saying so.

## Tests

Offline. Where the bead asks for it, the tests use the standalone entry `python3 -I shim.py hook <Event>` in `tests/test_session_shim.py`, and `tests/test_agents_adapters.py` for the commands.

**Reader** (`read_bounded`):
1. `test_read_bounded_drains_past_the_bound`: a writer thread sends `limit * 8` bytes in 1-byte and 64 KiB writes and closes. With `drain=True` the call returns exactly `limit + 1` bytes, and the writer finishes with no BrokenPipeError.
2. `test_read_bounded_at_exactly_the_limit`: `limit` bytes then EOF returns all `limit` bytes. `limit + 1` bytes then EOF returns `limit + 1`. Both with and without `drain`.
3. `test_read_bounded_drain_stops_at_the_deadline`, with a fake clock:
   - an always-readable flood with no EOF returns `limit + 1` at the deadline;
   - a stream under the bound with no EOF returns b"";
   - the clock is read before every read, which the fake clock's call count shows.
4. The existing `test_read_bounded_cuts_a_flood_and_drops_a_stall` passes unchanged, which pins `status_main`'s behaviour.

**Hook, standalone:**

5. `test_an_oversized_tool_call_is_denied_and_drained`: a PreToolUse Write of `MAX_HOOK_INPUT + 1` bytes:
   - the parent writes the whole payload with no BrokenPipeError;
   - the shim exits 0, and its stdout is the deny with `TOO_LARGE_REASON`;
   - `live.served == 0`, and no spool file exists.
6. `test_an_oversized_stream_left_open_is_denied_at_the_deadline`: more than `MAX_HOOK_INPUT` bytes are written and stdin is left open. The deny with `TOO_LARGE_REASON` comes within `HOOK_INPUT_WAIT` plus a margin, exit 0, and `live.served == 0`.
7. `test_hook_input_that_never_ends_never_holds_the_shim` (like the status test of that name): a well-formed PreToolUse is written and stdin is left open. The deny with `DENY_REASON` comes within `HOOK_INPUT_WAIT` plus a margin, exit 0, and `live.served == 0`.
8. `test_a_hook_with_no_stdin`: run through `sh -c 'exec 0<&-; exec python3 -I shim.py hook <Event>'`. PreToolUse gives the deny with exit 0. Stop gives empty stdout with exit 0.
9. `test_a_large_hook_input_under_the_bound_is_still_read_whole`:
   - a 1 MiB Write inside the worktree, with wsd down and `worktree_edit` auto-approved, is allowed silently, so the local check sees the full event;
   - with wsd up, a 1 MiB Stop is recorded as a stop, from its projection.
10. `test_a_lifecycle_hook_with_bad_input_is_silent`: for each lifecycle event, oversize input, input that is late (stdin left open) and non-JSON input each give empty stdout, exit 0, `live.served == 0`, and no spool.

**Event argument:**

11. `test_the_event_argument_fails_closed`, through `run_hook` and `main`:
    - `hook` alone, `hook Bogus` and `hook Stop extra` all deny bad input as PreToolUse;
    - a payload saying `PreToolUse` under `hook Stop` is denied, and nothing is sent;
    - a payload saying `Stop` under `hook PreToolUse` is denied;
    - matching payloads behave exactly as today.
12. `test_run_hook_denies_input_over_the_bound_unparsed`: `MAX_HOOK_INPUT + 1` bytes of *valid* JSON with `event="PreToolUse"` give the `TOO_LARGE_REASON` deny. With `event="Stop"` they give empty output. In both cases the socket is never contacted and nothing is spooled.

**Commands and budget:**

13. `tests/test_agents_adapters.py`: the expected commands become `hook_command(e)` and `pre_tool_command()`, one per event, each naming its event. The existing wrapper test (exit 2 when the interpreter can't run) runs the new `PRE_TOOL_COMMAND`. Codex's `hooks.json` keys are unchanged.
14. `test_the_hook_input_deadline_sits_inside_the_cli_timeout`: `shim.HOOK_INPUT_WAIT + settings.MAX_HOOK_WAIT < base.PRE_TOOL_TIMEOUT`. Also, `settings` rejects `hook_wait_seconds = MAX_HOOK_WAIT + 1`.

Every other existing shim test runs unchanged. They call `run_hook` with its default `event="PreToolUse"`, which is today's behaviour for unreadable input, or pipe small payloads through the standalone entry.

## Docs

- The `shim.py` module docstring gets one paragraph on the event argument, the bound, the deadline, the drain and the answers table.
- The comments on `HOOK_COMMAND` and `PRE_TOOL_TIMEOUT` in `agents/base.py` say the commands are fixed *per event*, name `HOOK_INPUT_WAIT` beside `hook_wait_seconds`, and cite E7.
- `docs/wsd.md` doesn't describe the shim's limits, so it is unchanged.

## Out of scope

- **`SessionServer`:** the server side already bounds its request line (`MAX_LINE + 1`) and its deadline.
- **`request_main`:** it reads argv, not stdin.
- **Recording a bare lifecycle event** for input that is lost (§5).

## Changes in r2

- **BLOCKING (Claude's EPIPE behaviour):** answered from the 2.1.286 bundle and the offline experiment, E1–E7. Closing stdin early never discards a decision. §4 states every case, including the deadline.
- **Wrong claim about other-event output:** replaced by the event argument (§1). Lifecycle events now get empty stdout, so no CLI receives a decision for another event, and both r1's trade-off and its rejected sniffing alternative are gone. Codex's rejection of such output is cited as the reason.
- **Accumulator:** a capped `bytearray`, a fixed scratch read while draining, and the deadline checked before every read (§4, test 3).
- **Added tests:** exactly at the limit (2), and an oversized stream left open past the deadline (6).
