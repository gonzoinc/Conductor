from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

from conductor_coordination.client_bridge import configure, validate_url

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("install_client", ROOT / "scripts/install_client.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class ClientSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.codex = self.root / "codex home"
        self.skills = self.root / "skills"
        self.token = "safe-test-credential-" + "x" * 40

    def tearDown(self):
        self.temp.cleanup()

    def install(self, **changes):
        options = dict(codex_home=self.codex, skills_dir=self.skills,
                       url="http://127.0.0.1:8012/mcp", token=self.token,
                       source_root=ROOT, python=Path(sys.executable))
        options.update(changes)
        return installer.install(**options)

    def test_fresh_install_is_private_and_keeps_secret_out_of_toml(self):
        self.install()
        config = (self.codex / "config.toml").read_text()
        parsed = tomllib.loads(config)
        server = parsed["mcp_servers"]["conductor_coordination"]
        self.assertEqual(server["command"], str(Path(sys.executable).absolute()))
        self.assertNotIn(self.token, config)
        settings = self.codex / "conductor-coordination/client.json"
        self.assertNotIn(self.token, settings.read_text())
        credential = self.codex / "conductor-coordination/service-token"
        self.assertEqual(credential.read_text(), self.token)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(settings.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(settings.parent.stat().st_mode), 0o700)
        self.assertTrue((self.skills / "conductor-coordinator/references/workflow.md").is_file())

    def test_repeat_is_idempotent_and_preserves_unrelated_configuration(self):
        self.codex.mkdir()
        original = b'# user settings\nmodel = "example"\n[mcp_servers.other]\ncommand = "other"\n'
        (self.codex / "config.toml").write_bytes(original)
        first = self.install()
        config = (self.codex / "config.toml").read_bytes()
        self.assertTrue(config.startswith(original))
        self.assertTrue(first["changed_config"])
        self.assertFalse(self.install()["changed_config"])
        self.assertEqual((self.codex / "config.toml").read_bytes(), config)
        self.assertFalse((self.codex / "config.toml.before-conductor").exists())

    def test_existing_legacy_backup_does_not_block_current_config_merge(self):
        self.codex.mkdir()
        original = b'model = "current"\n[mcp_servers.other]\ncommand = "other"\n'
        old_backup = b'model = "older"\n'
        config = self.codex / "config.toml"
        backup = self.codex / "config.toml.before-conductor"
        config.write_bytes(original)
        backup.write_bytes(old_backup)
        self.install()
        self.assertTrue(config.read_bytes().startswith(original))
        self.assertEqual(backup.read_bytes(), old_backup)
        self.assertEqual(list(self.codex.glob("config.toml.before-conductor*")), [backup])

    def test_config_change_during_setup_is_preserved_for_retry(self):
        self.codex.mkdir()
        config = self.codex / "config.toml"
        config.write_bytes(b'model = "before"\n')
        changed = b'model = "changed-during-setup"\n'
        with mock.patch.object(installer, "grant_skill_access", side_effect=lambda _: config.write_bytes(changed)):
            with self.assertRaisesRegex(ValueError, "changed during setup"):
                self.install()
        self.assertEqual(config.read_bytes(), changed)
        self.assertFalse((self.skills / "conductor-coordinator").exists())
        self.assertFalse((self.codex / "conductor-coordination").exists())

    def test_local_user_install_preserves_ledger_and_explains_mcp_transport(self):
        self.codex.mkdir()
        original = b'# shared user settings\nmodel = "example"\n'
        config = self.codex / 'config.toml'
        config.write_bytes(original)
        options = dict(mode='local', url=None, token=None, data_root=self.root / 'state')
        self.install(**options)
        settings = json.loads((self.codex / 'conductor-coordination/client.json').read_text())
        self.assertEqual(settings['mode'], 'local')
        database = Path(settings['db_path'])
        from conductor_coordination.coordination_store import Store
        store = Store(database)
        store.claim('retained-work', 'manager', 'desktop', 'claim-1', 300)
        installed_config = config.read_bytes()
        self.assertFalse(self.install(**options)['changed_config'])
        self.assertEqual(config.read_bytes(), installed_config)
        self.assertFalse((self.codex / 'config.toml.before-conductor').exists())
        self.assertEqual(Store(database).snapshot('retained-work')['workload']['manager'], 'manager')
        self.assertFalse((self.codex / 'conductor-coordination/service-token').exists())
        skill = self.skills / 'conductor-coordinator'
        self.assertIn('conductor_coordination', (skill / 'SKILL.md').read_text())
        self.assertIn('Local MCP mode uses private SQLite', (skill / 'references/workflow.md').read_text())
        if os.name == 'nt':
            sid = installer.windows_user_sid()
            acl = subprocess.run(['icacls', str(skill), '/findsid', f'*{sid}', '/T'],
                                 check=True, capture_output=True, text=True)
            self.assertIn('SKILL.md', acl.stdout)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)

    def test_installer_command_uses_token_file_without_printing_credential(self):
        credential = self.root / "incoming-token"
        credential.write_text(self.token)
        installer.private_permissions(credential)
        command = [sys.executable, str(ROOT / "scripts/install_client.py"), "--token-file", str(credential),
                   "--codex-home", str(self.codex), "--skills-dir", str(self.skills)]
        first = subprocess.run(command, capture_output=True, text=True, timeout=60)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotIn(self.token, first.stdout + first.stderr)
        second = subprocess.run(command, capture_output=True, text=True, timeout=60)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('"changed_config": false', second.stdout)

    def test_conflicting_server_changes_nothing(self):
        self.codex.mkdir()
        original = b'[mcp_servers.conductor_coordination]\nurl = "https://example.com/mcp"\n'
        (self.codex / "config.toml").write_bytes(original)
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual((self.codex / "config.toml").read_bytes(), original)
        self.assertFalse((self.codex / "conductor-coordination").exists())
        self.assertFalse(self.skills.exists())

    def test_conflicting_skill_is_preserved_before_any_credentials_written(self):
        target = self.skills / "conductor-coordinator"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("owned by somebody else")
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual((target / "SKILL.md").read_text(), "owned by somebody else")
        self.assertFalse(self.codex.exists())

    def test_existing_credential_cannot_be_silently_rotated(self):
        self.install()
        with self.assertRaises(ValueError):
            self.install(token="different-" + "x" * 40)
        self.assertEqual((self.codex / "conductor-coordination/service-token").read_text(), self.token)

    def test_legacy_skill_blocks_duplicate_installation(self):
        legacy = self.codex / "skills/conductor-coordinator"
        legacy.mkdir(parents=True)
        (legacy / "SKILL.md").write_text("existing policy")
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.codex / "config.toml").exists())
        self.assertFalse(self.skills.exists())

    def test_failed_config_write_rolls_back_new_credential_and_skill(self):
        self.codex.mkdir()
        original = b'model = "user-model"\n'
        config = self.codex / "config.toml"
        config.write_bytes(original)
        real_write = installer.atomic_write
        def fail_config(path, content):
            if path == config:
                raise OSError("simulated unavailable config")
            return real_write(path, content)
        with mock.patch.object(installer, "atomic_write", side_effect=fail_config):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(config.read_bytes(), original)
        self.assertFalse((self.codex / "conductor-coordination").exists())
        self.assertFalse((self.skills / "conductor-coordinator").exists())
        self.assertFalse((self.codex / "config.toml.before-conductor").exists())

    def test_invalid_toml_does_not_partially_install(self):
        self.codex.mkdir()
        original = b"invalid = [\n"
        (self.codex / "config.toml").write_bytes(original)
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual((self.codex / "config.toml").read_bytes(), original)
        self.assertFalse(self.skills.exists())

    def test_injected_or_remote_plaintext_urls_are_rejected(self):
        for url in ("http://example.com/mcp", "https://user:password@example.com/mcp",
                    "https://example.com/mcp?token=secret", "https://example.com/mcp#fragment",
                    "file:///mcp", "https://example.com/other", "http://127.0.0.1:bad/mcp"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        self.assertEqual(validate_url("https://example.com/mcp"), "https://example.com/mcp")
        self.assertEqual(validate_url("http://[::1]:8012/mcp"), "http://[::1]:8012/mcp")

    def test_bridge_loads_private_settings_and_rejects_public_credential(self):
        self.install()
        settings = self.codex / "conductor-coordination/client.json"
        saved = {key: os.environ.get(key) for key in ("MCP_COORDINATION_URL", "MCP_COORDINATION_TOKEN_FILE")}
        try:
            configure(settings)
            self.assertEqual(os.environ["MCP_COORDINATION_URL"], "http://127.0.0.1:8012/mcp")
            self.assertEqual(Path(os.environ["MCP_COORDINATION_TOKEN_FILE"]).read_text(), self.token)
            if os.name != "nt":
                (settings.parent / "service-token").chmod(0o644)
                with self.assertRaises(ValueError):
                    configure(settings)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    @unittest.skipIf(os.name == "nt", "POSIX symlink test")
    def test_symbolic_link_settings_are_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.codex.mkdir()
        (self.codex / "conductor-coordination").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows ACL test")
    def test_bridge_rejects_broad_windows_credential_acl(self):
        self.install()
        settings = self.codex / "conductor-coordination/client.json"
        credential = settings.parent / "service-token"
        subprocess.run(["icacls", str(credential), "/grant", "*S-1-1-0:(R)"], check=True, capture_output=True)
        with self.assertRaises(ValueError):
            configure(settings)


if __name__ == "__main__":
    unittest.main()
