# Maintenance recovery NOTICE consent and permits

This implements only the admind side of the approved maintenance operational
recovery design, §6. It never executes a queue operation or changes a native task,
approval, membership, or decision. Installation, enrollment and activation require
separate deployment authority. The queue executor must independently validate all
proofs and consume the permit under its exclusive maintenance fence.

The additive local client is:

```
admind maintenance-recovery register --descriptor FILE --proof-set-sha256 HASH
admind maintenance-recovery status PROPOSAL_HASH
admind maintenance-recovery permit PROPOSAL_HASH
admind maintenance-recovery permit PROPOSAL_HASH --certificate-sha256 HASH
```

Replies are JSON. An unavailable/ambiguous socket reply is not proof of failure:
query the exact proposal hash. Registration is not consent. Only an authenticated
current operator's exact `!maintenance settle TOKEN` reply to the actually sent
NOTICE can select consent; `!maintenance revoke TOKEN` tombstones pending consent.
Messages retain original provenance. Replays with altered content refuse. Other
responses, reactions and approve words cannot select a recovery decision. Existing
ask statuses, APIs, answers and capacity are unchanged; normal NOTICE limits apply.

## Deployment-owned enrollment

The fixed daemon state subdirectory is `maintenance-recovery`, mode 0700. Its
`enrollment.json` is an owned 0600 regular single-link file, with this closed shape:

```
schema: admind-maintenance-recovery-enrollment.v1
enrollment_sha256: canonical SHA256 of the enrolled queue enrollment
source_closure: canonical SHA256 of that enrollment's sources object
coordinator_root: absolute fixed queue coordinator root
descriptor_links: map of at most 128 proposal SHA256 keys to immutable public links
```

The queue's `enrollment.json`, `coverage.json`, source hashes and coordinator hash
must verify. Its roots map must explicitly bind `admind_maintenance_recovery` to
this daemon directory. No request may supply a registration path or coordinator
locator. These checks establish consistency within the trusted same-UID host;
they do not authenticate arbitrary same-UID code as an operator.

The ordinary redactor is unchanged. Exact material hashes which cannot be shown
without redaction require a registered immutable public GitHub blob link to the
complete proposal/source/proofs. Missing, unsafe, oversized or materially redacted
content holds registration rather than shortening the consent scope. Updating this
bounded data-only link catalog is an operational input, not consent or a software
design change. Catalog publication/verification by a real deployment owner remains
an integration prerequisite; this source does not install or publish it.

## Queue composition contract

Canonical JSON is sorted/compact, ASCII escaped, finite, UTF-8 with no newline.
Descriptors retain the exact approved `maintenance-recovery.v1` fields. The original
operation record is read only from `operations/OPERATION.json` under the enrolled
coordinator root and its entire canonical digest must match.

A DB-committed permit candidate is published as identical owned 0600 bytes at:

```
maintenance-recovery/generations/PROPOSAL_HASH/GENERATION.json
maintenance-recovery/permits/PROPOSAL_HASH.json
```

The immutable generation is durable before the atomic current-file replacement.
Restart/repeated issuance repairs that same candidate without extending its
30-second BOOTTIME initiation deadline. A genuine boot ID and current authorized
principal are required. Generations are bounded to 1–3.

Renewal reads only the enrolled queue root's `recovery-v1` files:

```
PROPOSAL_HASH.nonconsumption-GENERATION.json
PROPOSAL_HASH.superseded-GENERATION.json
```

They must be identical immutable bytes of the closed
`maintenance-recovery-nonconsumption.v1` certificate, binding proposal, old permit,
generation, operation, original record, effects root, enrollment, boot and observed
BOOTTIME. Any consumed/issuing/receipt marker holds renewal. The queue must publish
this certificate under EX and release its locks before calling admind. This package
does not implement that executor or assert any native effect was absent/completed.
