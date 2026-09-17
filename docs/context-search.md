# Context and search policy

`harness.capabilities.apply_capabilities(run, config)` applies the same policy on
initial launch and recovery. It changes only launch arguments and environment;
it does not modify global user settings, remove deny rules, or broaden sandbox
permissions. Calling it twice produces the same arguments.

## Compaction

Codex uses `model_auto_compact_token_limit = min(native_window / 2, 500000)`.
The selected model is resolved from local config, project config, named profile,
and explicit CLI overrides; its context window comes from the local model
catalog. A smaller explicit context setting reduces the threshold. Unknown
models use a conservative 32000-token fallback, reported as unknown, until a
catalog is available. This fallback cannot establish 50% for an unknown model;
custom small models should supply an accurate model catalog. No artificial
`model_context_window` is injected. Changing model interactively mid-session
requires relaunch to recalculate this harness policy.

On this installation, Codex 0.154.0 selects `gpt-6-astra` and its installed
`models_cache.json` advertises **272000 tokens**, yielding **136000 tokens**.
The policy does not assume that advertised million-token model capability is
actually enabled in this Codex installation.

Claude Code 2.1.201 supports `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=50` and
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=1000000`. The installed binary clamps the latter
to `min(native_window, configured_window)` before calculating thresholds. Thus
the effective target is half the native window, at most 500000 tokens. Setting
both 50% and a 500000-token window would instead compact at 250000. The prior
local window override was 400000. These are compaction triggers, not an exact
maximum for a single oversized tool result or model turn.

Native documentation:

- [Codex configuration](https://developers.openai.com/codex/config-reference/)
- [Claude Code settings](https://code.claude.com/docs/en/settings)

## Searches

Codex receives `web_search="live"`. Claude receives narrowly scoped permissions
for WebSearch, WebFetch, local read/search tools, and Semble's two MCP tools and
search CLI subcommands. Existing allowed tools are merged; existing denies and
permission modes stay intact. Administrative or project restrictions can still
block tools and must not be bypassed silently.

Both agents can use Semble for implementation discovery and Ripwire for
structural inspection; `rg` remains available for all-occurrence literal
queries. Diagnostics report locally resolved executables without invoking an
API or spending model tokens. Executable presence does not prove remote
authentication, network access, or that a tool has been used successfully.

The inspected baseline had Semble and Ripwire executables on PATH but no Codex
MCP servers and no Claude Semble MCP integration. The global setup command is
`semble install --agent codex claude --type mcp -y`; running the command again
should update its own server entry, not replace unrelated servers. Search
availability should be verified by an MCP initialize/tools-list and a bounded
query against a fixture or known repository. A real Semble CLI query against
this repository successfully located the provider launch contract during this
implementation.

Global settings for direct (non-harness) invocations should mirror the current
resolved Codex threshold and live web search, and Claude's two environment
settings. Existing processes retain their old configuration until resumed or
restarted; do not terminate active coding work just to apply settings.

## Installation verification

`python3 install_capabilities.py` installed global settings and Semble MCP for
both providers. A second run produced byte-identical configuration files.
Backups use `.pre-context-search`; writes preserve unrelated settings and use
private file permissions. Existing hook configuration, trust entries, and
Claude permission mode/denials were retained.

Both providers now point at the same pinned `semble[mcp]==0.5.6` stdio server.
A real MCP initialize and tools/list returned `search` and `find_related`; a
real bounded search returned `calculate_total` in a temporary Python fixture
without a model/API call. Local unit coverage verifies config preservation,
model/profile/project selection, threshold capping, and repeat installation.
Web search is configured; no paid model turn was started to exercise it.

Direct Codex launches use the globally resolved 136000-token threshold until
this installer runs again. Harness launches recalculate for each selected model.
The auto-compaction setting is therefore not dynamically recomputed if the
user switches models interactively inside an already-running Codex session.
