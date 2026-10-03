# Security model

This is a summary of the security design in [ADR 0001](adr/0001-workstreams-v2.md). The ADR is authoritative; read the cited sections for detail.

**Implementation status.** Two parts exist today. The configuration side: the action-class tiers, host-only policy, `[restrict]` tightening, and the rejection of inline secrets (see [configuration.md](configuration.md)). And `admind` (plan 2): the admin channel described in its section below, with its runbook in [admind.md](admind.md). The policy engine, approvals, sandbox and self-test described below are the target design for later plans.

## The sandbox is the boundary, and tiers decide what escalates (§5.3)

- **Agents run fully permissive inside an external sandbox:** Codex with `--dangerously-bypass-approvals-and-sandbox`, Claude with `--permission-mode bypassPermissions`. Neither raises permission prompts.
- **The sandbox is the security boundary.** It holds no git push credentials, deploy tokens, messaging tokens or other secrets; the only credential inside is model auth (§7). Agents commit to a local `btq/<id>` branch in their worktree, and nothing is pushed automatically.
- **Only approved, typed actions leave the host.** They are executed outside the sandbox by `wsd-act`, a separate OS user that holds the privileged credentials. The action registry is closed: `push_branch`, `open_pr`, `merge_pr`, `deploy` and `notify`, each with fixed fields plus `idempotency_key`, `approver` and `revision`. The agent requests, `wsd` records, the operator approves the exact payload, and `wsd-act` revalidates and executes it. Anything outside the registry (sending email, spending money, touching secrets) is an ask that authorises someone to proceed inside the sandbox; `wsd` never executes it.
- **The `PreToolUse` hook is the UX layer, not a control.** In Claude Code it fires before every tool call and turns operator-only intents into clear, parked asks. It never fires under `codex exec`, and until it is verified for interactive Codex sessions, every Codex session is treated as headless, with the outer sandbox as its only enforcement. A headless Codex run is allowed only with `-s read-only`, or inside the outer sandbox.
- **Tiers.** The policy engine checks the versioned tier file, `policy.toml`, which changes only through an approval bead:
  - **auto-approve:** reversible actions confined to the bead's worktree and sandbox (dependency installs, tests, local commits);
  - **escalate (operator-only):** pushes to shared branches, opening or merging PRs, deploys, sending messages or email, spending money, anything touching secrets, deletion outside the worktree;
  - **hard deny, never escalated:** an agent changing harness, policy or sandbox config, which would let it widen its own permissions;
  - **grey zone:** judged by the gatekeeper within 60 seconds; a timeout or an unavailable gatekeeper escalates.
- **Escalation parks and continues.** An approval bead records the summary, the reason and the typed payload verbatim, and blocks the task. The agent commits its work in progress, and pickup moves on.

What exists today: the default tiers and locked classes, host re-tiering that can never lower a locked class, and workstream `[restrict]` tightening that can never relax anything.

## Approval asks: required content, digest and grooming (§5.9)

An approval is valid evidence only for content that was pinned and shown.

- **Every ask has five parts:** what (one sentence), the exact effect (the typed payload, or what approving does and does not authorise), why, enough context to decide without opening anything else, and pinned references. A reference is an immutable identifier (a commit and path, a PR or patch head SHA, a `BASE..HEAD` range, or a bead ID with its `context_digest`). Branch names, "latest" and unpinned URLs are rejected.
- **The ask is one immutable object** (`metadata.ask`), written once. Enrichment or grooming replaces it whole, which means a new digest and a new card.
- **`context_digest`** is SHA-256 over canonical JSON of the title, description, ask, and each reference resolved to its content ID. Workflow state (decisions, labels, status, timestamps, comments) is excluded, so state changes don't invalidate it. The card shows the first 12 characters; the decision records `decided_digest`; the decision queue, `wsd-act` and btq's design gate each re-verify it. Any change after the ask was shown invalidates the approval.
- **The context sufficiency gate** runs before any card reaches a human: a deterministic lint, then the gatekeeper's judgement ("could the operator decide from this card alone?"). If context is missing, the gatekeeper enriches the ask (marked as gatekeeper-added) or sends it back to its originator for **grooming**, labelled `needs-grooming` with the gaps listed. After two failed groomings the operator gets a "cannot approve as written" notice with deny as the default. The gatekeeper never auto-approves an ask that failed the lint.
- **Design approvals bind to a revision.** A design approval pins the ADR commit, and every implementation bead carries `design_approval` and `adr_revision`. Any change to the ADR needs a new approval before work may cite it.

