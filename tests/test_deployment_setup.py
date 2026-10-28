import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

path=Path(__file__).resolve().parents[1]/'deploy/setup.py'
spec=importlib.util.spec_from_file_location('deployment_setup',path)
setup=importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

class SetupExportTests(unittest.TestCase):
    def setUp(self):
        self.scratch=tempfile.TemporaryDirectory()
        self.root=Path(self.scratch.name)
        self.root.chmod(0o700)
        self.target=self.root/'service-token'
    def tearDown(self): self.scratch.cleanup()
    def run_export(self,payload):
        stream=io.StringIO()
        with patch('sys.argv',['setup.py','--export-token',str(self.target)]),patch.object(setup.subprocess,'run',return_value=SimpleNamespace(stdout=payload)) as run,contextlib.redirect_stdout(stream):
            setup.main()
        return stream.getvalue(),run
    def test_export_is_private_and_never_echoes_credential(self):
        credential=b'x'*48
        output,run=self.run_export(credential)
        self.assertEqual(self.target.read_bytes(),credential)
        self.assertEqual(self.target.stat().st_mode & 0o777,0o600)
        self.assertNotIn(credential.decode(),output)
        self.assertEqual(run.call_args.kwargs['stdout'],setup.subprocess.PIPE)
    def test_export_refuses_existing_file_without_touching_it(self):
        self.target.write_text('existing user file')
        with self.assertRaises(SystemExit): self.run_export(b'x'*48)
        self.assertEqual(self.target.read_text(),'existing user file')
    def test_invalid_backend_response_does_not_create_file(self):
        with self.assertRaises(SystemExit): self.run_export(b'bad')
        self.assertFalse(self.target.exists())
    def test_public_parent_is_rejected_without_creating_file(self):
        self.root.chmod(0o755)
        with self.assertRaises(SystemExit): self.run_export(b'x'*48)
        self.assertFalse(self.target.exists())

if __name__=='__main__': unittest.main()
