> Imported copy: local paths were normalised (`<repos>/`, `~`). The canonical, digest-pinned record is the design repo at commit `e36f6d0` (bead `btq-96hm`).

REJECT

All three r6 blocking findings are resolved: §4.1 and §15 now share a precedence order; §4.3 specifies the required btq configuration change; and §15 permits platform support while aligning selection with install time.

- **[BLOCKING]** The new [§15 precedence rule](<repos>/hermes-workstreams-v2/docs/adr/0001-workstreams-v2.md:561) applies to “all settings” and places workstream config above host `policy.toml`. It does not exclude policy keys, leaving a path to override policy outside the approval process required by §5.3. Specify which settings a workstream may override.