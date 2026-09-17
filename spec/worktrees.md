# Task worktrees

Authority: [tasks](tasks.md), [sessions](sessions.md), [change lifecycle](changes.md).

New repository Beads get an owned local branch and checkout under
runs/RUN/beads/ISSUE. The base is the canonical local repository HEAD (operator
integration owns that checkout); no fetch or clone occurs. The intent manifest
pins that base before Git mutation. Retry adopts only the exact recorded path,
branch and repository, never a latest branch or another writer's checkout.
Existing bound Beads without a task manifest remain in their existing checkout.
A new bind/claim requires a clean worktree and a confirmed idle native worker,
with no approval or recovery hold. Dirty boundaries are workflow errors.

Existing .venv, venv and node_modules directories are copied locally using
cp --reflink=auto: reflinks when supported, independent local copies otherwise.
No download/install is run. The manifest records sources and the fallback policy,
not an unverified claim that reflinks were supported. Dependency-root symlinks and
tracked destination files fail closed. Python environments can retain absolute
script shebangs and editable paths: inspect them before mutation; dependency
reuse does not promise arbitrary venv relocation or version compatibility.
[Agenticow](https://github.com/ruvnet/agenticow) implements vector-memory branching,
not filesystem worktrees; its sharing principle is reused without a new package.

A successful boundary exact-resumes the same idle coding conversation in the new
cwd, removes stale Codex -C arguments, and sends no task prompt. Manager identity
is unchanged. Hook cwd checks use the new durable workdir. Interrupted switches
retain workspace_transition for operator reconciliation; do not replay workloads
or infer success from a directory's existence. Native sandbox cwd permissions must
be supplied by the resumed provider; the harness never edits approval settings.

Completed worktrees are retained. No automatic deletion/pruning exists. Explicit
operator cleanup must verify closed Bead, pinned completion evidence, correct
manifest/path/branch, no active worker at that cwd, clean Git state and no needed
untracked/ignored dependency artifacts. Remove only that exact worktree through
Git; never enumerate-and-delete unrelated worktrees or force removal.
