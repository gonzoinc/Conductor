import importlib.util
from pathlib import Path
import shutil
import os
import stat
import subprocess
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('skill_update', ROOT / 'scripts/update_skill.py')
UPDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATE)


def clean_tree(path):
    def readonly_file(function, name, error):
        if Path(name).is_file() and not (os.stat(name).st_mode & stat.S_IWRITE):
            os.chmod(name, stat.S_IREAD | stat.S_IWRITE)
            function(name)
        else:
            raise error[1]
    shutil.rmtree(path, onerror=readonly_file)


class SkillUpdateTests(unittest.TestCase):
    def setUp(self):
        self.work = ROOT / '.codex/skill-update-tests' / uuid.uuid4().hex
        self.work.mkdir(parents=True)
        self.addCleanup(clean_tree, self.work)
        self.repo = self.work / 'repo'
        self.source = self.repo / UPDATE.PREFIX
        self.source.mkdir(parents=True)
        (self.source / 'SKILL.md').write_text('old known skill\n')
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=Conductor Test', '-c', 'user.email=test@example.com',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Known initial skill')
        self.home = self.work / 'home'
        self.home.mkdir()
        self.config = self.home / 'config.toml'
        self.config.write_text('unchanged user configuration\n')
        self.target = self.work / 'skills/conductor-coordinator'
        shutil.copytree(self.source, self.target)
        (self.source / 'SKILL.md').write_text('new skill\n')
        (self.source / 'handoff.md').write_text('new handoff\n')

    def git(self, *args):
        subprocess.run(['git', '-c', 'safe.directory=' + self.repo.as_posix(), *args],
                       cwd=self.repo, check=True, capture_output=True)

    def test_known_upgrade_backup_and_unchanged_rerun(self):
        result = UPDATE.update(self.target, self.home, self.repo)
        self.assertEqual(result['status'], 'updated')
        self.assertEqual((Path(result['backup']) / 'SKILL.md').read_text(), 'old known skill\n')
        self.assertEqual((self.target / 'handoff.md').read_text(), 'new handoff\n')
        self.assertEqual(self.config.read_text(), 'unchanged user configuration\n')
        self.assertEqual(UPDATE.update(self.target, self.home, self.repo)['status'], 'unchanged')

    def test_customized_install_is_preserved(self):
        (self.target / 'SKILL.md').write_text('custom user changes\n')
        with self.assertRaisesRegex(ValueError, 'custom files were preserved'):
            UPDATE.update(self.target, self.home, self.repo)
        self.assertEqual((self.target / 'SKILL.md').read_text(), 'custom user changes\n')
        self.assertFalse((self.home / 'conductor-updates').exists())

    def test_ssh_ownership_marker_is_preserved(self):
        (self.target / '.conductor-ssh-skill').write_text('conductor-ssh-client')
        UPDATE.update(self.target, self.home, self.repo)
        self.assertEqual((self.target / '.conductor-ssh-skill').read_text(), 'conductor-ssh-client')

    def test_failed_swap_restores_old_skill(self):
        from unittest.mock import patch
        original = Path.rename

        def fail_staged(path, destination):
            if path.name.startswith('.conductor-update-'):
                raise OSError('simulated replacement failure')
            return original(path, destination)

        with patch.object(Path, 'rename', fail_staged), self.assertRaises(OSError):
            UPDATE.update(self.target, self.home, self.repo)
        self.assertEqual((self.target / 'SKILL.md').read_text(), 'old known skill\n')
        self.assertFalse(list(self.target.parent.glob('.conductor-update-*')))


if __name__ == '__main__':
    unittest.main()
