# Session recall

The reviewed upstream [auto-memory](https://github.com/dezgit2025/auto-memory)
revision is `888eb43c1e6a7006355dbfddda529382b50c3c1d` (package version 0.5.1).
Install from a clean checkout using `python3 install_memory.py /path/to/auto-memory`.
The installer uses a revision-specific isolated venv, a completion marker, and
idempotent wrappers in `~/.local/bin`. It never executes upstream installation
prompts or modifies native session databases. Runtime dependencies: none.
It also updates only its owned recall instruction blocks in `~/.codex/AGENTS.md`
and `~/.claude/CLAUDE.md`, preserving existing content and a first-change backup.
The upstream Markdown security scan found no findings; the runtime was reviewed
for subprocess/network sinks, read-only native connections, and transcript paths.

The installed commands are `session-recall`, `session-recall-codex`,
`session-recall-cc`, and `session-recall-claude`. The two Claude wrappers explicitly
enable `SESSION_RECALL_ENABLE_CLAUDE_BACKEND=1`. Plain `session-recall` targets
Copilot, not Claude or Codex. Claude creates its own disposable local FTS index;
it does not write source transcripts.

Use the harness command for repository-scoped recall:

```sh
workstream-recall codex list --repo /path/to/repo
workstream-recall codex search --repo /path/to/repo --query "delivery acknowledgement"
workstream-recall claude search --repo /path/to/repo --query "delivery acknowledgement"
```

The harness compares Git common directories, so existing worktrees share history
with their canonical repository. If a checkout no longer exists and cannot be
identified, it is excluded rather than attributed to another project. Claude's
upstream labels use only two path components; the harness verifies actual cwd
identity before returning data, avoiding label collisions. All output is marked
partial: Claude filters the newest 100 indexed sessions/search hits; Codex checks
the newest 100 active JSONL files and only their metadata and last 256 KiB.
Archived sessions and older portions of a transcript are outside this fallback.

**Codex compatibility:** upstream currently implements only `list`, `repos`, and
`schema-check`. On this host its read-only schema check exits 2: state migration
54 differs from expected 51, and `originator`/`daybreak_enabled` are new columns.
Its native DB query is not executed. Do not remove these columns, rewrite the
migration, or bypass upstream's schema gate. Harness recall instead uses a separate
read-only JSONL adapter supporting bounded literal case-insensitive search over
user/assistant conversation messages. It excludes tool payloads and symlinks and
does not claim to be upstream Codex search.

`memory_context(run)` provides at most 2500 characters of metadata at SessionStart
or compaction recovery; it should never run on every prompt. Historical content
is explicitly labeled untrusted data. Missing binaries, malformed indexes, and
timeouts produce a short diagnostic and do not block durable task recovery.
Recall does not replace Beads task state, session checkpoints, or explicit consent.
