# Five real supervisor cards: publication evidence

This thin archive contains five whole, actual **unsent dry-run** cards for `btq-iz0rd`, `btq-5dh3q`, `btq-wpyv5`, `btq-f41ih` and `btq-qcrlb`. All five were classified as stalled and rendered Decidable; their source reports 44–46 hours of inactivity. Process state is unknown. Elapsed time is not evidence that a worker died. Each body, title, choice and facts digest is copied exactly from the retained preview, including its existing sanitization and More section. Request tokens are preview identifiers, not proof of delivered or existing asks.

The timer remains **OFF**. These artifacts neither send a card nor authorize recovery, task closure, deployment or bypass of native gates. Root will publish them for Q1/Q2/Q3 decisions and a separately scoped one-scan deployment request through Marmot. No such approval is claimed here.

## Source and review

The deployed queue source is `5a4d4d0d91cee0a75afa414a4ca5b7d0bbc31af6`. Its actual additional MEDIUM built-in `/code-review` concluded **APPROVE: good enough to implement as a PoC**, exit 0; the full raw Markdown is in `reviews/`. The reviewer explicitly did not run tests and relied on the author's 184-test result (two skipped). Earlier review history, including the initial uncertain-summary REVISE, is not reset by this follow-up.

The original approved card design is retained byte-for-byte from published `e0554aac259b34713eba138f7557b2cc921fb9f9`. Its historical design-only header is preserved. Immutable published design: https://github.com/Epiphytic/heterodyne-metaharness/blob/e0554aac259b34713eba138f7557b2cc921fb9f9/docs/designs/2026-10-14-btq-supervise-cards.md .

## Accepted noncritical review observations

The actual review retains three minor observations without changing its approval: abandoning a newer uncertain summary can clear an older verified summary's fast-path marker and delay a card until the normal stall threshold; a crash before the native comment executes still requires conservative abandonment and permanently forbids retrying that exact body; and a dry run may show a normal action after settling an in-memory pending intent even though only a real scan persists its pin. No unsafe resend or delivered ask is inferred. The raw review states the exact scenarios.

## Evidence boundaries

`cards/five-whole-cards.md` is the readable five-card document. Per-bead Markdown bodies are exact UTF-8 source bodies; `five-rendered-cards.json` preserves the seven selected row fields. Four actual new summary receipts are included with command/file paths removed. The first four pre-fix writes (`n482b`, `p964v`, `ywnix`, `qdyj7`) are intentionally excluded; their history and timestamps are not repaired retroactively. The archive contains no whole native/runtime database or private runtime dump. Hashes and source pins appear in `evidence/manifest.json`; this Git commit supplies the immutable archive revision.

Exact render preservation includes one trailing space on each Idle line and the rendered final newline. Git whitespace checking reports those preserved source bytes in per-card/combined Markdown; they are intentionally retained rather than silently formatting the evidence.
