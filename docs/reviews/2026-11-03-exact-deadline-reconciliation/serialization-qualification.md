# Lifecycle serialization repair after PR162

The second full HIGH reconciliation review cleared the bounded API and identified concurrent expiry/stop/resolution/launch as a prerequisite before wiring. This continuation preserves its two actual HIGH reviews; next review is round THREE, medium, using the operator PoC question. No design re-review or new approval implied.

A typed local decorator serializes public lifecycle operations with one runtime RLock, including owner acquisition/release, reconciliation, sessions/restore, expiry, stop, launch, resume preflight, settlement and resolution. Session hooks only enqueue reports with queue_lock; they never acquire lifecycle_lock, avoiding a close/join inversion. This coordinates threads, not independent runtime instances or native authorization. Existing manager owner and exact record checks remain required. Nested observation during preflight requires reentrancy.

Twenty-four targeted tests passed (34.07 seconds), including six real deadline/dead-pane cases pausing the actual end while expiry, stop, sessions, owner release, launch validation or resolution competes. One initial test incorrectly assumed a normal launch after ending must refuse; corrected to an invalid launch that tests ordering without creating a successor. Full runtime suite running. Ruff passed and Pyright reports zero errors/warnings. Quality delta exit2: runtime class growth and generic locking-decorator similarity to Store/Journal (different lock domains); decorator usage is not recognized by its dead-code lens. Not claimed green.

No live deployment/restart, native task/owner write, pending input consumption or original UNKNOWN replay. Paired installer and native admission remain separate release gates.
