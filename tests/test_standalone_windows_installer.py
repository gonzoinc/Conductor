"""The downloadable Windows installer must contain the current client and skill."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_windows_installer", ROOT / "scripts/build_windows_installer.py"
)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class StandaloneInstallerTests(unittest.TestCase):
    def test_payload_matches_maintained_sources(self):
        current = BUILDER.INSTALLER.read_text(encoding="utf-8")
        self.assertEqual(BUILDER.build(current), current)
        self.assertNotIn("The complete Conductor repository is required", current)


if __name__ == "__main__":
    unittest.main()
