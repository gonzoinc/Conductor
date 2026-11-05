"""Regression coverage for offline migration and path redirection guards."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import repair_local as repair
import install_client as installer
from conductor_coordination.client_bridge import configure
from conductor_coordination.coordination_store import Store
from conductor_coordination import state_paths


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "old" / "tasks.sqlite3"
        self.source.parent.mkdir()
        installer.private_permissions(self.source.parent)
        Store(self.source)
        installer.private_permissions(self.source)
        installer.atomic_write(self.source.parent / repair.MARKER,
                               b'{"purpose":"conductor-local-state","version":1}\n')
        self.settings = self.root / "client.json"
        installer.atomic_write(self.settings, json.dumps({"mode": "local", "state_root": str(self.source.parent),
                                                         "db_path": str(self.source)}).encode())
        self.before = self.settings.read_bytes()
        self.destination = self.root / "new"

    def migrate(self):
        return repair.migrate(self.source, self.destination, [self.settings], clients_stopped=True)

    def test_preserves_every_record_and_supports_rollback(self):
        store = Store(self.source)
        store.claim("retained-work", "manager", "desktop", "claim-1", 300)
        # Preserve workload, operation, event and user records.
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("INSERT INTO users (id, name, created) VALUES (?, ?, ?)", ("repair-test", "Repair Test", 1.0))
            db.commit()
        journal = self.migrate()
        with closing(sqlite3.connect(self.source)) as old, closing(sqlite3.connect(self.destination / "tasks.sqlite3")) as new:
            self.assertEqual(repair.snapshot(old), repair.snapshot(new))
        self.assertEqual(configure(self.settings)["db_path"], str(self.destination / "tasks.sqlite3"))
        self.assertEqual(Store(self.destination / "tasks.sqlite3").snapshot("retained-work")["workload"]["manager"], "manager")
        self.assertEqual((self.destination / repair.MARKER).read_bytes(), (self.source.parent / repair.MARKER).read_bytes())
        repair.rollback(journal, clients_stopped=True)
        self.assertEqual(self.settings.read_bytes(), self.before)
        self.assertTrue(self.source.is_file())
        self.assertTrue((self.destination / "tasks.sqlite3").is_file())

    def test_two_profiles_switch_and_partial_switch_rolls_back(self):
        second = self.root / "vscode.json"
        installer.atomic_write(second, self.before)
        original = repair.atomic_write
        failed = False
        def fail_second(path, content):
            nonlocal failed
            if path == second and not failed:
                failed = True
                raise OSError("interrupted second profile")
            original(path, content)
        with mock.patch.object(repair, "atomic_write", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "second profile"):
                repair.migrate(self.source, self.destination, [self.settings, second], clients_stopped=True)
        self.assertEqual(self.settings.read_bytes(), self.before)
        self.assertEqual(second.read_bytes(), self.before)

    def test_failed_settings_switch_restores_original(self):
        original = repair.atomic_write
        failed = False
        def fail_once(path, content):
            nonlocal failed
            if path == self.settings and not failed:
                failed = True
                raise OSError("simulated interrupted settings switch")
            original(path, content)
        with mock.patch.object(repair, "atomic_write", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.migrate()
        self.assertEqual(self.settings.read_bytes(), self.before)
        self.assertTrue(self.source.is_file())

    @unittest.skipUnless(os.name == "nt", "Windows package paths")
    def test_real_redirected_candidate_is_migrated(self):
        home = self.root / "profile"
        logical = home / "AppData/Local/Conductor/tasks.sqlite3"
        physical = home / "AppData/Local/Packages/Example.Desktop/LocalCache/Local/Conductor/tasks.sqlite3"
        physical.parent.mkdir(parents=True)
        installer.private_permissions(physical.parent)
        self.source.replace(physical)
        (self.source.parent / repair.MARKER).replace(physical.parent / repair.MARKER)
        installer.atomic_write(self.settings, json.dumps({"mode": "local", "db_path": str(logical),
                                                         "state_root": str(logical.parent)}).encode())
        with mock.patch.object(Path, "home", return_value=home):
            self.assertEqual(repair.source_candidates(logical), [physical])
            repair.migrate(physical, self.destination, [self.settings], clients_stopped=True)
        self.assertTrue(physical.is_file())
        self.assertEqual(configure(self.settings)["db_path"], str(self.destination / "tasks.sqlite3"))
        self.assertFalse(logical.exists())

    @unittest.skipUnless(os.name == "nt", "Windows package paths")
    def test_two_aliases_of_one_physical_ledger_are_not_ambiguous(self):
        home = self.root / "profile"
        logical = home / "AppData/Local/Conductor/tasks.sqlite3"
        physical = home / "AppData/Local/Packages/Example.Desktop/LocalCache/Local/Conductor/tasks.sqlite3"
        physical.parent.mkdir(parents=True)
        logical.parent.mkdir(parents=True)
        installer.private_permissions(physical.parent)
        self.source.replace(physical)
        (self.source.parent / repair.MARKER).replace(physical.parent / repair.MARKER)
        logical.hardlink_to(physical)
        installer.atomic_write(self.settings, json.dumps({"mode": "local", "db_path": str(logical),
                                                         "state_root": str(logical.parent)}).encode())
        with mock.patch.object(Path, "home", return_value=home):
            self.assertEqual(len(repair.source_candidates(logical)), 1)
            repair.migrate(physical, self.destination, [self.settings], clients_stopped=True)
        self.assertTrue(logical.samefile(physical))
        self.assertTrue((self.destination / "tasks.sqlite3").is_file())

    def test_requires_stopped_clients_and_unused_destination(self):
        with self.assertRaisesRegex(ValueError, "Stop Desktop"):
            repair.migrate(self.source, self.destination, [self.settings], clients_stopped=False)
        self.destination.mkdir()
        with self.assertRaisesRegex(ValueError, "must not exist"):
            self.migrate()
        self.assertEqual(self.settings.read_bytes(), self.before)

    def test_missing_source_and_ambiguous_source_never_create_destination(self):
        with mock.patch.object(repair, "source_candidates", return_value=[self.source, self.root / "other.sqlite3"]):
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                self.migrate()
        self.source.unlink()
        with self.assertRaises(ValueError):
            self.migrate()
        self.assertFalse(self.destination.exists())
        with self.assertRaisesRegex(ValueError, "no replacement database"):
            configure(self.settings)
        self.assertFalse(self.source.exists())

    def test_installer_does_not_recreate_configured_missing_ledger(self):
        self.source.unlink()
        home = self.root / "home"
        folder = home / "conductor-coordination"
        folder.mkdir(parents=True)
        installer.private_permissions(folder)
        installer.atomic_write(folder / "client.json", self.before)
        with self.assertRaisesRegex(ValueError, "no empty replacement"):
            installer.install(codex_home=home, skills_dir=self.root / "skills", source_root=ROOT,
                              python=Path(sys.executable), mode="local", data_root=self.source.parent)
        self.assertFalse(self.source.exists())

    def test_rollback_preserves_later_settings_edits(self):
        journal = self.migrate()
        installer.atomic_write(self.settings, b'{"user_edit":true}')
        with self.assertRaisesRegex(ValueError, "changed after repair"):
            repair.rollback(journal, clients_stopped=True)
        self.assertEqual(self.settings.read_bytes(), b'{"user_edit":true}')

    @unittest.skipUnless(os.name == "nt", "Windows paths")
    def test_windows_default_and_appdata_rejection(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "redirected")}):
            self.assertEqual(state_paths.local_state_root(), Path.home() / ".conductor")
        with self.assertRaisesRegex(ValueError, "resolve differently"):
            state_paths.validate_shared_windows_path(Path.home() / "AppData/Local/Conductor")

    def test_unix_default_unchanged(self):
        from types import SimpleNamespace
        with mock.patch.object(state_paths, "os", SimpleNamespace(name="posix", environ={"XDG_STATE_HOME": str(self.root)})):
            self.assertEqual(state_paths.local_state_root(), self.root / "conductor")
