- [BLOCKING] **§7, session lifetime and §4.3:** The new credential gate says a session is parked at its maximum lifetime, but §4.3 requires a parked bead to have a blocking edge and resumes it only when that edge closes. A time limit supplies no such blocker, so the specified resume path can leave the bead parked indefinitely. Specify a time-limit stop and relaunch path that passes through the freshness gate without using the blocked-bead parking state, or define a blocker that clears at expiry.

- [NON-BLOCKING] **§5.3:** After saying no Codex hook can be relied on, “It recognises operator-only intents early” still reads as a promise for every Codex session. Scope that sentence explicitly to sessions with a verified hook.

The round 12–14 dispositions otherwise match the current S1–S4 evidence, including S1’s uncommitted Q2 paragraph. The amendments add no install-specific identity, home path, npub or IP address.

VERDICT: REJECT