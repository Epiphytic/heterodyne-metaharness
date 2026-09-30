# Security model

This is a summary of the security design in [ADR 0001](adr/0001-workstreams-v2.md). The ADR is authoritative; read the cited sections for detail.

**Implementation status.** Only the configuration side exists today: the action-class tiers, host-only policy, `[restrict]` tightening, and the rejection of inline secrets (see [configuration.md](configuration.md)). The policy engine, approvals, sandbox, self-test and `admind` described below are the target design for later plans.

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

- **Independent.** It has its own service unit and its own Marmot identity, and shares no dependency with `wsd`, the Hermes gateway, beads or the gatekeeper. It works in a two-member group (the operator and the admin bot), refuses to operate if the group has more members, and accepts messages only from the operator's exact npub.
- **Passthrough.** The operator's text goes byte for byte to a persistent session of the `admin` profile, and replies come back verbatim. There is no gatekeeper LLM in between.
- **Deliberately privileged.** The agent runs as the harness's service user, with permission prompts bypassed, no sandbox and no root. Its purpose is to repair anything the harness can break, so a least-privilege identity would defeat it. This is an operator decision (2026-09-29); the design review's objection and the rebuttal are recorded in the r1 response.
- **Mitigations:** the sender must be MLS-authenticated as the operator's npub (§3.4); the two-member group check; admind's own Marmot keys are readable only by its unit; every message and action goes to an append-only log.
- **Built-in commands** need no LLM: `!new`, `!interrupt`, `!tail [n]`, `!ps`, and `!restart <unit>`, which accepts only units from a fixed allowlist in admind's config.
- **Audit** is a local append-only JSONL log plus the agent transcript, kept separate from beads on purpose. admind also relays `wsd`'s local alerts to the operator.

The accepted residual risk, verbatim from the r1 response ([docs/reviews/0001-design-review-r1-response.md](reviews/0001-design-review-r1-response.md), "Finding 10: admind"):

> **Residual risk, accepted by the operator:** anyone who can send authenticated operator messages to the admin group has `<operator-user>`-level control of the host. The protection is the operator's Marmot key, and nothing else.

`<operator-user>` stands for the host account that admind runs as; the committed review record uses that placeholder because the repository names no install-specific account.

## Configuration security (§15)

- The repository is install-agnostic, and host config and policy never go in git. The checker, pre-commit and CI enforce it ([install.md](install.md#repository-checks)).
- `policy.toml` is host-only; lower layers can only tighten it, through `[restrict]`.
- Host and workstream config files are outside every sandbox, so agents can't edit them.
- Secrets are never inline. Config refers to them by `{ file = … }` or `{ command = … }`, the loader rejects inline secrets on a best-effort scan, and config errors never echo a secret ([configuration.md](configuration.md#secret-references)).
