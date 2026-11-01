# R2 disposition and cumulative repair

Actual second full Claude Opus HIGH review requested changes. The unaltered findings are retained alongside this disposition. The reviewer reported running a trigger reproduction despite the read-only/no-tests instruction; that deviation is retained, not converted into authored validation.

- Hidden SQL trigger: schema filtering now compares the literal sqlite_ prefix, refusing sqlitextrg and every unexpected object. Existing foreign schemas are compared before any schema or journal-mode write. Actual root storage regression rejects hidden triggers and weakened indexes without altering the latter schema.
- Bootstrap nonce: the handoff explicitly retains native receiver_nonce_hex A from the private verified capability; raw Consumed must equal that exact A. Admission nonce B remains a distinct sealed descriptor and response correlation. No native receipt is rewritten. Matching native parent repair is required before installation.
- Postcommit OSError: safe negative settlement also handles transport/proc faults after committed admission. A causal root entry test verifies the tombstone and subsequent genuine ledger replay refusal.
- Recovery: recover_never_issued is a root-only journal operator operation with no inputs. It atomically settles only ready records with no issued events, retains all one-use identifiers, and refuses to release event-bearing holds. This releases safe negative holds without restoring authority.
- Deadline: re-read the actual clock after the potentially blocking transaction rather than relying on the prior in_window value.
- Causal loss test: inject the postcommit failure only on the initial process. The two fresh process replays reach the actual one-use ledger and are refused independently.
- Duplication and decomposition findings are deferred for this finite PoC. No effect, installer, complete result, public receipt importer or future outage guarantee is added.

Validation: 29 focused tests pass, including actual mapped-root storage/entry/credential/OFD mechanics; synthetic test consent remains explicitly non-authoritative. Ruff and Pyright results are recorded separately. Earlier genuine native fixture qualification remains historical evidence of its precise source hashes; it is not misrepresented as a rerun of this new source. Previous quality debt is retained.
