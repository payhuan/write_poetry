from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from poem_state import PoemState
from poem_templates import TemplateLibrary, validate_template, DEFAULT_PATH


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.library = TemplateLibrary()

    def state(self):
        return PoemState.create({"raw_request": "咏梅七绝", "theme": "梅", "poem_type": "七言绝句"})

    def test_full_coverage(self):
        rows = self.library.list()
        self.assertEqual(len(rows), 16)
        for poem_type in ("五言绝句", "七言绝句", "五言律诗", "七言律诗"):
            rows = self.library.list(poem_type)
            self.assertEqual(len(rows), 4)
            self.assertEqual(len({r["pattern"] for r in rows}), 4)

    def test_selection(self):
        with self.assertRaises(ValueError):
            self.library.select("七言绝句")
        with self.assertRaises(ValueError):
            self.library.select("七言绝句", template_id="wj_z")
        with self.assertRaises(ValueError):
            self.library.select("七言绝句", template_id="absent")
        t = self.library.select("七言绝句", pattern="平起首句入韵")
        self.assertEqual(t["id"], "qj_p_y")
        t["tunes"].clear()
        self.assertEqual(len(self.library.select("七言绝句", template_id="qj_p_y")["tunes"]), 28)

    def test_bad_templates(self):
        template = self.library.select("七言绝句", template_id="qj_p_y")
        mutations = [lambda t: t["tunes"].pop(),
                     lambda t: t["tunes"].__setitem__(0, deepcopy(t["tunes"][1])),
                     lambda t: t["rhyme"].update(positions=[2, 4]),
                     lambda t: t.update(char_count=27),
                     lambda t: t["tunes"][0].update(tune="随意"),
                     lambda t: t["tunes"][1].update(tune="仄"),
                     lambda t: t["tunes"][6].update(rhythm="句")]
        for mutation in mutations:
            bad = deepcopy(template)
            mutation(bad)
            with self.assertRaises(ValueError):
                validate_template(bad)

    def test_binding_and_recovery(self):
        state = self.state()
        state.set_plan(rhyme={"system": "平水韵", "group": "一东", "first_line_rhymes": None})
        before = state.to_dict()["revision"]
        self.library.bind(state, template_id="qj_z")
        self.assertEqual(state.to_dict()["revision"], before + 1)
        self.assertEqual(state.to_dict()["rhyme"]["group"], "一东")
        self.assertFalse(state.to_dict()["rhyme"]["first_line_rhymes"])
        self.library.bind(state, template_id="qj_z")
        self.assertEqual(state.to_dict()["revision"], before + 1)
        from assessment_binding import make_binding
        state.set_assessment({**make_binding(state.to_dict()), "revision": before + 1, "valid": None,
                              "checks": {"pingze": {"status": "not_run"}}, "errors": [], "warnings": []})
        self.library.bind(state, template_id="qj_p_y")
        self.assertEqual(state.to_dict()["checks"], {})
        self.assertTrue(state.to_dict()["rhyme"]["first_line_rhymes"])
        self.assertEqual(PoemState.from_dict(state.to_dict()).to_dict(), state.to_dict())
        before = state.to_dict()
        with self.assertRaises(ValueError):
            self.library.bind(state, template_id="wl_z")
        with self.assertRaises(ValueError):
            state.set_plan(template_id="qj_z")
        self.assertEqual(state.to_dict(), before)

    def test_duplicate_id(self):
        data = json.loads(DEFAULT_PATH.read_text(encoding="utf-8-sig"))
        data["五言绝句"]["formats"].append(deepcopy(data["五言绝句"]["formats"][0]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                TemplateLibrary(path)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "state.json", Path(directory) / "bound.json"
            self.state().save(source)
            command = [sys.executable, "-X", "utf8", str(Path(__file__).with_name("poem_templates.py")),
                       "bind", "--state", str(source), "--template-id", "qj_p_y", "--output", str(output)]
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(PoemState.load(output).to_dict()["template_id"], "qj_p_y")
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
