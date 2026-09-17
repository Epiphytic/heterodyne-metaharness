#!/usr/bin/env python3
"""Build exact reviewed context cleanup manifest; contract: spec/context.md."""
import argparse
import hashlib
import json
from pathlib import Path


def prepare(home):
    files=[]
    def add(path, replacement=None):
        if path.is_file():
            entry={'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'mode':path.stat().st_mode & 0o777}
            if replacement is not None: entry['replacement']=replacement
            files.append(entry)
    base=home/'skills/autonomous-ai-agents/coding-delegation'
    for name in ('references/workstream-lifecycle.md','references/tmux-progress-relay.md','SKILL.md.pre-durable-harness'):
        add(base/name)
    for name in ('dream-cycle','memory-manager','ruvector-memory','memory-tiering','memory-audit','moltguard'):
        for path in (home/'skills/openclaw-imports'/name).rglob('*'):
            if path.is_file(): add(path)
    memory=home/'memories/MEMORY.md'
    text=memory.read_text()
    text=text.replace(' Always reply English.', '').replace(' NEVER reply in Chinese (or any language other than English) — violated 2026-09-09, Liam called it out.', '')
    text=text.replace(' Always-on Marmot activation for chat 8f240eee… needs gateway restart (config listed, gateway predated it).', '')
    text=text.replace('session-sync plugin (2026-09-09, ~/.hermes/plugins/session-sync/): pre_llm_call hook', 'session-sync plugin (~/.hermes/plugins/session-sync/): installed but DISABLED as verified 2026-09-16; not in plugins.enabled. The following describes its implementation, not active context synchronization: pre_llm_call hook')
    text=text.replace('## Workstream: memory-lance (2026-09-09, in progress)', '## Historical workstream: memory-lance (2026-09-09 snapshot)')
    text=text.replace('in flight with codex in tmux ws-memory-lance.', 'were pending at this historical snapshot; the legacy ws-memory-lance harness is archived. Consult Beads and deployed artifacts for current milestone status.')
    pointer='Hermes maintenance: significant changes use existing hermes-maintenance Codex/tmux via workstream maintenance. Active authority: /home/operator/repos/hermes-workstream-harness/spec/README.md. Beads owns current task status.'
    if pointer not in text: text=text.rstrip()+'\n§\n'+pointer+'\n'
    if text!=memory.read_text(): add(memory,text)
    return {'version':1,'files':files,'notes':'Quiesce memory/skill writers during apply. USER preference remains canonical. No transcript or cron mutation in this manifest.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--home',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(prepare(a.home),indent=2));a.output.chmod(0o600)
