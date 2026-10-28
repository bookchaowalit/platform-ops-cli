#!/usr/bin/env python3
"""Refresh explicitly configured organization and personal hub projections using GitHub CLI.

Preview is read-only. --check exits 1 on drift; --apply writes and commits scoped
projections; --apply --publish also pushes. --clone-missing clones hubs that have no
checkout yet (hosted runs start empty). Blockers exit 2. No credentials or project
contents are copied into reports or the umbrella index.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
import fcntl
from html import escape
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo


SCHEMA = 'bookchaowalit.org-hub-sync.v1'
SLUG = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*\Z')
REPOSITORY_SLUG = re.compile(r'\.?[A-Za-z0-9][A-Za-z0-9_.-]*\Z')  # GitHub allows e.g. owner/.github.
ORGANIZATION_SCOPE = 'All organization repositories except this hub, including archived repositories.'
INVENTORY_SCHEMA = 'bookchaowalit.org-hub.v1'
INDEX_SCHEMA = 'bookchaowalit.empire-hubs.v1'
CLONE_URL = 'https://github.com/{}.git'


class SyncError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _command(args: list[str], code: str = 'command_failed') -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=60,
                              env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GH_PROMPT_DISABLED': '1'})
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyncError(code) from exc
    if proc.returncode:
        raise SyncError(code)
    return proc.stdout.strip()


def _git(root: Path, *args: str) -> str:
    return _command(['git', '-C', str(root), *args], 'git_command_failed')


class GitHub:
    def get(self, endpoint: str) -> dict:
        try:
            result = json.loads(_command(['gh', 'api', endpoint], 'github_read_failed'))
        except ValueError as exc:
            raise SyncError('invalid_github_json') from exc
        if not isinstance(result, dict):
            raise SyncError('invalid_github_json')
        return result

    def pages(self, endpoint: str) -> list[dict]:
        try:
            pages = json.loads(_command(['gh', 'api', '--paginate', '--slurp', endpoint], 'github_read_failed'))
        except ValueError as exc:
            raise SyncError('invalid_github_json') from exc
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise SyncError('invalid_github_pagination')
        return [item for page in pages for item in page]


def _name(value: object) -> str:
    parts = value.split('/') if isinstance(value, str) else []
    if len(parts) != 2 or not SLUG.fullmatch(parts[0]) or not REPOSITORY_SLUG.fullmatch(parts[1]):
        raise SyncError('invalid_repository_name')
    return value


def personal_scope(owner: str) -> str:
    return (f'All repositories owned by the personal account {owner} except this hub, including archived '
            'repositories. Organization repositories are listed in their own hubs.')


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise SyncError('invalid_registry_json') from exc
    if not isinstance(value, dict):
        raise SyncError('invalid_registry_json')
    return value


def json_text(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def normalize_repositories(organization: str, hub_repository: str, records: list[dict]) -> list[dict]:
    if not isinstance(records, list):
        raise SyncError('invalid_repository_metadata')
    result, seen = [], set()
    for row in records:
        if not isinstance(row, dict):
            raise SyncError('invalid_repository_metadata')
        name = _name(row.get('full_name'))
        if (name.split('/')[0] != organization or not isinstance(row.get('owner'), dict)
                or row['owner'].get('login') != organization):
            raise SyncError('repository_owner_mismatch')
        if name.casefold() in seen:
            raise SyncError('duplicate_repository')
        seen.add(name.casefold())
        if row.get('html_url') != 'https://github.com/' + name:
            raise SyncError('invalid_repository_url')
        branch = row.get('default_branch')
        if ('default_branch' not in row or (branch is not None and
            (not isinstance(branch, str) or not branch or any(c in branch for c in '\n\r\x00')))
            or row.get('visibility') not in {'private', 'public', 'internal'}
            or type(row.get('archived')) is not bool or type(row.get('fork')) is not bool):
            raise SyncError('invalid_repository_metadata')
        if name != hub_repository:
            result.append({'full_name': name, 'url': row['html_url'], 'default_branch': branch,
                           'visibility': row['visibility'], 'archived': row['archived'], 'fork': row['fork']})
    return sorted(result, key=lambda row: row['full_name'].casefold())


def build_inventory(existing: dict | None, entry: dict, records: list[dict], parent_url: str, today: str,
                    scope: str = ORGANIZATION_SCOPE) -> dict:
    date.fromisoformat(today)
    if existing is not None and (existing.get('schema_version') != INVENTORY_SCHEMA
            or existing.get('organization') != entry['organization']
            or existing.get('hub_repository') != entry['repository']):
        raise SyncError('inventory_identity_mismatch')
    result = {'schema_version': INVENTORY_SCHEMA, 'snapshot_date': today,
              'source': 'GitHub REST API', 'organization': entry['organization'],
              'hub_repository': entry['repository'], 'parent_reference': parent_url,
              'inventory_scope': scope,
              'repositories': normalize_repositories(entry['organization'], entry['repository'], records)}
    if existing is not None:
        old_date = existing.get('snapshot_date')
        try:
            date.fromisoformat(old_date)
        except (TypeError, ValueError) as exc:
            raise SyncError('invalid_snapshot_date') from exc
        comparison = {**result, 'snapshot_date': old_date}
        if comparison == existing:
            return comparison
    return result


def replace_region(document: str, marker: str, generated: str) -> str:
    begin, end = f'<!-- BEGIN {marker} -->', f'<!-- END {marker} -->'
    if document.count(begin) != 1 or document.count(end) != 1 or document.index(begin) >= document.index(end):
        raise SyncError('missing_generated_region')
    prefix, remainder = document.split(begin)
    _, suffix = remainder.split(end)
    return prefix + begin + '\n' + generated.rstrip() + '\n' + end + suffix


def _cell(text: object) -> str:
    return escape(str(text), quote=False).replace('|', '&#124;').replace('\n', ' ')


def render_inventory(inventory: dict) -> str:
    records = inventory['repositories']
    active = [r for r in records if not r['archived']]
    archived = [r for r in records if r['archived']]
    lines = [f"สำรวจเมื่อ {inventory['snapshot_date']}: {len(active)} repo ที่ไม่ archived และ {len(archived)} repo ที่ archived",
             'จำนวนนี้ไม่นับ hub ตัวนี้ และเป็น snapshot ของทะเบียน',
             'ค่า default branch เป็น metadata จาก GitHub; repo ว่างอาจยังไม่มี commit', '']
    for title, rows in [('Repositories ที่ไม่ archived', active), ('Archived repositories', archived)]:
        lines += ['## ' + title, '']
        if not rows:
            lines += ['ยังไม่มี repository ในกลุ่มนี้ ณ วันที่สำรวจ', '']
            continue
        lines += ['| Repository | Default branch | Visibility |', '|---|---|---|']
        for row in rows:
            literal = ''.join(f'&#{ord(c)};' if c in '\\`*_[]()~' else c
                              for c in _cell(row['default_branch'])) if row['default_branch'] else ''
            branch = '<code>' + literal + '</code>' if literal else '—'
            lines.append(f"| [{row['full_name'].split('/')[1]}]({row['url']}) | {branch} | {row['visibility']} |")
        lines.append('')
    return '\n'.join(lines).rstrip()


def render_hubs(index: dict) -> str:
    personal = index['personal_hub']
    lines = ['## Personal hub', '', '| Owner | Hub | หน้าที่ |', '|---|---|---|',
             f"| `{personal['owner']}` | [{personal['checkout_name']}]({personal['url']}) | {_cell(personal['purpose'])} |",
             '', '## Organization hubs', '', '| Organization | Hub | ขอบเขต |', '|---|---|---|']
    for row in index['organizations']:
        lines.append(f"| `{row['organization']}` | [{row['checkout_name']}]({row['url']}) | {_cell(row['purpose'])} |")
    lines += ['', 'ทะเบียนแบบอ่านด้วยโปรแกรม: [hubs.json](registry/hubs.json)',
              f"ข้อมูลอัปเดตเมื่อ {index['updated']}: 1 personal hub และ {len(index['organizations'])} organization hubs",
              'ขอบเขตนี้รวม Org ที่เป็นเจ้าของ; Org ที่เป็นเพียงสมาชิกอยู่นอกทะเบียนนี้']
    return '\n'.join(lines)


def _safe_file(root: Path, target: Path) -> None:
    if not target.is_relative_to(root) or not target.resolve().is_relative_to(root.resolve()):
        raise SyncError('unsafe_projection_path')
    components = [target, *[p for p in target.parents if p.is_relative_to(root)]]
    if any(p.is_symlink() for p in components):
        raise SyncError('unsafe_projection_path')


def projection_paths(entry: dict, base: Path) -> tuple[Path, Path, Path]:
    name = entry.get('checkout_name')
    if not isinstance(name, str) or not SLUG.fullmatch(name) or name.startswith('.'):
        raise SyncError('unsafe_projection_path')
    base = base.resolve()
    if (base / name).is_symlink():
        raise SyncError('unsafe_projection_path')
    root = (base / name).resolve()
    if root.parent != base:
        raise SyncError('unsafe_projection_path')
    config = entry.get('sync')
    if not isinstance(config, dict):
        raise SyncError('missing_sync_configuration')
    paths = []
    for key in ['inventory_path', 'readme_path']:
        relative = config.get(key)
        if not isinstance(relative, str):
            raise SyncError('unsafe_projection_path')
        p = PurePosixPath(relative)
        if p.is_absolute() or '..' in p.parts or any(part.startswith('.') for part in p.parts):
            raise SyncError('unsafe_projection_path')
        if key == 'inventory_path':
            allowed = p.parent == PurePosixPath('repository-catalog/registries') and p.suffix == '.json'
        else:
            allowed = relative == 'README.md' or (p.parent == PurePosixPath('repository-catalog') and p.suffix == '.md')
        target = root / relative
        if not allowed:
            raise SyncError('unsafe_projection_path')
        _safe_file(root, target)
        paths.append(target)
    return root, paths[0], paths[1]


def validate_index(index: dict, base: Path) -> None:
    if index.get('schema_version') != INDEX_SCHEMA or not isinstance(index.get('organizations'), list):
        raise SyncError('invalid_hub_index')
    owner = _name(index.get('repository')).split('/')[0]
    personal = index.get('personal_hub')
    if not isinstance(personal, dict) or personal.get('owner') != owner:
        raise SyncError('invalid_hub_index')
    references = [personal, *index['organizations']]
    names, checkouts, orgs = set(), set(), set()
    for row in references:
        if not isinstance(row, dict):
            raise SyncError('invalid_hub_index')
        name = _name(row.get('repository'))
        checkout = row.get('checkout_name')
        if (name in names or checkout in checkouts or not isinstance(checkout, str)
                or not SLUG.fullmatch(checkout) or checkout.startswith('.')
                or row.get('url') != 'https://github.com/' + name
                or not isinstance(row.get('purpose'), str)):
            raise SyncError('invalid_hub_index')
        names.add(name)
        checkouts.add(checkout)
        if row is personal:
            if name.split('/')[0] != owner:
                raise SyncError('invalid_hub_index')
            if 'sync' in row:
                projection_paths(row, base)
        else:
            org = row.get('organization')
            if org in orgs or org != name.split('/')[0] or not org.startswith(owner + '-'):
                raise SyncError('invalid_hub_index')
            orgs.add(org)
            projection_paths(row, base)


def prepare_checkout(root: Path, repository: str, branch: str, remote_sha: str, *, apply: bool) -> None:
    if not root.is_dir() or Path(_git(root, 'rev-parse', '--show-toplevel')).resolve() != root.resolve():
        raise SyncError('invalid_git_boundary')
    if _git(root, 'status', '--porcelain'):
        raise SyncError('dirty_checkout')
    remote = _git(root, 'remote', 'get-url', 'origin')
    if remote.startswith('git@github.com:'):
        remote = 'https://github.com/' + remote.split(':', 1)[1]
    url = urlsplit(remote)
    if url.hostname != 'github.com' or url.path.removesuffix('.git').lstrip('/') != repository:
        raise SyncError('unexpected_remote')
    if _git(root, 'branch', '--show-current') != branch:
        raise SyncError('unexpected_branch')
    if _git(root, 'rev-parse', '--abbrev-ref', '@{upstream}') != 'origin/' + branch:
        raise SyncError('unexpected_upstream')
    if _git(root, 'rev-parse', 'HEAD') != remote_sha:
        if not apply:
            raise SyncError('stale_or_unpushed_checkout')
        _git(root, 'fetch', '--quiet', 'origin', branch)
        if _git(root, 'rev-parse', 'FETCH_HEAD') != remote_sha:
            raise SyncError('remote_changed_during_run')
        try:
            _git(root, 'merge-base', '--is-ancestor', 'HEAD', 'FETCH_HEAD')
        except SyncError as exc:
            raise SyncError('ahead_or_diverged_checkout') from exc
        _git(root, 'merge', '--quiet', '--ff-only', 'FETCH_HEAD')


def clone_checkout(root: Path, repository: str, branch: str) -> None:
    """Clone a missing hub; an existing path is left for prepare_checkout to verify."""
    if root.exists() or root.is_symlink():
        return
    _command(['git', 'clone', '--quiet', '--single-branch', '--branch', branch, '--',
              CLONE_URL.format(repository), str(root)], 'git_clone_failed')


def _atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def commit_projection(root: Path, contents: dict[str, str], *, branch: str, publish: bool) -> dict:
    if _git(root, 'status', '--porcelain'):
        raise SyncError('dirty_checkout')
    if _git(root, 'branch', '--show-current') != branch:
        raise SyncError('unexpected_branch')
    for name in contents:
        p = PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or any(part.startswith('.') for part in p.parts) or not (root / name).resolve().is_relative_to(root.resolve()):
            raise SyncError('unsafe_projection_path')
        _safe_file(root, root / name)
    changes = {name: text.encode('utf-8') for name, text in contents.items()
               if not (root / name).is_file() or (root / name).read_bytes() != text.encode('utf-8')}
    if not changes:
        return {'status': 'unchanged'}
    originals = {name: (root / name).read_bytes() if (root / name).exists() else None for name in changes}
    committed = False
    try:
        for name, content in changes.items():
            _atomic(root / name, content)
        _git(root, 'add', '--', *changes)
        if set(_git(root, 'diff', '--cached', '--name-only').splitlines()) != set(changes):
            raise SyncError('unexpected_staged_paths')
        _git(root, 'diff', '--cached', '--check')
        _git(root, 'commit', '--quiet', '-m', 'chore(registry): sync GitHub repository metadata')
        committed = True
    except (OSError, SyncError) as exc:
        if not committed:
            _git(root, 'restore', '--staged', '--', *changes)
            for name, original in originals.items():
                if original is None:
                    (root / name).unlink(missing_ok=True)
                else:
                    _atomic(root / name, original)
        raise SyncError('git_commit_failed') from exc
    head = _git(root, 'rev-parse', 'HEAD')
    if publish:
        try:
            _git(root, 'push', '--quiet', 'origin', 'HEAD:refs/heads/' + branch)
        except SyncError as exc:
            raise SyncError('git_push_failed') from exc
    return {'status': 'updated', 'commit': head, 'published': publish, 'changed_files': list(changes)}


def _metadata(client: GitHub, reference: dict) -> dict:
    name = reference['repository']
    meta = client.get('repos/' + name)
    if (meta.get('full_name') != name or meta.get('html_url') != 'https://github.com/' + name
            or meta.get('visibility') not in {'private', 'public', 'internal'}
            or not isinstance(meta.get('default_branch'), str)):
        raise SyncError('hub_identity_changed')
    return meta


def _revision(client: GitHub, repository: str, branch: str) -> str:
    result = client.get('repos/' + repository + '/git/ref/heads/' + quote(branch, safe=''))
    sha = result.get('object', {}).get('sha')
    if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{40,64}', sha):
        raise SyncError('invalid_remote_revision')
    return sha


def synchronize(index_path: Path, base: Path, *, apply: bool, publish: bool,
                client: GitHub | None = None, today: str | None = None, clone: bool = False) -> dict:
    client = client or GitHub()
    today = today or datetime.now(ZoneInfo('Asia/Bangkok')).date().isoformat()
    index_path, base = index_path.resolve(), base.resolve()
    root = index_path.parent.parent
    if index_path != root / 'registry/hubs.json':
        raise SyncError('invalid_index_path')
    _safe_file(root, index_path)
    _safe_file(root, root / 'README.md')
    index = _json(index_path)
    validate_index(index, base)
    umbrella = index['repository']
    root_meta = _metadata(client, {'repository': umbrella})
    if root_meta['visibility'] != 'private':
        raise SyncError('umbrella_must_be_private')
    root_sha = _revision(client, umbrella, root_meta['default_branch'])
    prepare_checkout(root, umbrella, root_meta['default_branch'], root_sha, apply=apply)
    _safe_file(root, index_path)
    _safe_file(root, root / 'README.md')
    index = _json(index_path)  # A clean fast-forward may update target configuration.
    validate_index(index, base)
    if index['repository'] != umbrella:
        raise SyncError('hub_identity_changed')
    if client.get('user').get('login') != umbrella.split('/')[0]:
        raise SyncError('unexpected_github_account')
    memberships = client.pages('user/memberships/orgs?per_page=100')
    owner = umbrella.split('/')[0]
    owned = {m['organization']['login'] for m in memberships
             if m.get('state') == 'active' and m.get('role') == 'admin'
             and m.get('organization', {}).get('login', '').startswith(owner + '-')}
    configured = {row['organization'] for row in index['organizations']}
    if configured - owned:
        raise SyncError('organization_access_changed')
    if owned - configured:
        raise SyncError('unregistered_owned_organizations')

    def fetch(entry: dict) -> tuple[dict, str, list[dict]]:
        meta = _metadata(client, entry)
        revision = _revision(client, entry['repository'], meta['default_branch'])
        rows = client.pages(f"orgs/{entry['organization']}/repos?per_page=100&type=all")
        normalize_repositories(entry['organization'], entry['repository'], rows)
        if entry['repository'] not in {row['full_name'] for row in rows}:
            raise SyncError('hub_missing_from_organization')
        return meta, revision, rows

    personal = index['personal_hub']
    personal_meta = _metadata(client, personal)
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(fetch, index['organizations']))
    hubs = [(entry, ORGANIZATION_SCOPE, *snapshot) for entry, snapshot in zip(index['organizations'], snapshots)]
    if 'sync' in personal:
        # /user/repos also covers private repos; affiliation=owner can still include
        # repos of other owners in edge cases, so keep only the personal account's own.
        rows = [row for row in client.pages('user/repos?per_page=100&affiliation=owner')
                if isinstance(row, dict) and isinstance(row.get('owner'), dict)
                and row['owner'].get('login') == owner]
        normalize_repositories(owner, personal['repository'], rows)
        if personal['repository'] not in {row['full_name'] for row in rows}:
            raise SyncError('hub_missing_from_account')
        revision = _revision(client, personal['repository'], personal_meta['default_branch'])
        hubs.append(({**personal, 'organization': owner}, personal_scope(owner), personal_meta, revision, rows))
    updated_index = deepcopy(index)
    updated_index['personal_hub']['visibility'] = personal_meta['visibility']
    for entry, (meta, _, _) in zip(updated_index['organizations'], snapshots):
        entry['visibility'] = meta['visibility']
    if updated_index != index:
        updated_index['updated'] = today
    parent = 'https://github.com/' + umbrella
    targets = []
    root_readme = replace_region((root / 'README.md').read_text(), 'EMPIRE HUBS', render_hubs(updated_index))
    targets.append((umbrella, root, root_meta, root_sha,
                    {'registry/hubs.json': json_text(updated_index), 'README.md': root_readme}))
    results = []
    for entry, scope, meta, revision, rows in hubs:
        try:
            hub_root, inv_path, readme_path = projection_paths(entry, base)
            if meta['visibility'] != 'private' or meta.get('archived'):
                raise SyncError('hub_not_writable_private')
            if clone:
                clone_checkout(hub_root, entry['repository'], meta['default_branch'])
            prepare_checkout(hub_root, entry['repository'], meta['default_branch'], revision, apply=apply)
            hub_root, inv_path, readme_path = projection_paths(entry, base)
            existing = _json(inv_path) if inv_path.exists() else None
            inventory = build_inventory(existing, entry, rows, parent, today, scope)
            readme = replace_region(readme_path.read_text(), 'ORG-HUB INVENTORY', render_inventory(inventory))
            targets.append((entry['repository'], hub_root, meta, revision,
                            {str(inv_path.relative_to(hub_root)): json_text(inventory),
                             str(readme_path.relative_to(hub_root)): readme}))
        except (SyncError, OSError) as exc:
            results.append({'repository': entry['repository'], 'status': 'blocked',
                            'code': exc.code if isinstance(exc, SyncError) else 'projection_file_unavailable'})
    for name, path, meta, revision, contents in targets:
        try:
            changed = [file for file, text in contents.items() if not (path / file).is_file() or (path / file).read_text() != text]
            if not apply:
                result = {'status': 'drift' if changed else 'unchanged', 'changed_files': changed}
            else:
                prepare_checkout(path, name, meta['default_branch'], revision, apply=False)
                result = commit_projection(path, contents, branch=meta['default_branch'], publish=publish)
            results.append({'repository': name, **result})
        except (SyncError, OSError) as exc:
            results.append({'repository': name, 'status': 'blocked',
                            'code': exc.code if isinstance(exc, SyncError) else 'projection_write_failed'})
    blocked = sum(row['status'] == 'blocked' for row in results)
    changed = sum(row['status'] in {'drift', 'updated'} for row in results)
    return {'schema': SCHEMA, 'status': 'blocked' if blocked else ('updated' if apply else 'drift') if changed else 'unchanged',
            'checked_organization_hubs': len(index['organizations']), 'checked_personal_hubs': int('sync' in personal),
            'references_verified': len(snapshots) + 1,
            'inventory_records': sum(len(rows) - 1 for *_, rows in hubs),
            'changed_targets': changed, 'blocked_targets': blocked, 'results': results}


@contextmanager
def _writer_lock(root: Path):
    directory = root / '.runtime'
    if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
        raise SyncError('unsafe_runtime_path')
    directory.mkdir(exist_ok=True)
    path = directory / 'org-hub-sync.lock'
    if path.is_symlink():
        raise SyncError('unsafe_runtime_path')
    with path.open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SyncError('sync_already_running') from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--checkouts-root', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--clone-missing', action='store_true')
    args = parser.parse_args(argv)
    if (args.publish and not args.apply) or (args.check and args.apply):
        parser.error('--publish requires --apply; --check and --apply are mutually exclusive')
    root = args.index.resolve().parent.parent
    try:
        if args.apply:
            with _writer_lock(root):
                try:
                    report = synchronize(args.index, args.checkouts_root, apply=True, publish=args.publish,
                                         clone=args.clone_missing)
                except (SyncError, OSError, ValueError, KeyError, TypeError) as exc:
                    report = {'schema': SCHEMA, 'status': 'blocked', 'blocked_targets': 1,
                              'code': exc.code if isinstance(exc, SyncError) else 'invalid_input_or_environment'}
                report['checked_at'] = datetime.now(ZoneInfo('Asia/Bangkok')).isoformat()
                target = root / '.runtime/org-hub-sync.json'
                if target.is_symlink():
                    raise SyncError('unsafe_runtime_path')
                _atomic(target, json_text(report).encode())
        else:
            report = synchronize(args.index, args.checkouts_root, apply=False, publish=False,
                                 clone=args.clone_missing)
        print(json.dumps(report, ensure_ascii=False))
        return 2 if report['blocked_targets'] else 1 if args.check and report['changed_targets'] else 0
    except (SyncError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'schema': SCHEMA, 'status': 'blocked', 'blocked_targets': 1,
                          'code': exc.code if isinstance(exc, SyncError) else 'invalid_input_or_environment'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
