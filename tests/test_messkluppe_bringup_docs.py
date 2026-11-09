import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = PROJECT_ROOT / "scripts" / "messkluppe-radio-smoke.sh"
BRINGUP_DOC = PROJECT_ROOT / "docs" / "messkluppe-bringup.md"


class MesskluppeBringupDocsTests(unittest.TestCase):
    def test_radio_smoke_script_has_valid_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(SMOKE_SCRIPT)],
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bringup_doc_references_smoke_and_payload_replay(self):
        text = BRINGUP_DOC.read_text(encoding="utf-8")

        self.assertIn("./scripts/messkluppe-radio-smoke.sh", text)
        self.assertIn("/messkluppe/api/radio/recent-payloads", text)
        self.assertIn("/messkluppe/api/radio/recent-commands", text)
        self.assertIn("/api/ingest-hex", text)


if __name__ == "__main__":
    unittest.main()