## Sandbox contents and the launch self-test (§7)

- **Profile.** A platform-neutral, per-workstream sandbox profile (writable paths, read-only binds, network allowlist) is compiled at launch to bubblewrap arguments on Linux, or a Seatbelt profile on macOS. Coder and reviewer share it; the reviewer's worktree bind is read-only.
- **Environment.** The launcher clears the environment and sets only an allowlist: locale, terminal, user, `HOME`, `PATH`, the session socket and the proxy settings.
- **Home.** A synthetic `$HOME` holds only the agent's config and auth files. The auth files are bound read-only; the rest is a writable per-session copy. The real home (SSH keys, forge tokens, queue credentials), other workstreams' worktrees and the `wsd` journal are not mounted.
- **Token freshness.** The auth files hold the operator's own login, whose refresh token the host shares. Before every launch the launcher refreshes on the host, or refuses, if the access token would expire within the session's maximum lifetime plus a stop margin. A session is stopped at its maximum lifetime and relaunched through the same gate.
- **Connectors.** The model token probably also reaches the account's MCP connectors. The proxy denies the Claude MCP proxy host, and adapter config disables claude.ai MCP servers and Codex connectors.
- **Network.** A per-sandbox egress proxy allows exact hosts per adapter: model endpoints, package registries, and read-only fetch from the git remote. It checks only the CONNECT host, not TLS SNI or paths.
- **Session socket.** Each session gets its own socket, authenticated by a per-launch token, that accepts only that session's hook events and requests and exposes no control operations.
- **Accepted residual risks:** a compromised agent could exfiltrate its model credentials or repository content through allowlisted endpoints; a client can present a different SNI behind an allowed host; proxy logs show hosts, not content; and Codex connector traffic shares a host with model traffic, so only adapter config keeps it off.
- **The launch self-test** runs before every launch, resume and relaunch, and fails closed. Each probe must prove the specific enforcement, because a generic failure looks like enforcement:
  - a real-home canary, readable outside and absent inside;
  - a non-allowlisted host refused *by the proxy*, with a paired allowlisted control that succeeds;
  - no route except loopback, and a raw connect to a literal address fails;
  - a control operation on the session socket gets an explicit `forbidden`;
  - no environment variable outside the allowlist.

The sandbox spike's findings are in [spikes/S3-sandbox.md](spikes/S3-sandbox.md).

## The admin override channel, `admind` (§8)

- **Independent.** It has its own service unit and its own Marmot identity, and shares no dependency with `wsd`, the Hermes gateway, beads or the gatekeeper. It works in a group of the admin bot and its operators (every `policy.toml` operator with a `marmot_npub`; revision 13 allows several), refuses to operate if the member count is not the one it trusts, and accepts messages only from an operator's exact npub that is also confirmed in the group.
- **Passthrough.** The operator's text goes byte for byte to a persistent session of the `admin` profile, and replies come back verbatim when short, summarized or batched when long (redacted either way, see "Output policy"). There is no gatekeeper LLM in between.
- **Deliberately privileged.** The agent runs as the harness's service user, with permission prompts bypassed, no sandbox and no root. Its purpose is to repair anything the harness can break, so a least-privilege identity would defeat it. This is an operator decision (2026-09-29); the design review's objection and the rebuttal are recorded in the r1 response.
- **Mitigations:** the sender must be MLS-authenticated as the operator's npub (§3.4); the member-count check against the trusted count; admind's own Marmot keys are readable only by its unit; every message and action goes to an append-only log.
- **Built-in commands** need no LLM: `!new`, `!interrupt`, `!tail [n]`, `!ps`, `!details [full]` and `!restart <unit>`, which accepts only units from a fixed allowlist in admind's config.
- **Audit** is a local append-only JSONL log (0600) plus the agent transcript, kept separate from beads on purpose. It records whole operator messages and outcomes, all redacted: see "Output policy" below. admind also relays `wsd`'s local alerts to the operator.

