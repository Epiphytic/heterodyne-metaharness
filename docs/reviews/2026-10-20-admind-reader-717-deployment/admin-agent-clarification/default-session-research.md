# Five default-socket sessions: bounded metadata investigation

Observed sessions2,21,5,6,admin-20 on /tmp/tmux-1001/default only. Exact time, PID/start-tick/ancestor/executable/cwd/FD metadata is retained in metadata.json. No command lines, environments, panes, transcripts, DB contents or checkpoint contents were read. No controls/messages were sent.

| Session | Observed live foreground descendant | Assessment |
|---|---|---|
|2|Only bash1142559, cwd .hermes|No observed current command child; shell can launch future work.|
|5|Only bash3342168, cwd <operator-home>|No observed current command child; unrelatedness not authenticated.|
|admin-20|Only bash1579833, cwd .hermes|Name alone does not bind it to the private admind admin agent.|
|21|Codex823308 below bash2556607, cwd repos/polytrader|Distinct repo context; not evidence of an admind writer or safe-to-stop session.|
|6|Codex174221 below bash1632860, cwd .hermes; zombie chrome descendants|Potential current operator/controller session; cannot infer stopped work or safe termination.|

All five belong to default tmux server63042, executable /usr/bin/tmux, ancestorPID1. No observed live descendant has admind/python/systemctl as its first executable. No observed descriptor points to the exact admind DB/WAL/SHM path (see metadata denial flags). This does not prove no socket-mediated writes, future launches, remote effects, or detached descendants. Zombies cannot run new work. Shell state S and Codex state S do not prove application idleness.

## Supported ownership association

Semble located harness/tmux.py:13–33,36–65: default managed adapter socket is hermes-workstreams; ownership requires exact HERMES_WORKSTREAM_RUN session environment matching an existing run, plus recorded pane selection at73–90. These default-socket sessions are therefore NOT positively associated with a supported Hermes run by the inspected metadata. A configurable alternate socket is possible, so socket mismatch alone is not proof they are unmanaged. No native session/checkpoint mapping was fabricated. The actual five pane IDs are in the metadata artifact; parent can corroborate existing recorded identity through an independently authorized bounded checkpoint lookup if needed. Do not call launch_state: its implementation105 onward reads argv/environment, outside this task's bounds.

## Finite writer exclusion recommendation

These observations do not justify killing or interrupting any default-socket session, nor treating all same-UID processes as actual writers. Scope the deployment writer inventory to identified entrypoints and positively associated principals: admind service/native helper, verified private admind agent, identified direct CLI/install sessions, WSD/hooks and actual timers/controllers. Obtain the operator's explicit deployment-window no-start agreement for those interactive principals; root must ensure its own current session makes no concurrent admin writer call. The three shell-only sessions need no invented stop command solely because they exist. The two live Codex processes require identity attribution if they are in the actual writer set; no message/control was sent here.

The inspected Hermes mechanism provides run ownership checks, not a global admind writer exclusion lock for arbitrary default-socket shells. approval_wake/migration.py:90–108 validates explicitly inventoried stopped/disabled units and absent process identities; it is not a general command that pauses these sessions. A point-in-time FD scan is corroboration, never an exclusive lease. No already-installed universal gate excluding future same-UID direct DB/CLI writers was established by this focused investigation. Do not invent such a claim or demand arbitrary unrelated session destruction. If actual writer attribution remains unknown, hold that specific unresolved writer/entrypoint rather than declaring all hypothetical same-UID capabilities safe.

Relevant source discovery used Semble; source symbols and line references are observations of canonical harness and current product source, not new installation/runtime claims. The separately documented own-private-admin stop route is unchanged. Incorporated deployment design was not edited.
