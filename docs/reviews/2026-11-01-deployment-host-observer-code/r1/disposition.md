# Actual R1 disposition

The actual review reported ten issues and no blocker for the private read-only scope. Its result is preserved unchanged. The author repaired correctness findings1–3 and boundedness/testing findings8/10 before relying on observations: bind the active original pidfd and process UID/environment; omit directory/link allocation sizes from the manifest; check every entry owner and bind its normalized owner; check deadlines on both walks; require successful mount privatization and explicitly skip only unavailable namespace facilities.

Causal tests exercise an actual child carrying the old release while configured release changes, wrong UID and child exit, actual directory history, actual foreign entry ownership in a private namespace, and a directory-only deadline. The mapped manager fixture now represents inactive state; it does not fabricate a running process.

Findings4–7 remain limitations: root-controlled executable paths are checked before/after but retained file descriptors do not control exec; observer/cessation manager paths can drift; walker private APIs are reused; content and command hashes are repeated. These do not authorize effects. Finding9's test primarily exercises the existing endpoint validator; no new causal claim is made for the redundant refusal check.

The repaired actual live probe returned HOLD because the existing selected release contains a world-writable venv lock file. No permissions were changed and no PASS was manufactured. The earlier successful live read belongs to the R1 source and is retained as historical evidence only. Automatic guards remain unchecked and no effect runner is present.
