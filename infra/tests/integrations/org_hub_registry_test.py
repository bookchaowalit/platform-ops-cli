#!/usr/bin/env python3
"""Behavioral contracts for metadata-only hub projections and scoped Git writes."""
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[3] / 'infra/scripts/sync/org_hub_registry.py'


def record(name='test-org/app', **changes):
    value = {'full_name': name, 'html_url': 'https://github.com/' + name,
             'owner': {'login': name.split('/')[0]}, 'default_branch': 'main',
             'visibility': 'private', 'archived': False, 'fork': False}
    value.update(changes)
    return value


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class RegistrySyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location('org_hub_registry', SCRIPT)
            cls.worker = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = cls.worker
            spec.loader.exec_module(cls.worker)

    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'The registry synchronization worker is not implemented')
        self.m = self.worker
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.entry = {'organization': 'test-org', 'repository': 'test-org/test-hub',
                      'url': 'https://github.com/test-org/test-hub', 'checkout_name': 'test-hub',
                      'visibility': 'private', 'purpose': 'Test',
                      'sync': {'inventory_path': 'repository-catalog/registries/repositories.json',
                               'readme_path': 'README.md'}}

    def checkout(self):
        remote = self.base / 'remote.git'
        git(self.base, 'init', '--bare', '--quiet', str(remote))
        root = self.base / 'test-hub'
        git(self.base, 'init', '--quiet', '--initial-branch=main', str(root))
        git(root, 'config', 'user.name', 'Registry Test')
        git(root, 'config', 'user.email', 'registry-test@example.invalid')
        (root / 'README.md').write_text('Human notes\n')
        git(root, 'add', '--', 'README.md')
        git(root, 'commit', '--quiet', '-m', 'initial')
        git(root, 'remote', 'add', 'origin', str(remote))
        git(root, 'push', '--quiet', '--set-upstream', 'origin', 'main')
        return root, remote

    def test_inventory_keeps_archived_private_and_unusual_branch_and_excludes_hub(self):
        rows = [record('test-org/test-hub'), record('test-org/z', archived=True,
                default_branch='release/notes|`<x>'), record('test-org/a', fork=True)]
        result = self.m.normalize_repositories('test-org', 'test-org/test-hub', rows)
        self.assertEqual([r['full_name'] for r in result], ['test-org/a', 'test-org/z'])
        self.assertEqual(result[1], {'full_name': 'test-org/z',
            'url': 'https://github.com/test-org/z', 'default_branch': 'release/notes|`<x>',
            'visibility': 'private', 'archived': True, 'fork': False})

    def test_invalid_or_incomplete_provider_records_fail_instead_of_truncating(self):
        cases = [record('another-org/app'), record(html_url='https://elsewhere.invalid/x'),
                 record(visibility='unknown'), record(archived='false'), record(owner=None),
                 {'full_name': 'test-org/app'}]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(self.m.SyncError):
                self.m.normalize_repositories('test-org', 'test-org/test-hub', [item])
        with self.assertRaises(self.m.SyncError):
            self.m.normalize_repositories('test-org', 'test-org/test-hub', [record(), record()])

    def test_identical_metadata_keeps_snapshot_date_and_change_advances_it(self):
        first = self.m.build_inventory(None, self.entry, [record()],
                    'https://github.com/owner/empire', '2026-01-01')
        same = self.m.build_inventory(first, self.entry, [record()],
                    'https://github.com/owner/empire', '2026-10-15')
        self.assertEqual(same, first)
        changed = self.m.build_inventory(first, self.entry, [record(default_branch='release')],
                    'https://github.com/owner/empire', '2026-10-15')
        self.assertEqual(changed['snapshot_date'], '2026-10-15')

    def test_empty_org_is_honest_and_markdown_escapes_branch_table_structure(self):
        empty = self.m.build_inventory(None, self.entry, [], 'https://github.com/o/e', '2026-10-15')
        self.assertEqual(empty['repositories'], [])
        self.assertIn('0 repo', self.m.render_inventory(empty))
        inventory = self.m.build_inventory(None, self.entry,
            [record(default_branch='release/notes|`<x>')], 'https://github.com/o/e', '2026-10-15')
        rendered = self.m.render_inventory(inventory)
        self.assertIn('notes&#124;', rendered)
        self.assertNotIn('<x>', rendered)

    def test_branch_balanced_markdown_is_encoded_as_literal_text(self):
        inventory = self.m.build_inventory(None, self.entry,
            [record(default_branch='release/`notes`/_notes_')], 'https://github.com/o/e', '2026-10-15')
        rendered = self.m.render_inventory(inventory)
        self.assertIn('release/&#96;notes&#96;/&#95;notes&#95;', rendered)

    def test_generated_region_preserves_human_prose_and_refuses_missing_or_duplicate_markers(self):
        before = 'Human intro\n<!-- BEGIN ORG-HUB INVENTORY -->\nold\n<!-- END ORG-HUB INVENTORY -->\nHuman tail\n'
        after = self.m.replace_region(before, 'ORG-HUB INVENTORY', 'new')
        self.assertEqual(after, 'Human intro\n<!-- BEGIN ORG-HUB INVENTORY -->\nnew\n<!-- END ORG-HUB INVENTORY -->\nHuman tail\n')
        for bad in ['No generated region', before + before,
                    '<!-- END ORG-HUB INVENTORY -->\n<!-- BEGIN ORG-HUB INVENTORY -->']:
            with self.subTest(bad=bad), self.assertRaises(self.m.SyncError):
                self.m.replace_region(bad, 'ORG-HUB INVENTORY', 'new')

    def test_projection_paths_reject_traversal_credentials_and_symlink_escape(self):
        root = self.base / 'test-hub'
        root.mkdir()
        for field, value in [('checkout_name', '../outside'),
                             ('inventory_path', '../../outside.json'),
                             ('inventory_path', '.git/config'),
                             ('readme_path', 'clients/private.md')]:
            entry = json.loads(json.dumps(self.entry))
            if field == 'checkout_name': entry[field] = value
            else: entry['sync'][field] = value
            with self.subTest(field=field), self.assertRaises(self.m.SyncError):
                self.m.projection_paths(entry, self.base)
        outside = self.base / 'outside'
        outside.mkdir()
        (root / 'repository-catalog').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(self.m.SyncError):
            self.m.projection_paths(self.entry, self.base)

    def test_projection_rejects_links_to_client_files_even_inside_own_hub(self):
        root = self.base / 'test-hub'
        (root / 'repository-catalog/registries').mkdir(parents=True)
        (root / 'clients').mkdir()
        private = root / 'clients/private.json'
        private.write_text('{"client": "private payload"}')
        (root / 'repository-catalog/registries/repositories.json').symlink_to(private)
        with self.assertRaises(self.m.SyncError) as error:
            self.m.projection_paths(self.entry, self.base)
        self.assertEqual(error.exception.code, 'unsafe_projection_path')

    def test_real_git_publish_is_scoped_clean_and_idempotent(self):
        root, remote = self.checkout()
        contents = {'README.md': 'Human notes\nGenerated inventory\n', 'registry.json': '{"items": []}\n'}
        first = self.m.commit_projection(root, contents, branch='main', publish=True)
        head = git(root, 'rev-parse', 'HEAD')
        self.assertEqual(first['status'], 'updated')
        self.assertEqual(git(remote, 'rev-parse', 'refs/heads/main'), head)
        self.assertEqual(set(git(root, 'show', '--pretty=', '--name-only', 'HEAD').splitlines()),
                         {'README.md', 'registry.json'})
        self.assertEqual(git(root, 'status', '--porcelain'), '')
        second = self.m.commit_projection(root, contents, branch='main', publish=True)
        self.assertEqual(second['status'], 'unchanged')
        self.assertEqual(git(root, 'rev-parse', 'HEAD'), head)

    def test_dirty_working_tree_is_preserved_without_registry_writes(self):
        root, _ = self.checkout()
        (root / 'README.md').write_text('Owner draft\n')
        with self.assertRaises(self.m.SyncError) as error:
            self.m.commit_projection(root, {'registry.json': '{}\n'}, branch='main', publish=True)
        self.assertEqual(error.exception.code, 'dirty_checkout')
        self.assertEqual((root / 'README.md').read_text(), 'Owner draft\n')
        self.assertFalse((root / 'registry.json').exists())

    def test_failed_commit_restores_only_projection_files_and_index(self):
        root, _ = self.checkout()
        original = self.m._git
        def command(path, *args):
            if args[0] == 'commit': raise self.m.SyncError('git_command_failed')
            return original(path, *args)
        with patch.object(self.m, '_git', side_effect=command), self.assertRaises(self.m.SyncError) as error:
            self.m.commit_projection(root, {'README.md': 'generated\n', 'registry.json': '{}\n'},
                                     branch='main', publish=False)
        self.assertEqual(error.exception.code, 'git_commit_failed')
        self.assertEqual((root / 'README.md').read_text(), 'Human notes\n')
        self.assertFalse((root / 'registry.json').exists())
        self.assertEqual(git(root, 'status', '--porcelain'), '')

    def test_rejected_push_preserves_local_commit_and_other_remote_work(self):
        root, remote = self.checkout()
        rival = self.base / 'rival'
        git(self.base, 'clone', '--quiet', '--branch', 'main', str(remote), str(rival))
        git(rival, 'config', 'user.name', 'Other Author')
        git(rival, 'config', 'user.email', 'other@example.invalid')
        (rival / 'other.md').write_text('Other work\n')
        git(rival, 'add', '--', 'other.md')
        git(rival, 'commit', '--quiet', '-m', 'other work')
        git(rival, 'push', '--quiet', 'origin', 'main')
        theirs = git(remote, 'rev-parse', 'refs/heads/main')
        with self.assertRaises(self.m.SyncError) as error:
            self.m.commit_projection(root, {'registry.json': '{}\n'}, branch='main', publish=True)
        self.assertEqual(error.exception.code, 'git_push_failed')
        self.assertNotEqual(git(root, 'rev-parse', 'HEAD'), theirs)
        self.assertEqual(git(remote, 'rev-parse', 'refs/heads/main'), theirs)
        self.assertTrue((root / 'registry.json').exists())
        self.assertEqual(git(root, 'status', '--porcelain'), '')

    def test_wrong_remote_and_nondefault_branch_are_blocked_without_network(self):
        root, _ = self.checkout()
        with self.assertRaises(self.m.SyncError) as error:
            self.m.prepare_checkout(root, 'test-org/test-hub', 'main', 'irrelevant', apply=False)
        self.assertEqual(error.exception.code, 'unexpected_remote')
        git(root, 'remote', 'set-url', 'origin', 'https://github.com/test-org/test-hub.git')
        git(root, 'checkout', '--quiet', '-b', 'owner-work')
        with self.assertRaises(self.m.SyncError) as error:
            self.m.prepare_checkout(root, 'test-org/test-hub', 'main', 'irrelevant', apply=False)
        self.assertEqual(error.exception.code, 'unexpected_branch')

    def test_clone_missing_creates_a_tracking_checkout_and_leaves_existing_paths(self):
        root, remote = self.checkout()
        target = self.base / 'cloned-hub'
        with patch.object(self.m, 'CLONE_URL', str(remote)):
            self.m.clone_checkout(target, 'test-org/test-hub', 'main')
        self.assertEqual(git(target, 'rev-parse', 'HEAD'), git(root, 'rev-parse', 'HEAD'))
        self.assertEqual(git(target, 'branch', '--show-current'), 'main')
        self.assertEqual(git(target, 'rev-parse', '--abbrev-ref', '@{upstream}'), 'origin/main')
        (root / 'README.md').write_text('Owner draft\n')
        with patch.object(self.m, '_command', side_effect=AssertionError('no clone over a path')):
            self.m.clone_checkout(root, 'test-org/test-hub', 'main')
        self.assertEqual((root / 'README.md').read_text(), 'Owner draft\n')

    def test_failed_clone_is_reported_with_its_own_code(self):
        with patch.object(self.m, 'CLONE_URL', str(self.base / 'missing.git')), \
                self.assertRaises(self.m.SyncError) as error:
            self.m.clone_checkout(self.base / 'test-hub', 'test-org/test-hub', 'main')
        self.assertEqual(error.exception.code, 'git_clone_failed')

    def graph(self, fail_inventory=False, personal_sync=None):
        index = {'schema_version': 'bookchaowalit.empire-hubs.v1', 'updated': '2026-01-01',
                 'repository': 'test/empire', 'personal_hub': {'owner': 'test',
                 'repository': 'test/solo', 'url': 'https://github.com/test/solo',
                 'visibility': 'private', 'checkout_name': 'solo', 'purpose': 'Personal'},
                 'organizations': [self.entry]}
        if personal_sync is not None:
            index['personal_hub']['sync'] = personal_sync
        roots = {'test/empire': self.base / 'empire', 'test-org/test-hub': self.base / 'test-hub'}
        if personal_sync is not None:
            roots['test/solo'] = self.base / 'solo'
        for full_name, root in roots.items():
            git(self.base, 'init', '--quiet', '--initial-branch=main', str(root))
            git(root, 'config', 'user.name', 'Graph Test')
            git(root, 'config', 'user.email', 'graph@example.invalid')
            marker = 'EMPIRE HUBS' if full_name == 'test/empire' else 'ORG-HUB INVENTORY'
            (root / 'README.md').write_text('Human intro\n<!-- BEGIN ' + marker + ' -->\nold\n<!-- END ' + marker + ' -->\nHuman tail\n')
            (root / '.gitignore').write_text('.runtime/\n')
            if full_name == 'test/empire':
                (root / 'registry').mkdir()
                (root / 'registry/hubs.json').write_text(json.dumps(index) + '\n')
            git(root, 'add', '--', 'README.md', '.gitignore', *(['registry/hubs.json'] if full_name == 'test/empire' else []))
            git(root, 'commit', '--quiet', '-m', 'fixture')
            git(root, 'remote', 'add', 'origin', 'https://github.com/' + full_name + '.git')
            git(root, 'update-ref', 'refs/remotes/origin/main', git(root, 'rev-parse', 'HEAD'))
            git(root, 'branch', '--set-upstream-to', 'origin/main', 'main')
        m = self.m
        class Provider:
            def get(self, endpoint):
                if endpoint == 'user': return {'login': 'test'}
                for name in ['test/empire', 'test/solo', 'test-org/test-hub']:
                    if endpoint == 'repos/' + name:
                        return record(name, default_branch='main')
                    if endpoint == 'repos/' + name + '/git/ref/heads/main':
                        return {'object': {'sha': git(roots[name], 'rev-parse', 'HEAD')}}
                raise AssertionError(endpoint)
            def pages(self, endpoint):
                if endpoint == 'user/memberships/orgs?per_page=100':
                    return [{'organization': {'login': 'test-org'}, 'role': 'admin', 'state': 'active'}]
                if endpoint == 'orgs/test-org/repos?per_page=100&type=all':
                    if fail_inventory: raise m.SyncError('github_read_failed')
                    return [record(), record('test-org/test-hub')]
                if endpoint == 'user/repos?per_page=100&affiliation=owner' and personal_sync is not None:
                    # Owner affiliation may still surface another owner's repository; it is filtered out.
                    return [record('test/solo'), record('test/.github', visibility='public'),
                            record('test/old', archived=True), record('someone-else/shared')]
                raise AssertionError(endpoint)
        return roots, Provider()

    def test_provider_failure_never_writes_partial_inventory(self):
        roots, provider = self.graph(fail_inventory=True)
        before = {name: git(root, 'rev-parse', 'HEAD') for name, root in roots.items()}
        with self.assertRaises(self.m.SyncError) as error:
            self.m.synchronize(roots['test/empire'] / 'registry/hubs.json', self.base,
                               apply=True, publish=False, client=provider, today='2026-10-15')
        self.assertEqual(error.exception.code, 'github_read_failed')
        for name, root in roots.items():
            self.assertEqual(git(root, 'rev-parse', 'HEAD'), before[name])
            self.assertEqual(git(root, 'status', '--porcelain'), '')
        self.assertFalse((roots['test-org/test-hub'] / self.entry['sync']['inventory_path']).exists())

    def test_repository_names_allow_a_leading_dot_only_in_the_repository_part(self):
        self.assertEqual(self.m._name('test/.github'), 'test/.github')
        for bad in ['.test/app', 'test/..', 'test/.', 'test/.git/x', 'test/-app']:
            with self.subTest(bad=bad), self.assertRaises(self.m.SyncError):
                self.m._name(bad)
        rows = self.m.normalize_repositories('test-org', 'test-org/test-hub', [record('test-org/.github')])
        self.assertEqual(rows[0]['full_name'], 'test-org/.github')
        entry = json.loads(json.dumps(self.entry))
        entry['checkout_name'] = '.github'
        with self.assertRaises(self.m.SyncError):
            self.m.projection_paths(entry, self.base)

    def test_personal_inventory_keeps_personal_account_scope(self):
        entry = {'organization': 'test', 'repository': 'test/solo'}
        inventory = self.m.build_inventory(None, entry, [record('test/app')], 'https://github.com/test/empire',
                                           '2026-10-15', scope=self.m.personal_scope('test'))
        self.assertEqual(inventory['organization'], 'test')
        self.assertEqual(inventory['inventory_scope'],
                         'All repositories owned by the personal account test except this hub, including '
                         'archived repositories. Organization repositories are listed in their own hubs.')

    def test_personal_sync_block_uses_the_same_projection_safety_rules(self):
        roots, _ = self.graph(personal_sync={'inventory_path': 'repository-catalog/registries/repositories.json',
                                             'readme_path': 'README.md'})
        index = json.loads((roots['test/empire'] / 'registry/hubs.json').read_text())
        self.m.validate_index(index, self.base)
        for config, code in [({'inventory_path': '../escape.json', 'readme_path': 'README.md'}, 'unsafe_projection_path'),
                             ({'inventory_path': 'repository-catalog/registries/repositories.json',
                               'readme_path': 'clients/private.md'}, 'unsafe_projection_path'),
                             ('README.md', 'missing_sync_configuration')]:
            index['personal_hub']['sync'] = config
            with self.subTest(config=config), self.assertRaises(self.m.SyncError) as error:
                self.m.validate_index(index, self.base)
            self.assertEqual(error.exception.code, code)

    def test_personal_hub_without_sync_block_is_only_a_reference(self):
        roots, provider = self.graph()
        report = self.m.synchronize(roots['test/empire'] / 'registry/hubs.json', self.base,
                                   apply=False, publish=False, client=provider, today='2026-10-15')
        self.assertNotIn('test/solo', [row['repository'] for row in report['results']])

    def test_personal_hub_sync_writes_owned_repositories_with_personal_scope(self):
        sync = {'inventory_path': 'repository-catalog/registries/repositories.json', 'readme_path': 'README.md'}
        roots, provider = self.graph(personal_sync=sync)
        index_path = roots['test/empire'] / 'registry/hubs.json'
        preview = self.m.synchronize(index_path, self.base, apply=False, publish=False,
                                     client=provider, today='2026-10-15')
        self.assertEqual(preview['changed_targets'], 3)
        self.assertEqual(preview['blocked_targets'], 0)
        self.assertEqual(preview['checked_personal_hubs'], 1)
        report = self.m.synchronize(index_path, self.base, apply=True, publish=False,
                                    client=provider, today='2026-10-15')
        self.assertEqual(report['blocked_targets'], 0)
        solo = roots['test/solo']
        inventory = json.loads((solo / sync['inventory_path']).read_text())
        self.assertEqual(inventory['organization'], 'test')
        self.assertEqual(inventory['hub_repository'], 'test/solo')
        self.assertTrue(inventory['inventory_scope'].startswith('All repositories owned by the personal account test'))
        self.assertEqual([r['full_name'] for r in inventory['repositories']], ['test/.github', 'test/old'])
        self.assertIn('[.github](https://github.com/test/.github)', (solo / 'README.md').read_text())
        self.assertEqual(git(solo, 'status', '--porcelain'), '')
        again = self.m.synchronize(index_path, self.base, apply=False, publish=False,
                                   client=provider, today='2026-10-16')
        self.assertEqual(again['status'], 'unchanged')

    def test_preview_detects_drift_without_writing_or_committing(self):
        roots, provider = self.graph()
        report = self.m.synchronize(roots['test/empire'] / 'registry/hubs.json', self.base,
                                   apply=False, publish=False, client=provider, today='2026-10-15')
        self.assertEqual(report['status'], 'drift')
        self.assertEqual(report['changed_targets'], 2)
        self.assertEqual(report['blocked_targets'], 0)
        self.assertEqual(report['checked_organization_hubs'], 1)
        self.assertFalse((roots['test-org/test-hub'] / self.entry['sync']['inventory_path']).exists())
        self.assertTrue(all(git(root, 'status', '--porcelain') == '' for root in roots.values()))

    def fast_forward_link(self, full_name):
        roots, provider = self.graph()
        target = roots[full_name]
        remote = self.base / 'upstream.git'
        git(self.base, 'init', '--bare', '--quiet', str(remote))
        git(target, 'push', '--quiet', str(remote), 'main')
        rival = self.base / 'upstream-edit'
        git(self.base, 'clone', '--quiet', '--branch', 'main', str(remote), str(rival))
        git(rival, 'config', 'user.name', 'Upstream Author')
        git(rival, 'config', 'user.email', 'upstream@example.invalid')
        (rival / 'clients').mkdir()
        marker = 'EMPIRE HUBS' if full_name == 'test/empire' else 'ORG-HUB INVENTORY'
        (rival / 'clients/notes.md').write_text('PRIVATE CLIENT CONTENT\n<!-- BEGIN ' + marker + ' -->\nold\n<!-- END ' + marker + ' -->\n')
        (rival / 'README.md').unlink()
        (rival / 'README.md').symlink_to('clients/notes.md')
        git(rival, 'add', '--', 'README.md', 'clients/notes.md')
        git(rival, 'commit', '--quiet', '-m', 'upstream symbolic link')
        git(rival, 'push', '--quiet', 'origin', 'main')
        theirs = git(remote, 'rev-parse', 'refs/heads/main')
        get = provider.get
        def fetch(endpoint):
            if endpoint == 'repos/' + full_name + '/git/ref/heads/main':
                return {'object': {'sha': theirs}}
            return get(endpoint)
        provider.get = fetch
        original = self.m._git
        def local_git(root, *args):
            if root == target and args[0] == 'fetch':
                return original(root, 'fetch', '--quiet', str(remote), 'main')
            return original(root, *args)
        return roots, provider, target, theirs, local_git

    def test_org_fast_forward_cannot_copy_client_content_through_a_new_symlink(self):
        roots, provider, target, theirs, local_git = self.fast_forward_link('test-org/test-hub')
        with patch.object(self.m, '_git', side_effect=local_git):
            report = self.m.synchronize(roots['test/empire'] / 'registry/hubs.json', self.base,
                                       apply=True, publish=False, client=provider, today='2026-10-15')
        result = next(r for r in report['results'] if r['repository'] == 'test-org/test-hub')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['code'], 'unsafe_projection_path')
        self.assertTrue((target / 'README.md').is_symlink())
        self.assertEqual(git(target, 'rev-parse', 'HEAD'), theirs)
        self.assertNotIn('PRIVATE CLIENT CONTENT', git(target, 'show', 'HEAD:README.md'))

    def test_umbrella_fast_forward_is_revalidated_before_reading_a_new_symlink(self):
        roots, provider, target, theirs, local_git = self.fast_forward_link('test/empire')
        with patch.object(self.m, '_git', side_effect=local_git), self.assertRaises(self.m.SyncError) as error:
            self.m.synchronize(roots['test/empire'] / 'registry/hubs.json', self.base,
                               apply=True, publish=False, client=provider, today='2026-10-15')
        self.assertEqual(error.exception.code, 'unsafe_projection_path')
        self.assertTrue((target / 'README.md').is_symlink())
        self.assertEqual(git(target, 'rev-parse', 'HEAD'), theirs)

    def test_latest_failure_replaces_stale_green_report_inside_writer_lock(self):
        roots, provider = self.graph(fail_inventory=True)
        runtime = roots['test/empire'] / '.runtime'
        runtime.mkdir()
        report_path = runtime / 'org-hub-sync.json'
        report_path.write_text('{"status": "unchanged", "checked_at": "yesterday"}\n')
        output = io.StringIO()
        with patch.object(self.m, 'GitHub', return_value=provider), contextlib.redirect_stdout(output):
            code = self.m.main(['--index', str(roots['test/empire'] / 'registry/hubs.json'),
                                '--checkouts-root', str(self.base), '--apply', '--publish'])
        self.assertEqual(code, 2)
        report = json.loads(report_path.read_text())
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['code'], 'github_read_failed')
        self.assertNotEqual(report['checked_at'], 'yesterday')

    def test_lock_contender_never_overwrites_active_writer_report(self):
        roots, _ = self.graph()
        root = roots['test/empire']
        output = io.StringIO()
        with self.m._writer_lock(root):
            report_path = root / '.runtime/org-hub-sync.json'
            report_path.write_text('{"status": "active-writer"}\n')
            with contextlib.redirect_stdout(output):
                code = self.m.main(['--index', str(root / 'registry/hubs.json'),
                                    '--checkouts-root', str(self.base), '--apply'])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(report_path.read_text()), {'status': 'active-writer'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
