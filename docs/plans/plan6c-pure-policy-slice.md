# Plan6C independent pure policy slice

This is partial implementation of approved Plan6C d4 §§3–5, not completion or activation of
`btq-4w2ec`. The approved published source is
`80eb65b5449531d4ad4e5acc1b4cad8bed0d57ce:docs/designs/2026-10-16-plan6c-durable-delivery-d4.md`;
genuine design approval is `btq-nl628` (archived approved revision
`b4c464f7120ac7ac8fdcdefa24f4a85124879f6c`). §1 expressly permits pure reducers and fake adapters after
approval; dependent adapters still require final compatible 6A/6B contracts and frozen producer
protocols before their implementation review. No Plan5 port is assumed.

`heterodyne.delivery` imports only the Python standard library. It is not imported by any service or
command. Frozen identity DTOs retain source/logical identity, full destination identity, exact source
revision, rendering/config identity and reason occurrence. Copy admission checks compare all immutable
payload fields while reusing the original admitted random copy ID. They do not allocate a durable
generation. A disposable dictionary-backed fake owner exists only in the tests.

Frozen copy parts are already final redacted/framed text; no renderer, redactor or rechunker is supplied.
The pure bounds enforce canonical-v1's 1024 parts, 16KiB/part and 512KiB total; display-v1's 20 parts and
64KiB total; and caller-supplied character/frame bounds. UTF-8 is measured exactly. Metadata strings
have a conservative local 4096-character bound; this is not a qualified wire admission profile.
The independent component does not establish that all existing native admitted cards fit: complete
6A/native Unicode/link/chunk compatibility qualification is still required before protected posting.

The immutable reducer proposes a reservation, a dispatch mark, correlated positive prewrite refusal,
uncertainty, or exact receipt settlement. It retains at most three attempt records with ordinals due at
initial eligibility +0/+5/+30 seconds. It does not sleep, schedule a timer or perform a send. WSD keys
bind the full frozen copy/part; canonical keys preserve ask:/askr:/askd: grammar and never contain an
attempt ordinal. Reserved-no-dispatch is accepted only for a still-reserved attempt; after a dispatch
mark, this partial reducer accepts only a definitive matching receipt to settle uncertainty. It never
uses a generic transport error, not_found, elapsed time or restart as non-dispatch evidence.

A receipt must match the exact attempt, account, group, native key and text hash and return exactly one
64-lowercase-hex message ID. Empty, malformed, multiple and conflicting/cross-copy IDs remain uncertain;
bounded raw diagnosis is retained without selecting a first-ID success. The future owner supplies
retained receipt claims. Current-copy summary requires every exact part of one copy; a confirmed prefix
is incomplete. Supersession preserves issued/uncertain history, and a late receipt cannot reactivate it.
Cancellation requires never-reserved or positively unissued evidence.

Gates, proofs, time and receipt claims are trusted **owner facts supplied to a pure function**, not
operational authority, authenticated transport evidence, a validated clock, a durability certificate or
an API for accepting arbitrary network input. No live caller is wired. The future owner must qualify
its typed decoder and source/trust/parent predicates, persist each transition and satisfy the recovery
barriers before effects. An in-memory constructor or successful reducer call cannot authorize dispatch.
The module cannot certify healthy absence, fence an old sender or reconstruct durable history.

The existing Store delay/group owner, canonical ask pipeline and unmanaged behavior are unchanged.
Store migrations/protected posting, source ports, transport phase adapter and receipt custody, registered
6B guard, current native approval checks, reminder/digest lifetime budgets, escalation/alerts, neutral
writer, recovery log/checkpoints/restoration, recovery actions, route wiring and all live deployment
remain unimplemented by this slice. No live proof or full-Plan6C PASS is claimed.

Offline contract tests are `tests/test_delivery_policy.py`; they exercise exact conflicts/bounds,
identity binding, stable keys, bounded deadlines, source/trust/parent gates, no replay of uncertainty,
cancellation and full-copy receipts. Isolated mutation evidence is retained outside the source tree.
The original tests and production source remain unchanged. Full code review and all downstream genuine
integration/deployment approvals remain separate gates.