What the implementation adds to the ADR's mitigations (details in [admind.md](admind.md)):

- **Latch (D4).** Any membership or admin event in the group, or a member count other than the trusted one, latches admind: every inbound message is dropped and nothing is posted until the operator checks the group in their client and runs `admind rearm` on the host. Posting and dispatch also need a live membership subscription and a verified group (resubscribe, then verify the count, then observe), and authorisation is rechecked before each command and each dispatch. A latch that lands while a command is already running does not cancel it: the command finishes, and its reply is held by the outbound gate until the latch is cleared. Accepted residual risks (ADR §3.4): `group_info` gives a count, not members, so a one-for-one swap made on the control socket by a same-user process is invisible, and re-verification after an outage is by count only. A same-user process can also forge a reply event on the hook socket. Hook events are correlated with the agent launch that produced them by a per-launch nonce; this orders a crashed launch's late events away from the current turn (they are treated as stale) but is not authentication, since a same-user process can read the nonce.
- **The control socket (B6, B18).** `admind operators add|remove` and `admind rearm` reach the daemon over `ctl.sock`, a 0600 socket in the 0700 state directory. Startup refuses a path that exists and is not a socket (`lstat`, so a symlink is refused) and only replaces a stale socket; shutdown unlinks only the socket the instance bound (an identity check); a request is limited to 4096 bytes. The trust boundary is the service user: anything that can use the socket can already act as admind, so there is no further authentication. Failures fail closed: a malformed or invalid request, an unreadable `policy.toml`, a change that cannot be confirmed, or a handler error each changes nothing or latches, and says so with fixed wording. There is no daemon-level single-instance lock: two admind daemons on one state directory are unsupported and nothing prevents them (a known follow-up).
- **Operators need both policy and the group (B21).** An operator may command admind only if its key is listed in `policy.toml` and confirmed in the group (stored). A restart cannot authorise someone who was never added, and an upgrade with several policy operators latches until `admind rearm` reconciles them (B22).
- **The summarizer is confined (B8).** It runs as a headless subprocess with no tools, hooks, MCP servers or user settings, a 60-second limit and a bounded output, and the profile's `args` are ignored so they cannot switch tools back on. Its input is the redacted reply; its output is redacted again. Any failure sends the reply to the batched backstop instead.
- **Private files, checked (D1).** admind's state directories must be real directories owned by the service user: a symlinked final component is refused, and the mode is set to 0700. The audit log must be a regular file and is set to 0600 on every open; the database is opened without following a symlink and is checked again by pathname just before SQLite reopens it. A swap in that last gap needs write access to the 0700 directory, which only the service user has. Details in [admind.md](admind.md), section 7.
- **Agent supervision (D8).** admind relaunches a dead or never-started admin agent under a crash-loop limit (three launches without a `SessionStart`), then stops and tells the operator rather than spinning.
- **Control-character refusal (D3).** Operator text containing a C0 or C1 control character (other than tab and newline) is refused with a reply and never altered, before commands are parsed. Text starting with `!` is always a command and never reaches the agent.
- **A private `wn-agent` child with its own token (D1).** admind spawns and supervises its own `wn-agent`: own home (0700), own control socket, and a bearer token (0600) that admind generates and never prints. The child's output goes to a private log in its home, not to the journal. Nothing else may share the home.
- **At-most-once delivery (D6).** A message ID is claimed before dispatch and is never replayed. A message accepted but not delivered when admind stopped is answered with a "resend if still needed" reply. A paste whose delivery is uncertain is never retried; the operator is told, and the queue is held until `!interrupt` or `!new`.
- **At the CLI boundary.** `admind unit` refuses (exit 78) any value that holds a secret, an npub, a 64-hex value, a control character or something systemd would split, rather than redacting it, because the unit must be installable verbatim. argparse errors never echo a rejected argument. Filesystem and other unexpected errors print one line naming only the exception type, never a traceback or a path.

