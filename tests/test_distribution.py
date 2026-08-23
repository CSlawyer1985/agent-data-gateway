from __future__ import annotations

import re
import unittest
from pathlib import Path

import gateway


ROOT = Path(__file__).resolve().parent.parent


class DistributionContractTest(unittest.TestCase):
    def test_release_version_has_one_authority_projection(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version = "([^"]+)"$', text, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), gateway.__version__)

    def test_package_discovery_excludes_stale_build_trees(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('exclude = ["build*", "dist*"]', text)


if __name__ == "__main__":
    unittest.main()
