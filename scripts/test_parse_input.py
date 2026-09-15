"""Run with python -m unittest discover -s scripts -p 'test_*.py'."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from parse_input import ALIASES, ClarificationRequired, parse_request


class InputParsingTests(unittest.TestCase):
    def test_normalization_and_negative_constraint(self):
        raw = "写一首秋日山寺七绝，清冷一些，不要出现钟声"
        state = parse_request(raw, {
            "theme": " 秋日山寺 ", "scene": "山寺", "time": "秋日",
            "poem_type": "七绝", "emotion": ["清冷", "", "清冷"],
            "constraints": ["不要出现钟声"],
            "evidence": {"theme": ["秋日山寺"], "poem_type": ["七绝"]},
        })
        self.assertEqual(state["request"]["raw_request"], raw)
        self.assertEqual(state["poem_type"], "七言绝句")
        self.assertEqual(state["emotion"], ["清冷"])
        self.assertEqual(state["constraints"], ["不要出现钟声"])
        self.assertIn("emotion", state["input_parse"]["unattributed_fields"])
        self.assertFalse(state["assumptions"])

    def test_aliases(self):
        for alias, canonical in ALIASES.items():
            with self.subTest(alias=alias):
                self.assertEqual(parse_request("咏梅", {"theme": "梅", "poem_type": alias})["poem_type"], canonical)

    def test_missing_optional_values(self):
        state = parse_request("咏梅", {"theme": "梅", "poem_type": None, "emotion": None})
        self.assertEqual(state["poem_type"], "七言绝句")
        self.assertTrue(state["assumptions"])
        self.assertEqual(state["request"]["scene"], "")
        self.assertEqual(state["emotion"], [])

    def test_clarification(self):
        for extraction in ({}, {"theme": "梅", "poem_type": "五言"},
                           {"theme": "梅", "questions": ["选择五绝还是七律？"]}):
            with self.subTest(extraction=extraction), self.assertRaises(ClarificationRequired):
                parse_request("写首诗", extraction)

    def test_invalid_schema_and_evidence(self):
        for extraction in ([], {"theme": 1}, {"theme": "梅", "emotion": "喜悦"},
                           {"theme": "梅", "typo": []},
                           {"theme": "梅", "evidence": {"theme": ["山寺"]}},
                           {"theme": "梅", "evidence": {"scene": ["梅"]}}):
            with self.subTest(extraction=extraction), self.assertRaises(ValueError):
                parse_request("咏梅", extraction)
        with self.assertRaises(ValueError):
            parse_request("  ", {"theme": "梅"})

    def test_cli_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, extraction, output = root / "request.txt", root / "intent.json", root / "state.json"
            original.write_text("咏梅", encoding="utf-8-sig")
            extraction.write_text(json.dumps({"theme": "梅"}), encoding="utf-8-sig")
            command = [sys.executable, "-X", "utf8", str(Path(__file__).with_name("parse_input.py")),
                       "--text-file", str(original), "--extraction", str(extraction), "--output", str(output)]
            run = subprocess.run(command, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["theme"], "梅")
            before = output.read_bytes()
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
            self.assertEqual(output.read_bytes(), before)
            extraction.write_text('{"questions": ["Which theme?"]}', encoding="utf-8")
            command[-1] = str(root / "unresolved.json")
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 3)
            self.assertFalse((root / "unresolved.json").exists())


if __name__ == "__main__":
    unittest.main()
