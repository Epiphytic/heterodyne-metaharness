# Actual R1 findings disposition

1. Medium resume now requires previous generation recorded install name AND digest to equal the verified launch install, as well as the qualified CLI version. Missing prior provenance refuses; native UUID alone is insufficient. Both changed-name and changed-digest cases refuse before cleanup.
2. Causal tests preserve low and other-version refusals before cleanup; a crashed qualified medium continuation verifies its pin before ending the old generation. The positive high-to-medium runtime test now asserts the first home actually contained high.

Generic model CLI capability remains explicit: actual network proof used only the recorded model, not every available model. Exact version check is an intentional narrow qualification boundary; duplicate verification remains as the later ordinary launch invariant. Lower-prior-effort transitions are not independently network-qualified; config adoption evidence covers high and default/medium prior cases only. No host custody or deployment claim.

19 focused tests pass in71.33s; Ruff and strict Pyright pass. Full repaired runtime suite is running, no PASS yet. Actual R1 remains CHANGES REQUESTED until the subsequent genuine review disposition.
