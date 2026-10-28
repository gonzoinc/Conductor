"""The public-release guard catches representative private material."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_public_tree", ROOT / "scripts/check_public_tree.py"
)
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class PublicReleaseGuardTests(unittest.TestCase):
    def test_rejects_private_address_and_key(self):
        content = "host = 192.168." + "2.7\n-----BEGIN OPENSSH " + "PRIVATE KEY-----\n"
        findings = GUARD.findings_for("docs/example.md", content)
        self.assertTrue(any("private network address" in item for item in findings))
        self.assertTrue(any("private key block" in item for item in findings))

    def test_rejects_private_file_name_and_personal_path(self):
        findings = GUARD.findings_for("config/client.json", "C:/Users/" + "someone/.ssh/id_ed25519")
        self.assertTrue(any("private file name" in item for item in findings))
        self.assertTrue(any("personal Windows path" in item for item in findings))

    def test_allows_generic_examples(self):
        self.assertEqual(
            GUARD.findings_for("examples/conductor.yaml.example", "C:/Users/example/file\n127.0.0.1"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
