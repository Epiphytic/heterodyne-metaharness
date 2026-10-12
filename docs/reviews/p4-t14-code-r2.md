# Plan 4 Task 14 code review, cycle 2

Reviewer: gpt-6.1-sol. Reviewed commit: 4e916eacfa398a28a3c0bde956bd666ce0542ab4. Verdict: REVISE.

Note: fixed in 3be0021 (1) and 7b9045a (2), plus a5aef9e: after reading the OpenShell v0.1.2 source, the controller found that the runtime binary is bind-mounted, not image-mounted. Finding 3, the plan wording, is the controller's erratum in 254ad41.

1. [BLOCKING] tests/live/test_live_sandbox.py:308 — Teardown still treats an unresolved create as clean. After a create timeout, runtime cleanup can record `ENDED` with `create_started=True`, `created=False`, while deliberately retaining the watcher for a sandbox that may appear later. `stop()` then does nothing, and an empty sandbox listing makes `teardown()` return no uncertainty; the fixture kills that watcher and deletes its records. An offline mock confirmed this result. Fix: preserve scratch and tmux whenever a create remains unresolved, including in an ended record, until its completion or cancellation is established.

2. [BLOCKING] tests/live/test_live_sandbox.py:233 — Preflight checks configuration files on disk and images in the client’s local Podman store, without establishing that these belong to the running gateway. Files changed without restarting the gateway can pass while the process retains an image override; its configured Podman socket can also address a different image store. The recorded digests therefore need not identify the artifacts actually executed, leaving cycle-1 finding 2 unresolved. Fix: verify the targeted gateway’s running version, effective configuration and Podman endpoint before launch; inspect through that endpoint and confirm the actual runtime, supervisor and workload image IDs used.

3. [NON-BLOCKING] docs/superpowers/plans/2026-10-09-plan4-agents-and-sandbox.md:6354 — Task 9B incorrectly says the supervisor comes from `ghcr.io/nvidia/openshell/sandbox:0.1.2`. OpenShell v0.1.2’s Podman driver separately ensures the sandbox runtime and supervisor images. Fix: name both default images and require recording both identities; align Task 14’s precondition wording.

REVISE