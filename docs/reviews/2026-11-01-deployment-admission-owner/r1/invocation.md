# Actual invocation

Original JSON SHA256: ff426262e7b67b8c33c23353fa8e10a8b59de2d9c9770485378983dadfc4bb48. The unchanged raw JSON is retained with the controller execution record. The following rendering adds only explicit evidence annotations to lines containing host paths.

```text
{
  "head": "f07838be05eea548d4a4e3cd1f5907fd37abc5e6",
  "argv": [
    "/home/openclaw/.nvm/versions/node/v22.22.0/bin/claude", # install-agnostic: allow=home-path — exact historical review invocation evidence, not runtime configuration
    "-p",
    "--settings",
    "{\"disableAllHooks\":true}",
    "--model",
    "opus",
    "--effort",
    "high",
    "--output-format",
    "stream-json",
    "--verbose",
    "--dangerously-skip-permissions",
    "--tools",
    "Read,Bash,Skill,Agent,ReportFindings",
    "--strict-mcp-config",
    "--mcp-config",
    "{\"mcpServers\":{}}",
    "--add-dir",
    "/home/openclaw/repos/heterodyne-deployment-admission-owner-20261101", # install-agnostic: allow=home-path — exact historical review invocation evidence, not runtime configuration
    "--",
    "Use your built-in /code-review skill with explicit args \"high 157\" for Epiphytic/heterodyne-metaharness PR157. FIRST full HIGH code review of frozen source. Read-only, no comments, pushes, edits, service changes or tests. Scope: private root-owned admission journal and fixed no-effect entry using the already reviewed parent channel and pure binding. It atomically reserves one-use keys, refuses replay and records failed-no-effect only if no events have issued. No effect permit or actual deployment effect is implemented in this slice. Actual25 focused tests passed1.74seconds; Ruff/Pyright pass. Genuine native MLS consumed fixture crossed actual credentialled mapped-root Python channel, was durably recorded and two fresh process replays refused with zero issue events. Fixture process projection and clock scope are explicitly documented; it is not installed custody. Full quality exit2 duplication/complexity debt retained honestly. Review concrete security/correctness flaws within component scope, not missing future effect runner as merge prerequisite. Root journal path and peer must remain source-owned, no caller DTO authority. Retain actual model/skill args and findings. Actual commands/reports must be preserved."
  ],
  "started_at": "2026-11-01T22:03:15.122130+00:00"
}
```
