"""Regression coverage for deployed mutation report input validation."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PATH = Path(__file__).resolve().parents[1] / "common/test/tools/compare_reports.py"
spec = importlib.util.spec_from_file_location("compare_reports", PATH)
reports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reports)


class ReportValidationTest(unittest.TestCase):
    def test_duplicate_mutant_ids_are_rejected(self):
        item = {"id": 1, "key": {"function": "a", "operator": "comparison",
                                  "original": "<", "replacement": "<=", "ordinal": 1},
                "status": "killed"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps({"valid": True, "results": [item, item]}), encoding="utf-8")
            with self.assertRaises(reports.ReportError):
                reports.load_report(path)


if __name__ == "__main__":
    unittest.main()
