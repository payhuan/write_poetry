import unittest
from parse_input import parse_request, ClarificationRequired
from poem_state import PoemState


class ContentRequirementTests(unittest.TestCase):
    def fragment(self, text="山寺秋深落叶多", **changes):
        return {"id": "f1", "text": text, "kind": "line", "source_excerpt": text, **changes}

    def parse(self, fragment, **changes):
        return parse_request("咏秋：" + fragment["source_excerpt"],
                             {"theme": "秋", "required_fragments": [fragment], **changes})

    def test_defaults_and_roundtrip(self):
        raw = "写秋诗，用庄周梦蝶的典故，包含山寺秋深落叶多"
        state = parse_request(raw, {"theme": "秋", "allusions": [
            {"id": "a1", "name": "庄周梦蝶", "source_excerpt": "用庄周梦蝶的典故"}],
            "required_fragments": [self.fragment()]})
        self.assertTrue(state["request"]["allusions"][0]["required"])
        self.assertEqual(state["request"]["required_fragments"][0]["mode"], "exact")
        self.assertEqual(PoemState.from_dict(state).to_dict(), state)
        self.assertEqual(state["lines"], [])

    def test_adapt_and_inspiration(self):
        for mode in ("adapt", "inspiration"):
            state = self.parse(self.fragment("秋山", mode=mode))
            self.assertEqual(state["request"]["required_fragments"][0]["text"], "秋山")

    def test_structural_conflicts(self):
        for fragment in (self.fragment("秋山"), self.fragment(line_index=5),
                         self.fragment(mode="inspiration", line_index=2)):
            with self.assertRaises(ClarificationRequired):
                self.parse(fragment)
        with self.assertRaises(ClarificationRequired):
            self.parse(self.fragment(), poem_type="五绝")

    def test_source_and_types(self):
        with self.assertRaises(ValueError):
            self.parse(self.fragment(source_excerpt="不同原文"))
        with self.assertRaises(ValueError):
            self.parse(self.fragment(line_index=True))
        with self.assertRaises(ValueError):
            parse_request("秋山", {"theme": "秋", "allusions": [
                {"id": "a1", "name": "梦蝶", "source_excerpt": "梦蝶"}]})

    def test_fixed_position_conflicts(self):
        a = self.fragment(line_index=2)
        b = self.fragment("古寺秋深落叶多", id="f2", line_index=2)
        with self.assertRaises(ClarificationRequired):
            parse_request(a["text"] + b["text"], {"theme": "秋", "required_fragments": [a, b]})
        b = self.fragment("春风", id="f2", kind="phrase", line_index=2)
        with self.assertRaises(ClarificationRequired):
            parse_request(a["text"] + b["text"], {"theme": "秋", "required_fragments": [a, b]})

    def test_optional_legacy_fields(self):
        old = PoemState.create({"raw_request": "秋", "theme": "秋"}).to_dict()
        self.assertNotIn("allusions", old["request"])
        self.assertEqual(PoemState.from_dict(old).to_dict(), old)


if __name__ == "__main__":
    unittest.main()
