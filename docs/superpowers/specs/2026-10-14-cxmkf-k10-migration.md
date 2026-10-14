# cxmkf K10: migration away from FYI-as-question

Design: `docs/superpowers/specs/2026-10-13-cxmkf-admind-card-kinds-design.md` (K10, K14, rollout step 8).

This release adds `admind notice post` (one-way FYI, no ask), `--choice ... --recommended X` and `--yes-no` for
questions, `--link`/`--ref` links and threaded parts. `REQUIRE_QUESTION_FORM` is `False`: a question without a
form is still accepted, with a warning on stderr. It flips to `True` in a later, separate change, only after the
doc and memory changes below are in place and one controller run has posted only structured questions (Q2).

FYI asks that are already active stay until their poster cancels them (`admind ask cancel ID`); admind does not
guess which questions are FYIs.

## Controller docs

A search on 2026-10-14 of `~/.AGENTS.md`, `~/CLAUDE.md`, `~/.claude/CLAUDE.md`, `~/.claude/agents`,
`~/.claude/commands`, `~/repos/beads-task-queue/docs` and this repository's docs found no controller doc that
tells posters to send FYIs as `--kind question` asks. The only mentions are `docs/admind.md` (updated in this
PR) and the historical plan `docs/superpowers/plans/2026-10-05-admind-marmot-relay.md` (a record, left as is).
The FYI-as-question convention lives in agent memory only.

## Agent memory changes (to be made by the team lead; this PR edits no memory file)

Directory: `~/.claude/projects/-home-openclaw--hermes/memory/`.

1. `marmot-status-and-restarts.md` (must change). Replace "Until admind has a one-way notice (design bead
   btq-cxmkf), give post-merge status in the terminal only ..." and "Once notices land, use them for status"
   with: post-merge status goes out as
   `admind notice post --title "<what merged>" --body-file - --link "PR #<n>=<PR URL>"`
   (prints `posted <id>`; no reply is needed or expected). Keep "Never use `admind ask post --kind question`
   for a status or FYI". Add the bounds: 12 notices an hour, 10 undelivered; over them the post is refused
   (`refused: <why>`), so keep status to one notice per merge. Update the description line to match.
2. `marmot-question-asks-lead-with-choice.md` (must change). The hand-written template (label line, `Reply:`
   line, `Choices:` footer in the body) is now rendered by admind. Replace it with: post questions with
   `--choice "<text>"` (2 to 6, lettered A-F in order) and `--recommended <letter>`, or `--yes-no`
   (`--recommended yes` is the default); the title states the decision; do not repeat the label, the options
   or a `Choices:` line in the title or body. Note that admind records which choice a 👍 resolved to
   (`admind ask get` shows `--- answer (choice A) ...`; `--json` has `choice`), which replaces "Record which
   option a 👍 resolved to". Approval labels now come from the bead's readout `purpose`, not the title; the
   advice to start approval bead titles with "APPROVAL: design — " stays only until btq reports `purpose`
   (the K17 btq change has its own design approval).
3. `marmot-links-and-threading.md` (must change). Replace "Until that lands, keep ask bodies short enough to fit
   in a single part" with: parts after the first are threaded under it automatically; add links with
   `--link "<label>=<pinned GitHub URL>"` or `--ref <path>[@<rev>]` (run in the repository; the commit must be
   pushed). Bare repo paths in the title or body get a stderr hint.
4. `asks-via-marmot-not-terminal.md` (should change). "With no approval bead, use a QUESTION: yes/no ask" becomes
   "use `admind ask post --kind question --yes-no` (or `--choice`)", with the pinned commit and URL as
   `--link`/`--ref`.
5. `pr-merge-asks-need-link.md` (review). The full PR URL is still required in the message to Liam; a merge ask
   can also carry other links with `--link`.
6. `plan-review-before-implementation.md` (no change needed). Rule 4 (decisions through Marmot asks) is still
   right; it does not mention FYIs.
7. `MEMORY.md` index lines for items 1 to 4: update the hooks, e.g. "FYIs go out as `admind notice post`, never
   asks" for marmot-status-and-restarts, and "questions use --choice/--recommended or --yes-no" for
   marmot-question-asks-lead-with-choice; drop "fix scoped into btq-cxmkf" from marmot-links-and-threading.

## Completion evidence

btq-cxmkf is not closed until its close note lists the changed doc and memory files above, and the changes take
effect in the same rollout step as the daemon restart (design rollout step 8).
