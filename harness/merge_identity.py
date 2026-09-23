"""Read-only rewritten-merge identity check. Authority: spec/changes.md."""
import json
import re
import subprocess

from .beads import BeadsError


def verify_github(repo, evidence, submitted):
    url = submitted.get('pr_url', '')
    if (not isinstance(url, str)
            or not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*', url)
            or evidence.get('review_ref') != url):
        raise BeadsError('Rewritten merge requires exact matching GitHub PR and review URLs')
    try:
        result = subprocess.run(
            ['gh', 'pr', 'view', url, '--json', 'url,state,headRefOid,mergeCommit,commits'],
            cwd=repo, check=True, capture_output=True, text=True, timeout=30)
        record = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        # Do not relay CLI diagnostics: authentication errors can include secrets.
        raise BeadsError('GitHub merge verification unavailable; retain task and retry after reconciliation') from exc
    if not isinstance(record, dict):
        raise BeadsError('GitHub PR response is not an object')
    merge = record.get('mergeCommit')
    expected = {'url': url, 'state': 'MERGED'}
    commits = record.get('commits', [])
    member = isinstance(commits, list) and any(
        isinstance(item, dict) and item.get('oid') == submitted['commit'] for item in commits)
    if record.get('headRefOid') != submitted['commit'] and not member:
        raise BeadsError('Recorded PR commit is absent from the merged PR history')
    if (any(record.get(key) != value for key, value in expected.items())
            or not isinstance(merge, dict) or merge.get('oid') != evidence['commit']):
        raise BeadsError('GitHub PR does not bind the submitted head to the exact merged commit')
