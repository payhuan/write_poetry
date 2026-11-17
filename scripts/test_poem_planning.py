"""Structured planning contract tests."""

from copy import deepcopy
import unittest

from day4_example import fixture_state, fixture_plan
from poem_planning import PlanError, TURN_AXES, validate_plan


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

    def test_regulated_verse_requires_arc_roles_and_grounded_turn_state_change(self):
        state = fixture_state('七言律诗')
        valid = fixture_plan(state)['plan']
        self.assertEqual([x['arc_role'] for x in valid['structure_plan']], ['起', '承', '转', '合'])
        bad_plans = []
        bad = fixture_plan(state)['plan']; bad['structure_plan'][2]['arc_role'] = '承'; bad_plans.append(bad)
        bad = fixture_plan(state)['plan']; bad['structure_plan'][2]['turn_plan'] = None; bad_plans.append(bad)
        bad = fixture_plan(state)['plan']; bad['structure_plan'][2]['turn_plan']['after_state'] = '仍在拒饮'; bad_plans.append(bad)
        bad = fixture_plan(state)['plan']; bad['self_check']['turn_is_more_than_next_event']['status'] = 'fail'; bad_plans.append(bad)
        for bad in bad_plans:
            with self.assertRaises(ValueError):
                validate_plan(state, bad)

    def test_invalid_turn_axis_reports_field_received_and_allowed_values(self):
        state = fixture_state('七言律诗')
        plan = fixture_plan(state)['plan']
        turn = plan['structure_plan'][2]['turn_plan']
        for axis in ('emotion', ['emotion']):
            with self.subTest(axis=axis):
                turn['axis'] = axis
                with self.assertRaises(PlanError) as caught:
                    validate_plan(state, plan)
                error = caught.exception.to_dict()
                self.assertEqual(error['code'], 'TURN_PLAN_REQUIRED')
                self.assertEqual(error['details'], [{
                    'location': 'structure_plan[2].turn_plan.axis',
                    'received': axis, 'allowed_values': sorted(TURN_AXES),
                }])
        turn['axis'] = 'emotion_stance'
        self.assertEqual(validate_plan(state, plan), plan)

    def test_quatrain_has_no_formal_arc_roles_or_turn_plan(self):
        state = fixture_state('五言绝句')
        plan = validate_plan(state, fixture_plan(state)['plan'])
        self.assertTrue(all(unit['arc_role'] is None and unit['turn_plan'] is None
                            for unit in plan['structure_plan']))

    def test_other_planning_failures_point_to_repairable_fields(self):
        state = fixture_state('七言律诗')
        valid = fixture_plan(state)['plan']
        cases = (
            (lambda p: p['poem_core'].pop('speaker'), 'INVALID_POEM_CORE',
             'poem_core', 'missing_fields', ['speaker']),
            (lambda p: p['structure_plan'][1]['antithesis'].update(relation='same'),
             'ANTITHESIS_PLAN_REQUIRED', 'structure_plan[1].antithesis.relation', 'received', 'same'),
            (lambda p: p['structure_plan'][2]['turn_plan'].update(relation_to_next='相反'),
             'TURN_PLAN_REQUIRED', 'structure_plan[2].turn_plan.relation_to_next', 'received', '相反'),
            (lambda p: p['core_images'][0].update(first_unit=[]), 'INVALID_IMAGE_PLAN',
             'core_images[0].first_unit', 'received', []),
            (lambda p: p['self_check']['ending_prepared'].update(status='fail'), 'PLAN_SELF_CHECK_FAILED',
             'self_check.ending_prepared.status', 'received', 'fail'),
            (lambda p: p['binding'].update(base_revision=-1), 'STALE_POEM_PLAN',
             'binding', 'differing_fields', ['base_revision']),
        )
        for change, code, location, key, expected in cases:
            with self.subTest(location=location):
                plan = deepcopy(valid)
                change(plan)
                with self.assertRaises(PlanError) as caught:
                    validate_plan(state, plan)
                self.assertEqual(caught.exception.code, code)
                detail = caught.exception.details[0]
                self.assertEqual(detail['location'], location)
                self.assertEqual(detail[key], expected)

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
