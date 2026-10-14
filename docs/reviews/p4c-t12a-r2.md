Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: dc4b03926129bfb4591793134e514517e5dd12cb (range 39a052a2e3a190727573af7a0e3ef19d718faa96..dc4b03926129bfb4591793134e514517e5dd12cb). Verdict: REVISE.

Note: code-review cycle 2 of 3.

- **[BLOCKING] `deploy/store-helper/README.md:169–177` — Rollback removals escape the fail-stop script.** If stopping an instance fails, or the process check refuses, only the inner `sudo bash` exits. A normal shell pasting the whole block continues with the removals and `userdel`. I reproduced this control flow with harmless stand-ins. Put the checks and removals in one fail-stop script.

- **[BLOCKING] `deploy/store-helper/README.md:176` — Copy-paste rollback deletes the store unconditionally.** The command block contains `rm -r /var/lib/heterodyne` without checking emptiness or whether installed harnesses remain in use. Stopping helper publishers does not establish that consumers have stopped using published installs. The condition stated at line 182 must guard the deletion.

- **[BLOCKING] `deploy/store-helper/README.md:148` — The sed command can delete another table’s fallback and miss the intended setting.** Valid TOML permits indented table headers. With `[harnesses]`, `fallback = true`, then `  [harnesses.claude]` and its own `fallback = true`, this command deletes both fallback entries. Conversely, `[harnesses] # host overrides` or `fallback = true # enable fallback` leaves the intended setting untouched. Both cases reproduced. Use an edit that respects TOML table boundaries and comments.

- **[BLOCKING] `deploy/store-helper/README.md:146` — Config-path resolution differs from heterodyne’s.** `config_dir()` expands a leading `~` in `HETERODYNE_CONFIG_DIR` and ignores relative `XDG_CONFIG_HOME` values (`src/heterodyne/config/paths.py:26`). The shell expression does neither. For example, with `XDG_CONFIG_HOME=relative`, it can edit an existing relative config while `config check` reads `$HOME/.config/heterodyne/config.toml`. Resolve the path using the application’s rules or pin the concrete host path in the approval.

R1 findings **1+3 are closed** under the stated approval-manifest trust model: the root-only stage prevents service-user races; precreation makes `mkdir` fail; archive verification precedes extraction; root executes and installs staged bytes. The manifest generator matches step 3’s archive invocation and checksum paths. Different Git archive bytes fail closed. Archive traversal or links would require those bytes to be approved.

R1 finding **2 is closed**: service ownership and writability are both refused, ownership reads use one stat function, and root resets PATH. The fake stat leaves root-specific execution untested, but I found no hidden defect in that branch.

Finding **4 remains open** because of the rollback issues above. Finding **5 remains open** because of the config-edit issues. Explicitly declaring the wsd restart **N/A** is acceptable for the stated host condition if the execution ask carries it.

Validation: **34 offline tests passed**; ruff, pyright, and diff whitespace checks passed. Repository unchanged; no privileged operations run.

**Verdict: REVISE**