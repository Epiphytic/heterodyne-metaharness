Native approval observational recovery source review

Code-Review: reviewer=claude-opus-5-5 author=Codex mode=cross-model range=5db4c0c27c51b4e3db5a5dee8ab94ef25d3af156..acd0a58bf98f9c72abd87b616812076a97ba4eda

Actual built-in Claude code-review reports, commands and result hashes are preserved here. Rounds 1–2 were not cleared. The medium-reasoning third review found no critical flaws and cleared the source as a PoC. No second review or validation was run. This evidence commit adds documentation only; reviewed source bytes remain acd0a58.

221 focused tests passed. Quality metrics still report complexity/churn; the result is not described as a quality pass. Final noncritical findings, downgrade compatibility limits, and required queue/snapshot/fencing qualification are retained in the actual third report. Source merge authorization does not deploy this contract.

Host-specific home prefixes in public invocation records are normalized to `${HOME}` to satisfy ADR0001. Each normalized JSON copy records its original SHA256; the original exact commands and raw streams remain retained in the controller evidence store.
