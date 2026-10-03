# Spike S4b: membership operations and the summarizer launch shape

Status: complete. Plan 2b Task 1 (bead btq-qvnbx). Throwaway identities only (three `wn-agent` 0.10.3
homes under `/tmp`, removed afterwards). No product code written. No tokens, npubs, relay URLs or
bootstrap output are recorded here.

## Verdict on the ADR assumptions

All hold. After a successful `group_member_add` the acting admin's `member_count` is `from + 1`
immediately, before and after the invitee's side settles; `group_member_remove` gives `from - 1`.
Nothing here contradicts the ADR.

## Method deviation: invite policy

The plan bootstraps every home with `--invite-policy deny`. With `deny` the invitee never joins: for the
first group, `opA` saw the group in `group_info` with `member_count` 1, `group_welcome_status` returned an
empty `pending` list, and the count never reached 2. The throwaway `opA` and `opB` were therefore
re-bootstrapped with `--invite-policy allowlist --allow-welcomer <admin account hex>`, and a second group
was created (the first was abandoned; it was left with admin and `opA` only and removed with the homes).
Under `allowlist` the welcome is accepted automatically, with no separate accept step. Items below were
run on the second group. Real operator accounts are the invitee side in production; this only affects the
test setup. `group_welcome_status` takes `{account_id_hex, group_id_hex}` and returns
`{type: group_welcome_status, group_id_hex, pending: [...]}`.

## Step 3 results

| # | Check | Result | Observed |
|---|-------|--------|----------|
| 1 | `group_create` admin with members=[opA]; `group_info.member_count` | PASS | `group_created`, `pending_welcome_count` 0; admin count 2, and opA's own view 2 (auto-accepted under allowlist) |
| 2 | `group_member_add` opB | PASS | `group_membership_updated`, `pending_welcome_count` 0; admin `member_count` 3 immediately after the response (from+1); still 3 at +4, +8 and +12 s; opA and opB also report 3 |
| 3 | `group_member_remove` opB | PASS | `group_membership_updated`; admin count 2 immediately and 5 s later |
| 4 | `group_member_add` opA, already a member | PASS | `error`, code `backend`, `retryable` false, message `connector request failed`; count unchanged at 2 |
| 5 | `group_member_remove` opB, not a member | PASS | `error`, code `unknown_member`, `retryable` false, message `connector request failed`; count unchanged at 2 |
| 6 | `group_state_changed` fan-out | PASS | admin's own subscription (live through items 2 and 3) received no event at all; opA's subscription saw `member_added` then `member_removed`, in that order, with no `detail` |
| 7 | admin killed between request and reply | PASS | `ControlClient.call` raised `ControlError`, code `socket_closed`, `retryable` True, at kill delays 0.02, 0.3 and 0.8 s. In all three runs the add had not committed: after restarting admin on the same home, `member_count` stayed 2. A committed-but-reply-lost case was not observed, so recovery must re-read `group_info` rather than assume either outcome |
| 8 | 60,000-character message admin to opA | PASS | received whole: length 60000 and SHA-256 equal on both sides. Also tried 120000 and 250000: both whole, equal SHA-256. Largest verified whole: 250000 characters |

Notes for Task 5 (error codes):

- `backend` and `unknown_member` are not in `KNOWN_ERROR_CODES`, so `decode_head` reports them as
  `unrecognised`. The peer free text for both was `connector request failed`, which does not tell the two
  apart. Task 5 must add `unknown_member` (remove of a non-member) and decide how to treat `backend`
  (add of an existing member), or read `group_info` first.
- `not_group_admin` is the code for a non-admin caller (from S4).
- A lost reply surfaces as `socket_closed` / `retryable` True, the same as any connection drop.

Note for Task 7 (`BATCH_MAX_CHARS`): the 60,000 default works whole, with margin (250,000 also worked),
so keep `backstop.BATCH_MAX_CHARS = 60000`.

## Step 4 results

CLI: Claude Code 2.1.286. Working directory: an empty `mktemp -d` directory. The config has no
`profiles.claude-opus.model` key, so the plan's fallback `sonnet` was used.

Working argv (stdin is the prompt):

```text
claude -p --model sonnet --tools "" --setting-sources project --settings '{"disableAllHooks": true}' \
  --strict-mcp-config --no-session-persistence --output-format text
```

| Check | Result | Observed |
|-------|--------|----------|
| `--tools ""` accepted | PASS | no error; the working alternative is not needed |
| Exit status and one line back | PASS | exit 0; one line: the build passed, with 3 tests skipped |
| Directory still empty | PASS | `ls -A` empty after each of the three runs |
| No new queue or brain state | PASS | `brain_offers` count 25 before and 25 after (read with Python `sqlite3`, read-only; the `sqlite3` CLI is not installed) |
| Keeps errors and questions | PASS | with Task 7's `PROMPT` pasted around the sample reply: 4 lines out; the error line `Error: wsd.service failed to start: exit-code 1` and the question `Should I roll back to yesterday's build, or keep debugging?` were each quoted verbatim; the 30 log lines became one line |
| No tools (stream-json) | PASS | `--output-format stream-json --verbose`, asked to list the files: exit 0; the `init` event lists 0 tools and 0 MCP servers; the one assistant message holds a single `text` block and there is no `tool_use` event; the directory stayed empty |

Note for Task 7: when asked to use a tool the model wrote the intended call as plain text (a
`<invoke name="Bash">` block) in its reply. Nothing ran, but a summary can contain such text, so it is
untrusted output and goes through `redact` and the length caps like any other.

## Cleanup

`admin` removed `opA` from the group (count 1 after). Each `wn-agent` child, and the two subscriber
helpers, were stopped by the PID recorded in its own `pid` / `subpid` file; a process listing afterwards
showed none left. The scratch homes and the summarizer directory were deleted. No live home, unit or
global config was touched.
