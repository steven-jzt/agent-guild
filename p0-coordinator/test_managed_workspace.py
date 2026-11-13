"""Source-repository isolation, ownership and publication regressions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from coordinator import Coordinator, CoordinatorError
from test_v1_worktree import git


class ManagedWorkspaceTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parent / '.tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root, prefix='isolated-')
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        git(self.source, 'init', '-b', 'main')
        git(self.source, 'config', 'user.name', 'fixture')
        git(self.source, 'config', 'user.email', 'fixture@local')
        (self.source / 'a.txt').write_text('base\n')
        git(self.source, 'add', '.')
        git(self.source, 'commit', '-m', 'base')
        self.sha = git(self.source, 'rev-parse', 'HEAD')
        self.c = Coordinator(self.root / 'state.sqlite')
        project = self.c.create_project('isolation')
        self.task = self.c.create_task(project['project_id'], 'task', ['a.txt'], repo_id='r')

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        return {name: git(self.source, *args) for name, args in {
            'head': ('rev-parse', 'HEAD'), 'refs': ('show-ref',),
            'status': ('status', '--porcelain'), 'index': ('ls-files', '--stage'),
            'config': ('config', '--local', '--list'),
            'worktrees': ('worktree', 'list', '--porcelain'),
        }.items()}

    def workspace(self, name='工作区 空格'):
        return self.c.create_workspace(self.task['id'], 'r', str(self.source), path=str(self.root / name))

    def test_independent_clone_commit_and_delete_do_not_touch_source(self):
        before = self.snapshot()
        ws = self.workspace()
        self.assertEqual(ws['storage_kind'], 'independent_clone')
        path = Path(ws['path'])
        self.assertTrue((path / '.git').is_dir())
        self.assertFalse((path / '.git/objects/info/alternates').exists())
        self.assertEqual(git(path, 'remote'), '')
        (path / 'a.txt').write_text('changed\n')
        self.c.commit_workspace(ws['workspace_id'])
        self.assertEqual(self.snapshot(), before)
        self.c.remove_workspace(ws['workspace_id'])
        self.assertFalse(path.exists())
        self.assertEqual(self.snapshot(), before)

    def test_dirty_source_is_not_copied_and_warns(self):
        (self.source / 'a.txt').write_text('unsaved\n')
        before = self.snapshot()
        ws = self.workspace()
        self.assertTrue(ws['warnings'])
        self.assertEqual((Path(ws['path']) / 'a.txt').read_text(), 'base\n')
        self.assertEqual(self.snapshot(), before)

    def test_default_location_is_managed_and_source_nested_paths_refused(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'appdata')}):
            ws = self.c.create_workspace(self.task['id'], 'r', str(self.source))
        self.assertTrue(Path(ws['path']).is_relative_to(self.root / 'appdata/AgentGuild/workspaces'))
        with self.assertRaises(CoordinatorError):
            self.c.create_workspace(self.task['id'], 'r', str(self.source), path=str(self.source / 'nested'))

    def test_creation_database_failure_cleans_only_owned_clone(self):
        before = self.snapshot()
        with patch.object(self.c, '_event', side_effect=RuntimeError('database failure')):
            with self.assertRaisesRegex(RuntimeError, 'database failure'):
                self.workspace()
        self.assertFalse((self.root / '工作区 空格').exists())
        self.assertEqual(self.snapshot(), before)

    def test_active_execution_and_unmerged_candidate_protect_workspace(self):
        ws = self.workspace()
        execution = self.c.start_execution(self.task['id'], 'fixture', workspace=ws['path'])
        with self.assertRaises(CoordinatorError):
            self.c.remove_workspace(ws['workspace_id'])
        self.c.finish_execution(execution['execution_id'], 'succeeded')
        (Path(ws['path']) / 'a.txt').write_text('candidate\n')
        self.c.commit_workspace(ws['workspace_id'])
        cand = self.c.create_candidate_from_workspace(ws['workspace_id'])
        self.assertEqual(cand['meta']['workspace_id'], ws['workspace_id'])
        with self.assertRaises(CoordinatorError):
            self.c.remove_workspace(ws['workspace_id'])

    def test_legacy_workspace_is_not_migrated(self):
        legacy = self.root / 'legacy'
        git(self.source, 'worktree', 'add', '-b', 'legacy', str(legacy))
        with self.c._tx() as conn:
            conn.execute("INSERT INTO workspaces(id,task_id,repo_id,repo_path,path,base_sha,status,created_at) VALUES (?,?,?,?,?,?,'active',0)",
                         ('W-legacy', self.task['id'], 'r', str(self.source), str(legacy), self.sha))
        reopened = Coordinator(self.root / 'state.sqlite')
        self.assertEqual(reopened.get_workspace('W-legacy')['storage_kind'], 'legacy_worktree')
        self.assertTrue((legacy / '.git').is_file())
        reopened.remove_workspace('W-legacy')
        self.assertFalse(legacy.exists())

    def test_semantic_capture_uses_bound_workspace_even_after_another_is_created(self):
        ws = self.workspace('first')
        (Path(ws['path']) / 'a.txt').write_text('first candidate\n')
        self.c.commit_workspace(ws['workspace_id'])
        cand = self.c.create_candidate_from_workspace(ws['workspace_id'])
        self.workspace('second')
        with patch('semantic.git_changes', return_value=[]) as capture:
            self.c.capture_semantic_changes(cand['candidate_id'])
        self.assertEqual(Path(capture.call_args.args[0]), Path(ws['path']))

    def test_missing_ownership_receipt_and_nonforce_dirty_removal_are_refused(self):
        ws = self.workspace()
        (Path(ws['path']) / 'a.txt').write_text('uncommitted\n')
        with self.assertRaises(CoordinatorError):
            self.c.remove_workspace(ws['workspace_id'], force=False)
        path = Path(ws['path'])
        path.with_name(path.name + '.guild-owner.json').unlink()
        with self.assertRaises(CoordinatorError):
            self.c.remove_workspace(ws['workspace_id'])
        self.assertTrue(path.is_dir())


if __name__ == '__main__':
    unittest.main()
