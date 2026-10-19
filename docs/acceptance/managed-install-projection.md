# Offline managed-install projection

This is a subordinate implementation step of the approved Plan9 release inventory,
not a completed operational collector. The design is
[Plan9 d4](https://github.com/Epiphytic/heterodyne-metaharness/blob/e009a2c6d1a4c7887bb5c5effd5f7d9bbbd023cc/docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d4.md).
Its approval is `btq-e9toz`. Sections 1, 5 and 10 require complete source proofs;
this implementation preserves those requirements.

`heterodyne.acceptance.install_projection.verify` accepts an already-open private
fixture installation directory, its captured manifest and original record bytes,
and an independently admitted adapter name, declared versions and manifest digest.
It uses the actual managed-store manifest and record decoders, `check_fast`, and
`entries_of`. The record codec has public read-only wrappers in `store_core` so
consumers share the existing implementation. Existing store operations keep their
original private interfaces and behavior.

The check refuses unexpected metadata before walking the tree. It checks the
original record before and after a complete content hash pass. It never creates
a fresh record to make an altered capture pass, follows a path to a host store,
calls the taint-writing verifier, invokes a CLI, or registers a proof adapter.
Both codex and claude private fixture installs use the same store-owned checks.

Each metadata input is at most 1 MiB. A manifest is at most 4,096 entries and
64 MiB of regular file content. The hash reader enforces a cumulative 64 MiB
budget even if a capture changes between checks. The original store walk still
has its own 200,000-entry and depth-64 limits, including unexpected directories;
this is not a smaller work or latency bound for adversarial directory trees.
Reads that cannot be completed or proven consistent return a typed HOLD.

Success means only that the sampled private fd matches the specified manifest.
Expected metadata must come from independently admitted source configuration;
supplying one's own matching digest is not authentication. The result always
refuses `require_authority`. Loaded-code provenance, acquisition authenticity,
mutation audit continuity, registry completeness, both actual managed adapters,
terminal fences and fresh endpoint comparison remain prerequisites. The default
operational proof adapter continues to HOLD for `release_inventory` and
`current_release` as for every other unsupported role. There is no deployment,
live observation, drift watcher or claim of a completed qualification segment.

Tests use private temporary installs and the real store parser/walker/hash code.
They cover both adapter labels, metadata-before-I/O refusal, malformed and
noncanonical records, tree/content changes, mutation while hashing, metadata and
hash budgets, invalid fds, and refusal to open a store or write a taint marker.