### Output policy: value-free and redacted

admind handles npubs, tokens and the operator's text, so what it prints, sends or logs is constrained:

- **Everything posted and every audit field is redacted by one function (B1, B16).** `admind/redact.py` replaces a secret, an npub or a run of 64 or more hex digits with a marker, keeps the rest of the text, and escapes every control character except newline and tab as `\xNN`. It repeats until the text stops changing (so it is idempotent), and text still changing after 10 passes becomes `<redacted text>`. It runs on every post (replies, summaries, batches, `!details`, command replies, alerts) on the whole text before chunking, again at delivery, on the summarizer's input and output, and on every audit field. `audit.clean` redacts recursively before any formatting: bytes are decoded, containers walked, compound keys cleaned, an object whose `str()` contains a backslash is written as `<Type: withheld>`, exceptions as their type and cleaned args, with `<cycle>` and `<too deep>` (past 64 levels) as the limits. In ID fields a 64-hex identifier is written as an `id:` reference (`id:` plus 12 hex digits of its SHA-256), so records stay correlated without holding the ID.
- One helper (`show`) renders anything user-supplied: a secret, npub or 64-hex value is replaced by `<redacted …>` and control characters are escaped as `\xNN`. Errors, command replies, unit names, alert names and the operator's own text in the audit log all pass through it.
- Failures are reported with fixed wording or an exception type, never `str(exc)` of a tmux, OS or peer error. The one place that records an exception message (a `wn-agent` restart failure) records the type plus admind's own fixed message, rendered through `show`.
- The audit log holds whole operator messages (redacted, under the operator's name) but no peer detail: a stranger's message is recorded as an 8-character sender prefix, a reason and a length; peer error details are dropped (only an allowlisted code is kept); the group ID, transcript paths and the identifiers of rejected input (ignored hook events, malformed peer message IDs) are not recorded. The session ID of the admin agent's own accepted events is recorded; it is a local UUID that admind generates, not a secret. Agent replies, summaries and command results are recorded by length. Message IDs are recorded as `id:` references.
- Alert files: a symlink, FIFO or oversize file is treated as malformed; an alert holding a secret or identifier is withheld (a fixed notice names the file and the kind); the outbox key is `alert:` plus the first 32 hex characters of the SHA-256 of the file name stem, so the name never appears in the key.
- The agent's reply, summaries, batches, `!details` and the `!tail` screen are relayed to the operator after redaction (a secret in them is masked and the rest is kept), and are never written to the audit log.

The accepted residual risk, verbatim from the r1 response ([docs/reviews/0001-design-review-r1-response.md](reviews/0001-design-review-r1-response.md), "Finding 10: admind"):

> **Residual risk, accepted by the operator:** anyone who can send authenticated operator messages to the admin group has `<operator-user>`-level control of the host. The protection is the operator's Marmot key, and nothing else.

`<operator-user>` stands for the host account that admind runs as; the committed review record uses that placeholder because the repository names no install-specific account.

## Configuration security (§15)

- The repository is install-agnostic, and host config and policy never go in git. The checker, pre-commit and CI enforce it ([install.md](install.md#repository-checks)).
- `policy.toml` is host-only; lower layers can only tighten it, through `[restrict]`.
- Host and workstream config files are outside every sandbox, so agents can't edit them.
- Secrets are never inline. Config refers to them by `{ file = … }` or `{ command = … }`, the loader rejects inline secrets on a best-effort scan, and config errors never echo a secret ([configuration.md](configuration.md#secret-references)).
