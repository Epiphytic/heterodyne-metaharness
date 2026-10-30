# Fixed root installer primitive: source qualification

This source implements the approved build/deployment queue design's bounded root
installer step. It does not install a helper, sudoers rule, service, account,
profile, permit producer or public entry point. A root-owned installed
interpreter/package closure and the protected admission owner's exact permit
publication remain prerequisites. No runtime deployment authority follows from
these fixture tests.

`deployment_root_helper._RootInstaller` consumes a private root-owned mode-0700
store. Every retained input is a regular root-owned single-link mode-0400 file,
opened by anchored descriptor with no symlink following and bounded readback.
The closed profile fixes one target, executor UID, checkout, and sudo/bash/helper
hashes. The closed permit retains the entire exact packet and its INSTALL ordinal,
before-state digest and bounded deadline. The packet must exactly match the
profile, helper and sudo pins and the protected target mapping. There is no
command, path, flag, credential or environment selector in the request: the
private call accepts only its lowercase 64-character packet SHA-256.

The permit's producer must independently authenticate genuine whole-packet
approval, admit the finite outage capsule/recovery route, obtain current stopped
guard evidence, and reserve the target. This module deliberately provides no
permit creation or approval import API. Root file ownership is a custody
precondition, not a claim that an arbitrary correctly shaped JSON file proves
human approval. The installed owner must retain exactly the root profile bytes
also checked at `/etc/heterodyne/deployment-install-profile.json` by the native
authority adapter. The source primitive does not open that production path.

The sudo gate checks real/effective root UID, exact `SUDO_UID`, and the actual
immediate parent through `/proc` identity. A pidfd is opened and closed as a
capability check; it is not retained and does not supply those identity reads. The parent must
have kernel UIDs `(executor, 0, 0, 0)` and the same device/inode as the pinned
root-owned non-writable `/usr/bin/sudo`, with stable PID/start time and ancestry.
An environment variable alone cannot satisfy it. The actual host's sudo monitor
topology passed a read-only test executing only the inspected provenance
functions with system Python; no Product imports or installer effects ran as
host root. Another platform/topology must qualify this contract or refuse.
Root itself remains trusted.

One root-private temporary descriptor retains the exact pinned installer bytes
through hash checking and invocation. The exact protected bash executable is
also retained by descriptor. The recipe is:

```
<pinned bash fd> <private installer fd> --no-wsd --no-verity
  --service-user <fixed target user> --checkout <fixed profile checkout>
  --commit <packet full source commit>
  --expect-sha256 <packet release archive SHA-256>
```

No fetch, adopt, prefix, staged override or arbitrary shell expression is
accepted. The existing installer resolves and checks its own exact commit archive
before running its staged copy. `--no-wsd` can stop/disable managed WSD: that
actual effect must remain in the deployment packet/approved outage scope. Its
whole host-tool/runtime closure, offline dependency cache, helper/store drift
preflight and source-specific writer exclusion still require deployment-time
qualification. This primitive does not silently authorize installer drift repair.

A per-target flock serializes the installer call and is inherited by the child.
A per-packet `O_EXCL` issued record is written and fsynced with the directory
before the effect. It records UNKNOWN, including exit zero, nonzero and process
launch exceptions. An existing record refuses every retry. The helper does not
mark APPLIED, release a target, settle historical UNKNOWN or restore a database.
The owning controller must retain its durable target reservation across helper
failure and independently reconcile all possible surviving writers; the local
flock alone is not a crash-recovery authority.

Tests execute a harmless pinned fixture script as root inside a disposable user
and mount namespace. Two private tmpfs mounts isolate `/tmp` and `/run`; no host
installer or service is changed. A distinct mapped UID receives three actual kernel
read/write/create denials on protected custody and one separate Python UID-gate
refusal. Sudo admission is explicitly
synthetic in that mechanics test and independently tested read-only against the
real host monitor. Tests cover fixed argv, malformed ordinal/deadline/digest/
before-state rejection before issuance, tampered installer rejection, durable
UNKNOWN after nonzero exit and launch error, and one-use refusal. They do not
claim protected profile/permit producer enrollment, native transport approval,
complete installed source closure or operational host deployment qualification.

The proposed runtime writes, requiring an exact deployment approval, are the
fixed helper and its enrolled interpreter/package, root profile, narrowly scoped
sudoers entry (no SETENV and no wildcard root bash), private root store, exact
permit/artifact files, per-target lock and per-packet issued journal. During an
authorized call, the existing installer also writes its approved root-private
stage and the bounded store/unit/release/config/service set described by the
approved deployment design. None of those installation writes occurred here.

Final source-bound qualification ran with inherited `PYTHONDONTWRITEBYTECODE`
unset on the qualified host: 121 focused tests passed in 3.82 seconds; Ruff and strict source Pyright
passed. The local worktree lacks its own `.venv`; checks explicitly use the
existing Step1 test interpreter. The manifest records exact source SHA-256,
public command templates and raw argv SHA-256, log hashes and unchanged source
through the run.

The actual quality check exits 2: twelve findings, two gating duplication rows
and ten new-symbol rows (including private DTOs/pytest entry points and the
large private effect primitive/namespace fixture). The duplication detector
normalizes `_stat_key` with existing stat/session comparisons. Those existing
helpers compare different identity fields, including one specialized store-core
record rather than `os.stat_result`; importing them would weaken the descriptor
stability predicate. The local predicate deliberately retains mode/UID/GID/link
count/size/mtime/ctime while ignoring read-induced atime. No metric waiver,
baseline reset or fabricated quality pass is applied. Full actual output is
retained beside the test manifest for review.


The actual first HIGH built-in review found no critical correctness or security
flaws in this dormant helper; the complete report and exact execution receipt
are retained under `deployment-root-helper-code/r1/`. The helper SHA pin checks
the custody and bytes of the named file. It does not establish that this file or
its interpreter/import closure is the actual running root process. That complete
installed running closure remains an enrollment prerequisite, not fixture proof.

The 121-test count is specific to the qualified host: the read-only real sudo
probe requires noninteractive sudo for system Python, and namespace mechanics
require user/mount namespaces plus subordinate UID mappings. These tests have
no automatic capability skip and must not be advertised as universally portable.
Remaining noncritical source findings (profile control-character validation,
intermediate descriptor cleanup and early retry diagnostics) are retained in the
actual review for follow-up. No runtime/source behavior changed after review.
