[BLOCKING] **§7 — maximum-lifetime relaunch.** The launch gate checks that the access token outlives the session’s maximum lifetime, but the new rule stops the session only at its *next turn boundary*. A long or stalled turn can cross token expiry before that boundary, leaving the shared refresh-token risk unresolved. Set a hard stop before token expiry, with recovery for interrupted WIP, or impose a turn limit and safety margin that guarantee a boundary before expiry. Update the round 15 disposition in the response file to match.

The §5.3 scoping fix, §14 operator decision, and other reviewed amendments are consistent with the cited spike evidence. The changed text adds no install-specific identifiers.

VERDICT: REJECT