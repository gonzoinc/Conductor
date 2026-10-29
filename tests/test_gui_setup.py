from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gui"))
from setup_controller import (PREVIOUS_SOURCE_SHA256, SetupCancelled, SetupConfig,
                              _grant_install_access,
                              _ensure_owned, _extract_source, _run, install, validate)
from build_windows import build_source_zip, ensure_builder_pip


@unittest.skipUnless(sys.platform == "win32", "Windows installer")
class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def config(self, **changes):
        values = dict(mode="local", install_dir=self.root / "install", state_dir=self.root / "data", profiles=("desktop",))
        values.update(changes)
        return SetupConfig(**values)

    def test_builder_repairs_existing_venv_without_pip(self):
        venv = self.root / "builder-no-pip"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
        python = venv / "Scripts/python.exe"
        self.assertNotEqual(subprocess.run([str(python), "-m", "pip", "--version"],
                                          capture_output=True).returncode, 0)
        ensure_builder_pip(python)
        subprocess.run([str(python), "-m", "pip", "--version"], check=True, capture_output=True)

    def test_gui_grants_current_user_access_to_installed_runtime(self):
        runtime = self.root / "install" / "python"
        runtime.mkdir(parents=True)
        (runtime / "python.exe").write_bytes(b"fixture")
        _grant_install_access(runtime.parent)
        identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                                  check=True, capture_output=True, text=True).stdout
        import csv
        import io
        sid = next(csv.reader(io.StringIO(identity)))[1]
        result = subprocess.run(["icacls", str(runtime.parent), "/findsid", f"*{sid}", "/T"],
                                check=True, capture_output=True, text=True)
        self.assertIn("python.exe", result.stdout)

    def resources(self):
        path = self.root / "resources"
        path.mkdir()
        (path / "uv.exe").write_bytes(b"fixture-uv")
        with zipfile.ZipFile(path / "conductor-source.zip", "w") as archive:
            archive.writestr("scripts/bootstrap.py", "# fixture\n")
            archive.writestr("scripts/install_client.py", "# fixture\n")
            archive.writestr("pyproject.toml", "[project]\nname='fixture'\n")
            archive.writestr("src/conductor_coordination/accounts.py", "# packaged module\n")
        (path / "runtime-lock.json").write_text(json.dumps({"uv_exe_sha256": hashlib.sha256(b"fixture-uv").hexdigest(),
                                                              "source_archive_sha256": hashlib.sha256((path / "conductor-source.zip").read_bytes()).hexdigest()}))
        return path

    def test_gui_package_excludes_vm_installer(self):
        package = self.root / "local-gui-source.zip"
        build_source_zip(Path(__file__).resolve().parents[1], package)
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
        self.assertIn("scripts/bootstrap.py", names)
        self.assertIn("scripts/install_client.py", names)
        self.assertNotIn("install-vm.sh", names)
        self.assertNotIn("scripts/install_host.py", names)
        self.assertFalse(any(name.startswith("deploy/") for name in names))

    def test_local_commands_use_real_managed_python(self):
        for mode in ("local",):
            with self.subTest(mode=mode):
                base = self.root / mode
                base.mkdir()
                config = SetupConfig(mode, base / "install", base / "data",
                                     ("desktop", "vscode"), True)
                resources = base / "resources"
                resources.mkdir()
                (resources / "uv.exe").write_bytes(b"fixture-uv")
                with zipfile.ZipFile(resources / "conductor-source.zip", "w") as archive:
                    archive.writestr("scripts/bootstrap.py", "# fixture")
                    archive.writestr("scripts/install_client.py", "# fixture")
                    archive.writestr("pyproject.toml", "[project]\nname='fixture'\n")
                (resources / "runtime-lock.json").write_text(json.dumps({"uv_exe_sha256": hashlib.sha256(b"fixture-uv").hexdigest(),
                    "source_archive_sha256": hashlib.sha256((resources / "conductor-source.zip").read_bytes()).hexdigest()}))
                python = config.install_dir / "python/python.exe"
                commands = []
                lines = []
                def fake_run(command, **kwargs):
                    commands.append(command)
                    if len(commands) == 1:
                        python.parent.mkdir(parents=True, exist_ok=True)
                        python.touch()
                with mock.patch("setup_controller._run", side_effect=fake_run), \
                     mock.patch("subprocess.check_output", return_value=str(python) + "\n"):
                    install(config, resources, lines.append, threading.Event())
                self.assertEqual(len(commands), 3)
                for command in commands[1:]:
                    self.assertEqual(command[0], str(python))
                    self.assertIn("--test-root", command)
                    self.assertIn("--profile", command)
                    self.assertIn("--mode", command)
                    self.assertIn("--db-path", command)
                    self.assertNotIn("--token-file", command)

    def test_validation_rejects_hosted_mode_and_unowned_target(self):
        with self.assertRaisesRegex(ValueError, "locally only"):
            validate(self.config(mode="hosted", state_dir=None))
        target = self.root / "existing"
        target.mkdir()
        (target / "user-file").write_text("preserve")
        with self.assertRaisesRegex(ValueError, "unowned"):
            _ensure_owned(target, "fixture-digest")
        self.assertTrue((target / "user-file").exists())

    def test_archive_refuses_path_escape(self):
        archive = self.root / "bad.zip"
        (self.root / "install").mkdir()
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("../outside", "bad")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            _extract_source(archive, self.root / "install", hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertFalse((self.root / "outside").exists())

    def test_rerun_accepts_only_matching_setuptools_build_copies(self):
        resources = self.resources()
        destination = self.root / "install"
        destination.mkdir()
        archive = resources / "conductor-source.zip"
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        _extract_source(archive, destination, digest)
        generated = destination / "source/build/lib/conductor_coordination/accounts.py"
        generated.parent.mkdir(parents=True)
        generated.write_bytes((destination / "source/src/conductor_coordination/accounts.py").read_bytes())
        _extract_source(archive, destination, digest)
        generated.write_text("# changed module\n")
        with self.assertRaisesRegex(ValueError, "unexpected file"):
            _extract_source(archive, destination, digest)

    def test_previous_install_gets_versioned_source_without_replacing_old_files(self):
        resources = self.resources()
        destination = self.root / "install"
        destination.mkdir()
        marker = destination / ".conductor-gui-install.json"
        marker.write_text(json.dumps({"product": "conductor-codex", "purpose": "windows-gui-install",
                                      "version": 1, "source_sha256": PREVIOUS_SOURCE_SHA256}))
        old = destination / "source/user-preserved.txt"
        old.parent.mkdir()
        old.write_text("old source remains")
        archive = resources / "conductor-source.zip"
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        source_name, profile_name = _ensure_owned(destination, digest)
        self.assertEqual(source_name, f"source-{digest[:12]}")
        self.assertEqual(profile_name, f"test-profiles-{digest[:12]}")
        _extract_source(archive, destination, digest, source_name)
        _extract_source(archive, destination, digest, source_name)
        self.assertEqual(old.read_text(), "old source remains")
        self.assertEqual(json.loads(marker.read_text())["source_sha256"], PREVIOUS_SOURCE_SHA256)

    def test_failing_step_includes_its_actual_diagnostic(self):
        lines = []
        with self.assertRaisesRegex(RuntimeError, "Specific setup failure"):
            _run([sys.executable, "-c", "print('Specific setup failure'); raise SystemExit(7)"],
                 env=dict(os.environ), cancel=threading.Event(), progress=lines.append)
        self.assertIn("Specific setup failure", lines)

    def test_cancellation_before_download(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(SetupCancelled):
            with mock.patch("setup_controller._run", side_effect=SetupCancelled):
                install(self.config(), self.resources(), lambda _: None, cancelled)

    def test_identical_rerun_and_tampered_source_are_checked_before_execution(self):
        resources = self.resources()
        config = self.config()
        python = config.install_dir / "python/python.exe"
        commands = []
        def fake_run(command, **kwargs):
            commands.append(command)
            if len(commands) == 1:
                python.parent.mkdir(parents=True, exist_ok=True)
                python.touch()
        with mock.patch("setup_controller._run", side_effect=fake_run), \
             mock.patch("subprocess.check_output", return_value=str(python) + "\n"):
            install(config, resources, lambda _: None, threading.Event())
            install(config, resources, lambda _: None, threading.Event())
            before = len(commands)
            (config.install_dir / "source/scripts/bootstrap.py").write_text("# tampered")
            with self.assertRaisesRegex(ValueError, "differs"):
                install(config, resources, lambda _: None, threading.Event())
            self.assertEqual(len(commands), before)
            with zipfile.ZipFile(resources / "conductor-source.zip") as package:
                (config.install_dir / "source/scripts/bootstrap.py").write_bytes(package.read("scripts/bootstrap.py"))
            (config.install_dir / "source/scripts/json.py").write_text("# shadow standard library")
            with self.assertRaisesRegex(ValueError, "unexpected file"):
                install(config, resources, lambda _: None, threading.Event())
            self.assertEqual(len(commands), before)
            (config.install_dir / "source/scripts/json.py").unlink()
            bytecode = config.install_dir / "source/scripts/__pycache__/bootstrap.cpython-312.pyc"
            bytecode.parent.mkdir()
            bytecode.write_bytes(b"crafted bytecode")
            with self.assertRaisesRegex(ValueError, "unexpected bytecode"):
                install(config, resources, lambda _: None, threading.Event())
            self.assertEqual(len(commands), before)

    def test_tampered_existing_uv_and_changed_bundle_refuse_execution(self):
        resources = self.resources()
        config = self.config()
        python = config.install_dir / "python/python.exe"
        commands = []
        def fake_run(command, **kwargs):
            commands.append(command)
            if len(commands) == 1:
                python.parent.mkdir(parents=True, exist_ok=True)
                python.touch()
        with mock.patch("setup_controller._run", side_effect=fake_run), \
             mock.patch("subprocess.check_output", return_value=str(python) + "\n"):
            install(config, resources, lambda _: None, threading.Event())
            before = len(commands)
            (config.install_dir / "uv.exe").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "downloader differs"):
                install(config, resources, lambda _: None, threading.Event())
            self.assertEqual(len(commands), before)
            (resources / "conductor-source.zip").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "source failed"):
                install(config, resources, lambda _: None, threading.Event())
            self.assertEqual(len(commands), before)


if __name__ == "__main__":
    unittest.main()
