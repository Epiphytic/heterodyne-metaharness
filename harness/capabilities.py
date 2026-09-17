"""Deterministic provider context/search policy; no model calls or global writes."""
import json
import os
from pathlib import Path
import shutil
import tomllib


MAX_COMPACT_TOKENS = 500_000
# Unknown/custom models must not inherit an assumed million-token window.
UNKNOWN_COMPACT_TOKENS = 32_000
CLAUDE_SEARCH_TOOLS = ['WebSearch', 'WebFetch', 'Read', 'Grep', 'Glob',
                       'mcp__semble__search', 'mcp__semble__find_related',
                       'Bash(semble search *)', 'Bash(semble find-related *)']


def _read(path, loader):
    try:
        return loader(path.read_text())
    except FileNotFoundError:
        return {}


def _options(args, names):
    values = []
    for index, argument in enumerate(args):
        if argument in names and index + 1 < len(args):
            values.append(args[index + 1])
        elif any(argument.startswith(name + '=') for name in names):
            values.append(argument.split('=', 1)[1])
    return values


def _codex_config(run, config, args):
    home = Path(config.get('env', {}).get('CODEX_HOME',
                os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))))
    settings = _read(home / 'config.toml', tomllib.loads)
    # Project configuration can select a different model. Only inspect files;
    # this neither executes project content nor grants trust to it.
    workdir = Path(run.get('workdir', os.getcwd())).resolve()
    for directory in reversed([workdir, *workdir.parents]):
        settings.update(_read(directory / '.codex' / 'config.toml', tomllib.loads))
    profiles = _options(args, ('-p', '--profile'))
    if profiles:
        profile = profiles[-1]
        if Path(profile).name != profile:
            raise ValueError('invalid Codex profile name')
        settings.update(settings.get('profiles', {}).get(profile, {}))
        settings.update(_read(home / (profile + '.config.toml'), tomllib.loads))
    for override in _options(args, ('-c', '--config')):
        if '=' in override:
            key, value = override.split('=', 1)
            if key in ('model', 'model_context_window', 'model_catalog_json'):
                try:
                    settings[key] = tomllib.loads('value=' + value)['value']
                except tomllib.TOMLDecodeError:
                    settings[key] = value
    models = _options(args, ('-m', '--model'))
    if models:
        settings['model'] = models[-1]
    return home, settings


def codex_context(run, config, args):
    home, settings = _codex_config(run, config, args)
    catalog_path = Path(settings.get('model_catalog_json', home / 'models_cache.json'))
    catalog = _read(catalog_path, json.loads)
    model = settings.get('model')
    models = catalog.get('models', [])
    if not model:
        default = next((item for item in models if item.get('is_default')), {})
        model = default.get('slug')
    native = next((item.get('context_window') for item in models
                   if item.get('slug') == model), None)
    configured = settings.get('model_context_window')
    windows = [n for n in (native, configured) if type(n) is int and n > 0]
    window = min(windows) if windows else None
    ceiling = MAX_COMPACT_TOKENS if type(native) is int and native > 0 else UNKNOWN_COMPACT_TOKENS
    limit = min(window // 2, ceiling) if window else UNKNOWN_COMPACT_TOKENS
    return {'model': model, 'native_context_window': native,
            'effective_context_window': window, 'compact_token_limit': max(1, limit),
            'context_source': 'model catalog/config' if window else 'unknown: conservative fallback'}


def _replace_codex_policy(args, limit):
    result = []
    index = 0
    while index < len(args):
        arg = args[index]
        value = None
        width = 1
        if arg in ('-c', '--config') and index + 1 < len(args):
            value, width = args[index + 1], 2
        elif arg.startswith('--config='):
            value = arg.split('=', 1)[1]
        if value and value.split('=', 1)[0] in ('model_auto_compact_token_limit', 'web_search'):
            index += width
            continue
        result.extend(args[index:index + width])
        index += width
    return result + ['-c', f'model_auto_compact_token_limit={limit}', '-c', 'web_search="live"']


def _claude_search_args(args):
    # Merge existing grants instead of replacing them with a smaller tool set.
    grants = list(CLAUDE_SEARCH_TOOLS)
    result = []
    index = 0
    while index < len(args):
        if args[index] in ('--allowedTools', '--allowed-tools'):
            index += 1
            while index < len(args) and not args[index].startswith('-'):
                grants.extend(args[index].split(','))
                index += 1
        else:
            result.append(args[index])
            index += 1
    return result + ['--allowedTools', ','.join(dict.fromkeys(grants))]


def apply_capabilities(run, config):
    """Apply idempotently to a provider's launch config and return diagnostics.

    Existing deny rules and sandbox policies retain authority. Diagnostic tool
    presence is availability, not a claim of authenticated successful searches.
    """
    agent = run.get('agent')
    if agent not in ('codex', 'claude', 'claude-code'):
        return {'provider': agent, 'policy_applied': False}
    args = config.get('extra_args', config.get('args', []))
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError('config.extra_args must contain strings')
    result = {'provider': agent, 'policy_applied': True,
              'code_search': {name: shutil.which(name) for name in ('semble', 'ripwire', 'rg')},
              'web_search': 'live' if agent == 'codex' else 'WebSearch/WebFetch'}
    if agent == 'codex':
        result.update(codex_context(run, config, args))
        config['extra_args'] = _replace_codex_policy(args, result['compact_token_limit'])
    else:
        config.setdefault('env', {}).update(CLAUDE_AUTOCOMPACT_PCT_OVERRIDE='50',
                                            CLAUDE_CODE_AUTO_COMPACT_WINDOW='1000000')
        config['extra_args'] = _claude_search_args(args)
        result.update(compact_percent=50, compact_window_ceiling=1_000_000,
                      compact_token_ceiling=MAX_COMPACT_TOKENS)
    return result
