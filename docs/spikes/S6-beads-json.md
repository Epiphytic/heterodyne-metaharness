# S6: bd 1.1 JSON shapes wsd relies on (plan 3, Task 4)

Run against bd 1.1.0 (8e4e59d39) on 2026-10-05, in a throwaway embedded database (`bd init` in a temp directory with a temp `HOME`). No real queue was read or written. The fake queue (`tests/fakes/fake_btq.py`) answers in these shapes, and `heterodyne.wsd.beads.parse` refuses anything else (UnexpectedShape, which wsd treats as beads being unavailable).

## Beads

- `bd show <id> --json` and `bd list --json` both return a **list** of objects; `show` has exactly one.
- Always present: `id`, `title`, `status`, `priority`, `issue_type`, `created_at`, `created_by`, `updated_at`, `dependency_count`, `dependent_count`, `comment_count`.
- **Omitted when empty:** `labels` (no labels), `metadata` (no metadata), `assignee` (unassigned) and `dependencies` (when `dependency_count` is 0). An absent key means empty only for these; wsd reads a missing `dependencies` with a non-zero `dependency_count` as a malformed answer, never as "no blockers".
- `metadata` is an object (`bd update <id> --set-metadata k=v` returns the updated bead list). A value that parses as an integer is stored as a number (`n=5` reads back `5`); anything else, a JSON object included (`wsd_session={...}`), is stored and read back as the string given.
- Other keys may appear (`owner`, from the git email); wsd ignores keys it does not use.

## Dependencies

- In `show`, `dependencies` lists the beads depended on, each with `id`, `title`, `status`, `priority`, `issue_type`, `created_at`, `created_by`, `updated_at`, `dependency_type`, and `labels` when that bead has any.
- In `list`, `dependencies` is a list of **edges** instead: `issue_id`, `depends_on_id`, `type`, `created_at`, `created_by`, `metadata` (a JSON string). wsd reads blockers only from `show`.
- `bd dep add A B` (A depends on B, type `blocks`) returns `{"issue_id", "depends_on_id", "type", "status": "added", "schema_version"}`. Adding the same edge again also reports `added` and creates no second edge: idempotent.

## Labels

- `bd label add <id> <label>` returns `[{"issue_id", "label", "status": "added"}]`, also when the label is already there (idempotent). `label remove` of an absent label reports `removed`.
- **`bd label add` on a missing bead exits 0** with `[]` and an error line on stderr. So a label write is never trusted from its exit status: wsd reads every write back (`BeadsAdapter.ensure_*`).

## Comments

- `bd comments <id> --json` returns a list of `{"id", "issue_id", "author", "text", "created_at"}`; `[]` when there are none.
- `bd comments add <id> <text>` is **not idempotent**: the same text twice makes two comments. wsd puts a unique mark (`wsd-park: <op id>`) in each comment and looks for it before adding.

## Not found

- `bd show <missing>` exits 1 with `no issue found matching "<id>"` (btq raises it as RuntimeError). `dep add` and `comments` on a missing bead exit 1 with the same phrase inside a JSON `error`. wsd treats only this phrase as "absent"; any other failure is BeadsUnavailable.
