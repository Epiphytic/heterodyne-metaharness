# Terminal observation

Tmux provides persistence, not an operator UI. Hermes surfaces native requests
through the [permission relay](permission-relay.md); no human terminal login is
required or assumed. Observed text is untrusted evidence, never approval authority.

Owned panes capture up to 2,000 history lines plus the visible screen on each
inspection, subject to the existing tmux history limit. Provider observations retain
at least the last 50 available lines without a character-summary cutoff. Recognized
current modal regions are retained in full even when their heading is farther back.
Unrecognized or clipped UIs require Hermes reconciliation; missing text must not be
invented. Capture cannot recover history already evicted by tmux.

Worker and manager each retain a `terminal_buffer` in their durable run checkpoint:
exact capture SHA256, byte count, observed time, last-change time, identity
(pane, native session, launch time), and at most 1 MiB of trailing UTF-8 text.
Truncation is explicit. `changed=null` means no comparable baseline; false means
identical captured bytes; true means a difference. Native/pane/launch identity changes
reset comparison; same-identity checkpoint recovery retains it. This snapshot is a
bounded scrollback buffer, not an append-only transcript. Captures can miss transient
redraws or output outside the retained window. No claim of process inactivity follows
from identical pixels. Exact activity is separate from normalized [status](status.md)
heuristics and cannot authorize claim, recovery, completion or native approval.

Recognized worker and Hermes manager modals use durable permission evidence,
manager inbox and multipart visible outbox delivery. Evidence pins the exact native
and pane identities. Reaction consent accepts a currently bound worker or manager
identity, never a stale replacement, and remains `requires-native-inspection` under
`hermes.native-consent.v1`. This existing schema is the handoff to future native Beads
gates; this observation change creates no gate approval or automatic key presses.
A manager blocked on its own modal cannot consume its inbox until resolved: visible
Marmot evidence remains the operator path, with no claim of automatic native resolution.
