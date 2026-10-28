import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from conductor_coordination.backup import backup, copy_database
from conductor_coordination.deployment import initialize, reject_symlinks, require_local
from conductor_coordination.coordination_store import Store

class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.scratch=tempfile.TemporaryDirectory()
        self.root=Path(self.scratch.name)
        self.root.chmod(0o700)
        self.source=self.root/'source.sqlite3'
        with sqlite3.connect(self.source) as db:
            db.execute('create table proof(value text)')
            db.execute("insert into proof values ('durable')")
        self.source.chmod(0o600)
    def tearDown(self): self.scratch.cleanup()
    def test_online_backup_preserves_committed_wal_data_and_is_private(self):
        with sqlite3.connect(self.source) as writer:
            writer.execute('pragma journal_mode=WAL')
            writer.execute("insert into proof values ('latest')")
            writer.commit()
            snapshot=backup(self.source,self.root)
        with sqlite3.connect(snapshot) as db:
            self.assertEqual(db.execute('select value from proof').fetchall(), [('durable',),('latest',)])
        self.assertEqual(snapshot.stat().st_mode & 0o777,0o600)
    def test_isolated_restore_refuses_overwrite_without_modifying_target(self):
        target=self.root/'restored.sqlite3'
        copy_database(self.source,target)
        previous=target.read_bytes()
        with self.assertRaises(FileExistsError): copy_database(self.source,target)
        self.assertEqual(target.read_bytes(),previous)
    def test_symlink_destination_does_not_touch_target(self):
        target=self.root/'untouched'
        target.write_bytes(b'user owned')
        link=self.root/'link'
        link.symlink_to(target)
        with self.assertRaises(RuntimeError): copy_database(self.source,link)
        self.assertEqual(target.read_bytes(),b'user owned')
    def test_symlink_parent_is_rejected(self):
        link=self.root/'directory-link'
        link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(RuntimeError): reject_symlinks(link/'new')
    def test_backup_refuses_public_destination_directory(self):
        other=self.root/'public'
        other.mkdir(mode=0o755)
        with self.assertRaises(RuntimeError): backup(self.source,other)
    def test_backup_refuses_public_source_database(self):
        self.source.chmod(0o644)
        with self.assertRaises(RuntimeError): backup(self.source,self.root)
    def test_corrupt_database_does_not_leave_partial_restore(self):
        self.source.write_bytes(b'not a database')
        target=self.root/'new.sqlite3'
        with self.assertRaises(sqlite3.DatabaseError): copy_database(self.source,target)
        self.assertFalse(target.exists())
    def test_missing_source_does_not_create_empty_backup(self):
        target=self.root/'new.sqlite3'
        with self.assertRaises(RuntimeError): copy_database(self.root/'missing',target)
        self.assertFalse(target.exists())
    def test_network_filesystem_state_is_rejected(self):
        with patch.object(Path,'read_text',return_value='server /state nfs4 rw 0 0\n'):
            with self.assertRaises(RuntimeError): require_local('/state/data')
    def test_initialize_creates_private_secret_and_never_rotates_it(self):
        state=self.root/'state'
        snapshots=self.root/'backups'
        with patch('os.geteuid',return_value=0), patch('os.chown'):
            initialize(state,snapshots)
            token=(state/'service-token').read_bytes()
            with patch('conductor_coordination.deployment.UID',os.getuid()):
                initialize(state,snapshots)
        self.assertGreaterEqual(len(token),32)
        self.assertEqual((state/'service-token').read_bytes(),token)
        self.assertEqual((state/'service-token').stat().st_mode & 0o777,0o600)
        self.assertEqual((state/'health-token').stat().st_mode & 0o777,0o600)
        self.assertNotEqual((state/'health-token').read_bytes(),token)
        self.assertEqual(Store(state/'data/tasks.sqlite3').principal_for_token(token.decode()),'owner')
        self.assertEqual((state/'data').stat().st_mode & 0o777,0o700)
    def test_initialize_refuses_unsafe_existing_token_without_replacing(self):
        state=self.root/'state'
        state.mkdir(mode=0o700)
        token=state/'service-token'
        token.write_text('z'*48)
        token.chmod(0o644)
        with patch('os.geteuid',return_value=0),patch('os.chown'):
            with self.assertRaises(RuntimeError): initialize(state,self.root/'backups')
        self.assertEqual(token.read_text(),'z'*48)
    def test_initialize_does_not_follow_credential_symlink(self):
        state=self.root/'state'
        state.mkdir(mode=0o700)
        (state/'service-token').symlink_to(self.source)
        previous=self.source.read_bytes()
        with patch('os.geteuid',return_value=0),patch('os.chown'):
            with self.assertRaises(RuntimeError): initialize(state,self.root/'backups')
        self.assertEqual(self.source.read_bytes(),previous)

if __name__=='__main__': unittest.main()
