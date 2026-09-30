> Imported copy: local paths were normalised (`<repos>/`, `~`). The canonical, digest-pinned record is the design repo at commit `e36f6d0` (bead `btq-96hm`).

REJECT

The r7 blocking finding is resolved: §15 now excludes policy from the precedence layers and limits workstream overrides.

- **[BLOCKING]** The new rules contradict each other: [§15](<repos>/hermes-workstreams-v2/docs/adr/0001-workstreams-v2.md:571) says the loader rejects policy keys in workstream config, then says a workstream may add hard-deny rules or raise an approval tier. Specify where those tighter rules live and how they are loaded.