# Source qualification for the custody proposal

This is proposed design evidence, not implementation clearance or approval. The companion source manifest identifies nine exact committed source blobs and two existing parent ADRs. Source IDs S1–S9 refer to the recorded historical revisions, not whatever a worktree contains later.

S3 (`harness/gateway.py`) was inspected as working bytes whose SHA256 is `a7d1fbc0edf1554d191b2ec3e2352199411ce7f9c0329ccc6c77b60da0adbdd2`; these differ from the recorded commit. The manifest supplies the committed blob hash separately. S3's captured line ranges apply only to that qualified working observation. The working diff SHA256 was `3564278034898a1a761924b661cfec3c0f9d5cb4d028988bfa4a5f93db741625`; it is private controller evidence, not a published source revision. No authority is inferred from those working changes.

Implementation admission must bind the then-current reviewed source graph and preserve public review workflow features. This proposal neither copies working code nor authorizes overwriting canonical dirty files. Local repository/evidence root mappings remain private; a reviewer receives them from the controller. Missing private evidence is an unavailable input, not permission to substitute guessed source.

The new broker, consumer acknowledgment validator, independent custody consent/control, queue custody EX settlement and compiler compatibility ports are proposed. Existing queue operational recovery and immutable publication do not already implement them. KKV critical findings 1/2/3/6/7 remain OPEN.
