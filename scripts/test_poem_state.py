import tempfile
from pathlib import Path
import unittest

from init_state import initialize
from poem_state import PoemState
from parse_input import parse_request
from assessment_binding import make_binding


class PoemStateTests(unittest.TestCase):
    def state(self):
        return PoemState.create({"raw_request": "咏梅", "theme": "梅", "poem_type": "五言绝句"})

    def assessment(self, state, candidate_id=None):
        data = state.to_dict()
        candidate = next((c for c in data['candidate_history'] if c['id'] == candidate_id), None)
        return {**make_binding(data, candidate), "revision": data["revision"], "valid": True,
                "checks": {k: {"status": "pass"} for k in ("length", "pingze", "rhyme")},
                "errors": [], "warnings": []}

    def test_isolation(self):
        request = {"raw_request": "咏梅", "theme": "梅", "emotion": ["宁静"]}
        state = PoemState.create(request)
        request["emotion"].append("悲伤")
        exported = state.to_dict()
        exported["request"]["emotion"].clear()
        branch = state.fork()
        branch.append_line("墙角数枝梅")
        self.assertEqual(state.to_dict()["emotion"], ["宁静"])
        self.assertEqual(state.to_dict()["lines"], [])

    def test_edits_invalidate(self):
        for edit in (lambda s: s.append_line("凌寒独自开"),
                     lambda s: s.replace_line(1, "墙角一枝梅"),
                     lambda s: s.truncate_lines(0),
                     lambda s: s.set_plan(imagery=["梅"])):
            state = self.state()
            state.append_line("墙角数枝梅")
            old = self.assessment(state)
            state.set_assessment(old, 0.8)
            edit(state)
            self.assertEqual(state.to_dict()["checks"], {})
            self.assertIsNone(state.to_dict()["quality_score"])
            with self.assertRaises(ValueError):
                state.set_assessment(old)

    def test_candidate_history(self):
        state = self.state()
        state.record_candidate("a", "墙角数枝梅", 1, "root")
        state.evaluate_candidate("a", checks=self.assessment(state, 'a'), decision="retained", quality_score=0.7)
        with self.assertRaises(ValueError):
            state.record_candidate("a", "重复", 1)
        state.append_line("墙角数枝梅")
        with self.assertRaises(ValueError):
            state.evaluate_candidate("a", checks=self.assessment(state))
        self.assertEqual(len(state.to_dict()["candidate_history"]), 1)

    def test_invalid_mutation_is_atomic(self):
        state = self.state()
        before = state.to_dict()
        for mutate in (lambda: state.append_line(""), lambda: state.replace_line(0, "梅"),
                       lambda: state.set_budget(beam_width=True), lambda: state.set_plan(rhyme={}),
                       lambda: state.record_candidate("x", "梅", 9),
                       lambda: state.set_assessment({}, float("nan"))):
            with self.assertRaises(ValueError):
                mutate()
            self.assertEqual(state.to_dict(), before)

    def test_structural_unit_edits_use_one_revision(self):
        state = self.state()
        state.append_unit(["墙角数枝梅", "凌寒独自开"])
        self.assertEqual(state.to_dict()["lines"], ["墙角数枝梅", "凌寒独自开"])
        self.assertEqual(state.to_dict()["revision"], 1)
        state.replace_unit(1, ["墙角一枝梅", "凌寒独自开"])
        self.assertEqual(state.to_dict()["revision"], 2)
        before = state.to_dict()
        with self.assertRaises(ValueError):
            state.append_unit(["遥知不是雪", ""])
        self.assertEqual(state.to_dict(), before)

    def test_lifecycle(self):
        state = self.state()
        with self.assertRaises(ValueError):
            state.set_status("completed")
        state.set_plan(template_id="test-template", rhyme={"system": "平水韵", "group": "测试韵部", "first_line_rhymes": False})
        for text in ("墙角数枝梅", "凌寒独自开", "遥知不是雪", "为有暗香来"):
            state.append_line(text)
        with self.assertRaises(ValueError):
            state.append_line("多余一句")
        state.set_status("reviewing")
        with self.assertRaises(ValueError):
            state.set_status("completed")
        state.set_assessment(self.assessment(state), 0.8)
        state.set_status("completed")
        state.replace_line(1, "墙角一枝梅")
        self.assertEqual(state.to_dict()["status"], "drafting")

    def test_unique_rhyme_alias_is_saved_as_canonical_group(self):
        state = self.state()
        state.set_plan(rhyme={"system": "平水韵", "group": "真",
                              "first_line_rhymes": False})
        self.assertEqual(state.to_dict()["rhyme"]["group"], "十一真")
        state.set_plan(rhyme={"system": "平水韵", "group": "上平声部/真",
                              "first_line_rhymes": False})
        self.assertEqual(state.to_dict()["rhyme"]["group"], "十一真")

    def test_persistence_and_migration(self):
        state = PoemState.from_dict(parse_request("咏梅", {"theme": "梅"}))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            state.save(target)
            self.assertEqual(PoemState.load(target).to_dict(), state.to_dict())
            with self.assertRaises(FileExistsError):
                state.save(target)
        legacy = initialize({"raw_request": "咏梅", "theme": "梅"})
        legacy["schema_version"] = "0.1.0"
        del legacy["revision"]
        self.assertEqual(PoemState.from_dict(legacy).to_dict()["revision"], 0)
        legacy["schema_version"] = "99"
        with self.assertRaises(ValueError):
            PoemState.from_dict(legacy)

    def test_invalid_restored_state(self):
        for field, value in (("budget", {}), ("lines", [1]), ("revision", True),
                             ("status", "completed"), ("form", {}), ("quality_score", float("inf"))):
            data = self.state().to_dict()
            data[field] = value
            with self.assertRaises(ValueError):
                PoemState.from_dict(data)

    def test_non_rhyming_candidate_and_summary_validation(self):
        state = self.state()
        state.record_candidate("a", "墙角数枝梅", 1)
        result = self.assessment(state, 'a')
        result["checks"]["rhyme"]["status"] = "not_applicable"
        state.evaluate_candidate("a", checks=result, decision="retained")
        with self.assertRaises(ValueError):
            state.set_assessment(result)
        result["checks"]["length"]["status"] = "fail"
        with self.assertRaises(ValueError):
            state.evaluate_candidate("a", checks=result)


if __name__ == "__main__":
    unittest.main()
