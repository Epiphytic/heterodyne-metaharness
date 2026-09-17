# Shared brain notifications

Authority follows [the spec entrypoint](README.md), [context hygiene](context.md),
and [stable session ownership](sessions.md). Task state remains in Beads.

The generic session-sync Hermes user plugin and coding-provider native hooks use
one harness SQLite notice store. Managed checkpoints belong to stable run UUID +
worker/manager role, not changing native IDs. Hermes standalone sessions follow
only verified compression ancestry; independent forks keep independent checkpoints.
Unmanaged coding sessions are scoped to native ID; stable cross-ID continuity
requires harness enrollment. Inherited environment alone does not bind delegates.

Scan actual SOUL, scoped AGENTS, memories/MEMORY, memories/USER, config hashes,
canonical spec, and recursively enrolled skills/plugins. Skip generated/cache and
hidden files; reject symlinks, nonregular sources, traversal errors and exceeded
budgets. Failed scans never imply deletions. Notices contain bounded escaped paths,
actions and counts, never file contents or configuration values.

Initial enrollment offers one roots/counts inventory notice for the whole snapshot.
Acknowledgment means the inventory notification reached context, not that files
were read or their contents became known. Subsequent notices contain at most 30
paths within the text budget; additional differences remain for later turns.
One immutable pending snapshot per checkpoint survives failed delivery and restart.
Offer and acknowledgment transactions serialize; duplicate acknowledgment is a no-op.

Hermes pre_llm_call offers context; post_llm_call acknowledges only the exact
native/turn offer with its marker in the current user's api_content followed by
assistant content. The actual caller persists this sidecar: plugin documentation
claiming it is never persisted is stale. A callback or assistant_response string
alone is insufficient evidence. Earlier history cannot acknowledge unseen context.
Coding SessionStart/UserPromptSubmit offer additionalContext; Stop requires the
marker and subsequent assistant content in the bounded transcript suffix captured
at that offer. Missing, oversized or uncertain evidence leaves the notice pending.
These receipts attest context delivery, not semantic understanding or task success.

Sleeping sessions stay asleep. Catch-up occurs on the next natural turn/context
hook, including resume/compact startup. No polling, forced turn, tool interruption,
queue pickup or native approval bypass is part of notification delivery.

After confirmed context receipt, atomically retain a separate bounded path-only
visible-chat receipt. The existing outbox dispatcher resolves exact run/group
ownership (role-labeled), or a unique standalone Hermes Marmot registry entry using
its structured native/compression identity and session key. Never guess a target.
Missing or ambiguous routing remains pending for fair bounded retries. Enqueue is
deduplicated by notice token and recipient; only existing transport acknowledgment
marks the outbox delivered. Context checkpoints advance independently of visible
routing/delivery. No forced model turn is needed for retries.

Installation preserves unrelated provider hooks and plugin configuration. Replace
the legacy disabled session-sync implementation using a reviewed private backup
plan, pin its canonical package path, then enable it. Trust only exact installed
Codex hook definitions. Reconcile short MEMORY pointers after successful enable and
native invocation verification; never publish config plans or private backups.
