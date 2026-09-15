from copy import deepcopy
import unittest

from parse_input import parse_request, ClarificationRequired
from poem_state import PoemState
from poem_templates import TemplateLibrary


class DayOneTests(unittest.TestCase):
    def parse(self, meter, **extra):
        return parse_request("写梅花七绝，按指定诗格", {"theme": "梅花", "poem_type": "七绝", "meter": meter, **extra})

    def test_explicit_combinations(self):
        for start, first, expected in (("平起", True, "qj_p_y"), ("平起", False, "qj_p"),
                                       ("仄起", True, "qj_z_y"), ("仄起", False, "qj_z")):
            state = self.parse({"start": start, "first_line_rhymes": first})
            self.assertEqual(state["template_id"], expected)
            self.assertIs(state["request"]["meter"]["first_line_rhymes"], first)
            self.assertEqual(state["meter_template"]["data"]["pattern"], start + ("首句入韵" if first else "首句不入韵"))

    def test_partial_preferences(self):
        for meter in ({"start": "平起"}, {"first_line_rhymes": False}, {}):
            state = self.parse(meter)
            self.assertIsNone(state["template_id"])
            candidates = TemplateLibrary().candidates(state["poem_type"], meter=state["request"]["meter"])
            self.assertEqual(len(candidates), 4 if not meter else 2)
        model = PoemState.from_dict(self.parse({"start": "平起"}))
        before = model.to_dict()
        with self.assertRaises(ValueError):
            TemplateLibrary().bind(model, template_id="qj_z")
        self.assertEqual(before, model.to_dict())
        TemplateLibrary().bind(model, template_id="qj_p_y")

    def test_pattern_and_inferred_type(self):
        self.assertEqual(self.parse({"pattern": "仄起首句不入韵"})["template_id"], "qj_z")
        state = parse_request("咏梅，使用 wj_z", {"theme": "梅", "meter": {"template_id": "wj_z"}})
        self.assertEqual(state["poem_type"], "五言绝句")
        self.assertEqual(state["template_id"], "wj_z")
        self.assertEqual(state["assumptions"], ["根据指定模板确定诗体"])
        direct = PoemState.create({"raw_request": "咏梅", "theme": "梅", "meter": {"template_id": "wj_z"}})
        self.assertEqual(direct.to_dict()["poem_type"], "五言绝句")

    def test_conflicts(self):
        for meter in ({"template_id": "wj_z"}, {"template_id": "qj_z", "start": "平起"},
                      {"pattern": "平起首句入韵", "first_line_rhymes": False},
                      {"first_line_rhymes": "false"}, {"template_id": "unknown"}):
            with self.assertRaises(ClarificationRequired):
                self.parse(meter)

    def test_explicit_false_evidence(self):
        state = parse_request("咏梅，首句不要押韵", {"theme": "梅", "meter": {"first_line_rhymes": False},
                                                   "evidence": {"meter": ["首句不要押韵"]}})
        self.assertNotIn("meter", state["input_parse"]["unattributed_fields"])
        self.assertIs(state["rhyme"]["first_line_rhymes"], False)

    def test_input_envelope(self):
        state = self.parse({})
        envelope = state["request"]["input"]
        self.assertEqual(envelope["attachments"], [])
        self.assertEqual(envelope["text"], state["request"]["raw_request"])
        raw = "写梅花七绝，按指定诗格"
        parse_request(raw, {"theme": "梅"}, input_envelope=envelope)
        for change in ({"attachments": [{"type": "image", "asset_id": "image-1"}]},
                       {"text": "different"}, {"version": 2}):
            bad = deepcopy(envelope)
            bad.update(change)
            with self.assertRaises(ValueError):
                parse_request(raw, {"theme": "梅"}, input_envelope=bad)

    def test_old_snapshot_does_not_invent_user_preferences(self):
        model = PoemState.create({"raw_request": "梅", "theme": "梅"})
        TemplateLibrary().bind(model, template_id="qj_p_y")
        old = model.to_dict()
        old["schema_version"] = "0.2.0"
        del old["request"]["meter"]
        del old["request"]["input"]
        restored = PoemState.from_dict(old).to_dict()
        self.assertEqual(restored["schema_version"], "0.3.0")
        self.assertIsNone(restored["request"]["meter"]["start"])
        self.assertEqual(restored["template_id"], "qj_p_y")


if __name__ == "__main__":
    unittest.main()
