"""Structured planning contract tests."""

from copy import deepcopy
import unittest

from day4_example import fixture_state, fixture_plan
from poem_planning import validate_plan


class PoemPlanningTests(unittest.TestCase):
    def test_four_forms_and_middle_couplet_antithesis(self):
        for form in ('五言绝句', '七言绝句', '五言律诗', '七言律诗'):
            state = fixture_state(form)
            plan = validate_plan(state, fixture_plan(state)['plan'])
            self.assertEqual(len(plan['structure_plan']), 4)
            middle = [x for x in plan['structure_plan'] if x['antithesis']]
            self.assertEqual(len(middle), 2 if '律诗' in form else 0)
            self.assertEqual(fixture_plan(state)['plan'], plan)

    def test_missing_unit_stale_binding_and_failed_check_rejected(self):
        state = fixture_state('七言律诗')
        cases = []
        bad = fixture_plan(state)['plan']; bad['structure_plan'].pop(); cases.append(bad)
        bad = fixture_plan(state)['plan']; bad['binding']['base_revision'] += 1; cases.append(bad)
        bad = fixture_plan(state)['plan']; bad['self_check']['semantic_progression']['status'] = 'fail'; cases.append(bad)
        bad = fixture_plan(state)['plan']; bad['self_check']['viewpoint_continuity']['status'] = 'fail'; cases.append(bad)
        bad = fixture_plan(state)['plan']; bad['structure_plan'][1]['antithesis'] = None; cases.append(bad)
        bad = fixture_plan(state)['plan']; bad['structure_plan'][1]['unit_id'] = 'line-9'; cases.append(bad)
        for bad in cases:
            with self.assertRaises(ValueError):
                validate_plan(state, bad)

    def test_wrong_allusion_arrangement_binding_is_rejected(self):
        from poem_state import PoemState
        from poem_templates import TemplateLibrary
        state = PoemState.create({'raw_request': '咏山，必用梦蝶', 'theme': '山',
                                   'poem_type': '五言绝句',
                                   'meter': {'start': '仄起', 'first_line_rhymes': False},
                                   'allusions': [{'id': 'a1', 'name': '梦蝶', 'required': True,
                                                  'source_excerpt': '必用梦蝶'}]})
        TemplateLibrary().bind(state)
        state.set_plan(rhyme={'system': '平水韵', 'group': '一东', 'first_line_rhymes': False})
        plan = fixture_plan(state)['plan']
        plan['allusion_plan']['items'][0]['unit_id'] = 'missing-unit'
        with self.assertRaises(ValueError):
            validate_plan(state, plan)

    def test_user_provenance_requires_verbatim_evidence(self):
        state = fixture_state()
        plan = fixture_plan(state)['plan']
        plan['provenance'][0]['evidence'] = '不存在于原文'
        with self.assertRaises(ValueError):
            validate_plan(state, plan)


if __name__ == '__main__':
    unittest.main()
