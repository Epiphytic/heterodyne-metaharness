# The OpenShell sandbox backend

wsd runs each agent session in an OpenShell sandbox when the host config says
`[platform] sandbox = "openshell"` (ADR 0001 §7). `heterodyne setup` still records `bubblewrap` on
Linux, which runs no agents: the operator enables the runtime by editing that one value in the host
`config.toml` and restarting wsd. Wait for crash-loop accounting first (below). This needs, once per host:

1. OpenShell 0.1.2 and rootless podman 5 (the podman compute driver needs podman 5). On a host whose
   system podman is older, install podman 5 side by side and give wsd its `PATH` and `CONTAINERS_CONF`
   through `[sandbox] tool_env`.
2. The gateway on the podman driver: copy `gateway.toml.example` to `~/.config/openshell/gateway.toml`,
   fill in the podman user socket, and restart the gateway's user unit.
3. The workload image, built with your uid and gid:

       podman build --build-arg AGENT_UID="$(id -u)" --build-arg AGENT_GID="$(id -g)" \
         -t localhost/heterodyne-agent:1 packaging/sandbox

   and `[sandbox] image = "localhost/heterodyne-agent:1"` in the host config.
4. The pinned agent CLIs installed on the host (`[adapters.<name>] binary`), with a default login each.
5. `kernel.yama.ptrace_scope` at 2 or more (probe protection, §7). With a lower value every launch
   fails closed and names the setting.

Every launch runs the §7 self-test before the agent does any work; a failed check refuses the launch
and names the check. `docs/wsd.md` describes what the runtime does.

**Wait for crash-loop accounting before enabling it.** wsd has no persistent crash-loop accounting yet
(plan 3's P1, btq-g08sd): each relaunch after a session that died opens a fresh launch-failure budget,
so a session that dies on every launch is relaunched without limit. Until btq-g08sd lands, setup keeps
recording `bubblewrap`, and a host enabled by hand must be watched.
