Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: 39a052a2e3a190727573af7a0e3ef19d718faa96 (range 5a9ec58bca0f46ab54b1962a82d9d4fa0cd3f5c3..39a052a2e3a190727573af7a0e3ef19d718faa96). Verdict: REVISE.

Note: code-review cycle 1 of 3.

- **[BLOCKING] `deploy/store-helper/setup-store.sh:81–82,128` — Git replacement refs defeat commit pinning.** The service user can create `refs/replace/<approved commit>` pointing to a different commit. Full-commit verification still returns the approved ID, but `cat-file` reads the replacement helper. I reproduced this in `/tmp`. Disable replacement objects for every Git invocation and verify the staged bytes against the approved blob.

- **[BLOCKING] `deploy/store-helper/setup-store.sh:150–155` — Non-writable ancestors can still be controlled by the service user.** A service-owned ancestor such as `/usr/local/libexec`, mode `0555`, passes `test -w`. Its owner can subsequently chmod it and replace a checked descendant with a symlink before root installs files. Reject service-owned ancestors as well as writable ones, and ensure checked paths cannot be changed by that user during installation.

- **[BLOCKING] `deploy/store-helper/README.md:49,75–76` — Root executes and installs mutable checkout files.** The checkout explicitly belongs to the service user. That user can modify `setup-store.sh` before root executes it, bypassing all internal checks, or replace the units after the script checks checkout cleanliness. A malicious unit can execute arbitrary commands as root. Execute and install verified, approved bytes staged under root-controlled paths.

- **[BLOCKING] `deploy/store-helper/README.md:117–120` — Rollback does not stop active helper instances.** Disabling the `Accept=yes` socket stops new connections but leaves existing template service instances running. Rollback can then remove their store or delete their account while a publication is active. Stop and confirm termination of all helper instances before removing store data or the account.

- **[BLOCKING] `deploy/store-helper/README.md:109–111` — Step 6 lacks the required exact command list.** “Remove … from the host config” and “Restart `wsd`” specify neither the config path/edit command nor the restart command. An approval quoting this README cannot authorize concrete execution of all steps 3–6. Resolve those host-specific commands before requesting execution approval.

Validation: **315 tests passed**; ruff and pyright passed. The two unit deviations match the helper interface and intended socket-group behavior.

**Verdict: REVISE**