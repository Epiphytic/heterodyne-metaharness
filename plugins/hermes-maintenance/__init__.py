"""Hermes workflow guard. Contract: spec/maintenance.md. Not a sandbox."""
import os
from pathlib import Path
import shlex


def pre_tool_call(tool_name='', args=None, **kwargs):
    args = args or {}
    home = Path(os.environ.get('HERMES_HOME', '~/.hermes')).expanduser().resolve()
    roots = [home / 'hermes-agent', home / 'plugins', home / 'scripts', home / 'skills',
             Path('/home/operator/repos/hermes-workstream-harness')]
    cwd = Path(args.get('workdir') or args.get('cwd') or os.getcwd()).expanduser().resolve()
    def protected(value):
        path = Path(value).expanduser()
        path = (cwd / path).resolve() if not path.is_absolute() else path.resolve()
        return any(path.is_relative_to(root) for root in roots)
    blocked = False
    if tool_name in ('write_file', 'patch', 'apply_patch', 'edit_file'):
        paths = [args[key] for key in ('path', 'file_path', 'target') if isinstance(args.get(key), str)]
        blocked = any(protected(path) for path in paths)
        patch = args.get('patch', args.get('input', ''))
        if isinstance(patch, str):
            for line in patch.splitlines():
                for marker in ('*** Update File: ', '*** Add File: ', '*** Delete File: '):
                    if line.startswith(marker): blocked |= protected(line[len(marker):])
    elif tool_name == 'terminal':
        # Narrow lexical guard: only explicit mutation tools/redirects and known
        # protected operands/cwd. It cannot interpret arbitrary programs or aliases.
        command = args.get('command', '')
        try:
            words = shlex.split(command)
        except ValueError:
            words = []
        readonly = (len(words) >= 2 and ((words[0] == 'git' and words[1] in ('status', 'diff', 'show', 'log', 'rev-parse')) or (words[0] == 'sed' and words[1] == '-n'))) and not any(token in command for token in (';', '|', '&&', '>', '$(', '`'))
        mutation = not readonly and (any(word in ('apply_patch', 'sed', 'perl', 'tee', 'cp', 'mv', 'rm', 'install', 'git') for word in words) or '>' in command)
        blocked = mutation and (protected(str(cwd)) or any(protected(word) for word in words if word.startswith(('/', './', '../', '~'))))
    if blocked:
        return {'action': 'block', 'message': 'Significant Hermes changes belong to the existing Codex/tmux worker. Use workstream maintenance --title TITLE --file TASK --key REQUEST_ID. See /home/operator/repos/hermes-workstream-harness/spec/maintenance.md. Native approvals remain required.'}
    return None


def register(ctx):
    ctx.register_hook('pre_tool_call', pre_tool_call)
