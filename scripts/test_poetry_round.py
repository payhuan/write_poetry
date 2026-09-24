"""Deterministic model fixtures test contracts and orchestration, never poetry taste."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from constraint_engine import ConstraintEngine
from parse_input import parse_request
from poem_state import PoemState
from poem_templates import TemplateLibrary
from poetry_round import (ENVELOPE, RUBRIC_VERSION, WEIGHTS, RoundError, export_context,
                          filter_candidates, review_readings, review_duplicates, finish_round, read_json,
                          validate_scores, write_new_json)
from rhyme_lexicon import RhymeLexicon
from test_duplicate_helpers import duplicate_decisions

FIXTURE = {'上平声部': {'一东': list('天地人风同'), '二冬': ['同', '冬']},
           '上声部': {'一董': list('水火土风')}}


def model_candidates(context, texts=None):
    texts = texts if texts is not None else ['天地水火人', '人天火水地', '天水水水天', '天风水水天']
    return {**{k: context[k] for k in ENVELOPE}, 'attempt': 1,
            'candidates': [{'id': 'c' + str(i), 'text': t, 'line_index': context['line_index']}
                           for i, t in enumerate(texts)]}


def model_scores(filtered, value=0.8):
    return {**{k: filtered['context'][k] for k in ENVELOPE}, 'attempt': 1,
            'filter_sha256': filtered['filter_sha256'], 'rubric_version': RUBRIC_VERSION,
            'scores': [{'id': c['id'], 'evaluation': {'dimensions': {k: value for k in WEIGHTS},
                        'reason': '固定测试评分，用于验证程序契约，不代表文学判断。'}}
                       for c in filtered['score_request']['candidates']]}


def model_readings(filtered):
    reviews = []
    for item in filtered['reading_request']['reviews']:
        decisions = []
        for occurrence in item['request']['occurrences']:
            option = next(x for x in occurrence['options'] if x['section'] == '上平声部')
            decisions.append({'coordinate': occurrence['coordinate'], 'char': occurrence['char'],
                              'status': 'selected', 'section': option['section'],
                              'group': option['group'], 'reason': '句义在此取平声义。'})
        reviews.append({'id': item['id'], 'review': {
            'review_version': item['request']['review_version'],
            'request_sha256': item['request']['request_sha256'], 'decisions': decisions}})
    return {**{k: filtered['context'][k] for k in ENVELOPE}, 'attempt': 1,
            'filter_sha256': filtered['filter_sha256'], 'reviews': reviews}


class RoundTests(unittest.TestCase):
    def setUp(self):
        self.library = TemplateLibrary()
        self.engine = ConstraintEngine(RhymeLexicon(FIXTURE), duplicate_policy='report')
        self.state = self.new_state()
        self.context = export_context(self.state, 2)

    def new_state(self, form='五言绝句'):
        state = PoemState.create({'raw_request': '写山中五绝，仄起，首句不入韵，不用愁字',
                                  'theme': '山中', 'emotion': ['宁静'], 'poem_type': form,
                                  'constraints': ['不用愁字'],
                                  'meter': {'start': '仄起', 'first_line_rhymes': False}})
        self.library.bind(state)
        state.set_plan(imagery=['山', '月'], structure=[{'unit': '颔联', 'intent': '从远山写到近水'}],
                       rhyme={'system': '平水韵', 'group': '一东', 'first_line_rhymes': False})
        return state

    def filtered(self, texts=None):
        return filter_candidates(self.state, self.context, model_candidates(self.context, texts), engine=self.engine)

    def assert_error(self, code, call):
        with self.assertRaises(RoundError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_user_meter_and_plan_propagation_from_parse(self):
        for form in ('五言绝句', '七言绝句', '五言律诗', '七言律诗'):
            for start in ('平起', '仄起'):
                for first in (True, False):
                    state = PoemState.from_dict(parse_request('测试', {'theme': '山中', 'poem_type': form,
                                                'meter': {'start': start, 'first_line_rhymes': first}}))
                    context = export_context(state, 1)
                    self.assertEqual(context['user_meter']['start'], start)
                    self.assertEqual(context['is_rhyme_line'], first)
                    self.assertEqual(context['tone_requirements'][1]['tune'], start[0])
                    self.assertEqual(len(context['tone_requirements']), state.to_dict()['form']['line_length'])
        c = self.context
        self.assertEqual(c['imagery'], ['山', '月'])
        self.assertEqual(c['emotion'], ['宁静'])
        self.assertEqual(c['constraints'], ['不用愁字'])
        self.assertEqual(c['rhyme']['group'], '一东')
        self.assertEqual(c['template'], self.state.to_dict()['meter_template'])
        self.assertEqual(c['structure'], self.state.to_dict()['structure'])

    def test_couplet_and_counterpart(self):
        state = self.new_state('五言律诗')
        for line in ['水水天天水', '天天水水天', '天天天水水']:
            state.append_line(line)
        context = export_context(state, 4)
        self.assertEqual(context['couplet']['unit'], '颔联')
        self.assertEqual(context['couplet']['paired_text'], '天天天水水')
        self.assertEqual(context['couplet']['intents'], ['从远山写到近水'])
        self.assertEqual(context['lines'], state.to_dict()['lines'])

    def test_context_errors_and_missing_rhyme(self):
        for line in (True, 0, 5, '2'):
            self.assert_error('INVALID_LINE_INDEX', lambda: export_context(self.state, line))
        blank = PoemState.create({'raw_request': '山', 'theme': '山'})
        self.assert_error('TEMPLATE_UNBOUND', lambda: export_context(blank, 1))
        self.state.set_plan(rhyme={**self.state.to_dict()['rhyme'], 'group': None})
        context = export_context(self.state, 2)
        self.assertEqual(context['status'], 'planning_required')
        self.assertEqual(context['reasons'][0]['code'], 'RHYME_GROUP_UNSELECTED')
        self.assert_error('PLANNING_REQUIRED', lambda: filter_candidates(self.state, context, model_candidates(context)))
        self.assertEqual(export_context(self.state, 1)['status'], 'ready')
        self.assertIsNone(self.state.to_dict()['rhyme']['group'])

    def test_classification_and_only_passed_score_request(self):
        before = self.state.to_dict()
        filtered = self.filtered()
        self.assertEqual([r['category'] for r in filtered['records']], ['passed', 'passed', 'failed', 'unresolved'])
        self.assertEqual([r['id'] for r in filtered['score_request']['candidates']], ['c0', 'c1'])
        self.assertIn('context', filtered)
        self.assertNotIn('context', filtered['score_request'])
        self.assertEqual(self.state.to_dict(), before)
        self.assertTrue(filtered['records'][2]['reasons'])
        self.assertTrue(filtered['records'][3]['reasons'])

    def test_duplicate_review_is_candidate_bound_and_precedes_eligibility(self):
        engine = ConstraintEngine(RhymeLexicon(FIXTURE))
        filtered = filter_candidates(self.state, self.context,
                                     model_candidates(self.context, ['天地水火天']), engine=engine)
        record = filtered['records'][0]
        self.assertEqual(record['checks']['checks']['duplicate']['status'], 'unknown')
        self.assertEqual(filtered['duplicate_request']['reviews'][0]['id'], record['id'])
        self.assertNotIn(record['id'], [x['id'] for x in filtered['score_request']['candidates']])
        request = filtered['duplicate_request']['reviews'][0]['request']
        response = {'review_version': request['review_version'],
                    'request_sha256': request['request_sha256'],
                    'decisions': duplicate_decisions(request)}
        payload = {**{k: self.context[k] for k in ENVELOPE}, 'attempt': 1,
                   'filter_sha256': filtered['filter_sha256'],
                   'reviews': [{'id': record['id'], 'review': response}]}
        reviewed = review_duplicates(self.state, filtered, payload, engine=engine)
        self.assertEqual(reviewed['records'][0]['checks']['checks']['duplicate']['status'], 'pass')
        self.assertEqual(reviewed['records'][0]['category'], 'passed')
        stale_state = self.state.fork()
        stale_state.set_plan(imagery=['山', '水'], structure=[{'unit': '颔联', 'intent': '变化'}])
        with self.assertRaises(RoundError):
            review_duplicates(stale_state, filtered, payload, engine=engine)

    def test_one_engine_and_snapshot_no_append(self):
        before = self.state.to_dict()
        seen = []
        check = self.engine.check_candidate
        def observe(candidate, state, **kwargs):
            seen.append(state.to_dict())
            return check(candidate, state, **kwargs)
        with patch.object(self.engine, 'check_candidate', side_effect=observe) as mock:
            with patch('poetry_round.ConstraintEngine', return_value=self.engine) as factory:
                filter_candidates(self.state, self.context, model_candidates(self.context))
                factory.assert_called_once_with()
                self.assertEqual(mock.call_count, 4)
        self.assertTrue(all(d == before for d in seen))
        self.assertEqual(self.state.to_dict(), before)

    def test_peripheral_dedup_preserves_raw_and_interior(self):
        texts = [' 天地水火人。 ', '天地水火人', '天地，水火人', '天地 水火人']
        result = self.filtered(texts)
        self.assertEqual([r['category'] for r in result['records']], ['passed', 'duplicate', 'failed', 'passed'])
        self.assertEqual(result['records'][1]['duplicate_of'], 'c0')
        self.assertEqual(result['records'][0]['text'], texts[0])
        self.assertEqual(result['records'][0]['checks']['checks']['length']['lines'][0]['original'], texts[0])
        self.assertEqual(result['records'][2]['normalized_text'], texts[2])

    def test_entire_format_batch_rejected_before_engine(self):
        changes = [({'id': 'c0'}, 'DUPLICATE_CANDIDATE_ID'), ({'text': ''}, 'EMPTY_CANDIDATE_TEXT'),
                   ({'text': ' 。 '}, 'EMPTY_CANDIDATE_TEXT'), ({'text': 5}, 'EMPTY_CANDIDATE_TEXT'),
                   ({'line_index': 1}, 'WRONG_LINE_INDEX'), ({'line_index': True}, 'WRONG_LINE_INDEX'),
                   ({'id': []}, 'INVALID_CANDIDATE_ID'), ({'readings': {}}, 'INVALID_CANDIDATE_FIELDS')]
        before = self.state.to_dict()
        for changeset, code in changes:
            payload = model_candidates(self.context)
            payload['candidates'][1].update(changeset)
            with patch.object(self.engine, 'check_candidate') as mock:
                error = self.assert_error('CANDIDATE_FORMAT_ERROR', lambda: filter_candidates(
                    self.state, self.context, payload, engine=self.engine))
                self.assertIn(code, [x['code'] for x in error.details])
                mock.assert_not_called()
            self.assertEqual(self.state.to_dict(), before)

    def test_envelope_error_names_missing_and_unexpected_fields(self):
        payload = model_candidates(self.context)
        payload.pop('attempt')
        payload['extra'] = True
        error = self.assert_error('INVALID_ENVELOPE', lambda: filter_candidates(
            self.state, self.context, payload, engine=self.engine))
        self.assertEqual(error.details[0]['location'], 'response')
        self.assertIn('attempt', error.details[0]['missing_fields'])
        self.assertIn('extra', error.details[0]['unexpected_fields'])

    def test_budget_includes_duplicates_and_no_truncation(self):
        for texts in ([], ['天地水火人'] * 11):
            self.assert_error('CANDIDATE_BUDGET', lambda: self.filtered(texts))
        self.state.set_budget(candidates_per_round=1)
        context = export_context(self.state, 2)
        self.assertEqual(context['candidate_count']['target'], 1)
        self.assert_error('CANDIDATE_BUDGET', lambda: filter_candidates(self.state, context, model_candidates(context)))

    def test_explicit_readings_are_not_a_model_loophole(self):
        filtered = self.filtered(['天风水水天'])
        self.assertEqual(filtered['records'][0]['category'], 'unresolved')
        self.assertFalse(filtered['score_request']['candidates'])
        self.assertEqual([x['id'] for x in filtered['reading_request']['reviews']], ['c0'])
        reviewed = review_readings(self.state, filtered, model_readings(filtered), engine=self.engine)
        self.assertEqual(reviewed['records'][0]['category'], 'passed')
        self.assertEqual([x['id'] for x in reviewed['score_request']['candidates']], ['c0'])
        self.assertTrue(reviewed['records'][0]['reading_review']['complete'])
        payload = model_candidates(self.context)
        payload['readings'] = {'2:2': {'section': '上平声部'}}
        self.assert_error('INVALID_ENVELOPE', lambda: filter_candidates(self.state, self.context, payload))

    def test_score_validation_all_failure_modes_atomic(self):
        filtered = self.filtered()
        before = self.state.to_dict()
        cases = []
        for value in (-0.01, 1.01, True, '0.8', None, float('nan'), float('inf')):
            scores = model_scores(filtered)
            scores['scores'][0]['evaluation']['dimensions']['theme'] = value
            cases.append(scores)
        scores = model_scores(filtered); del scores['scores'][0]['evaluation']['dimensions']['theme']; cases.append(scores)
        scores = model_scores(filtered); scores['scores'].pop(); cases.append(scores)
        scores = model_scores(filtered); scores['scores'].append(deepcopy(scores['scores'][0])); cases.append(scores)
        for candidate_id in ('unknown', 'c2', 'c3'):
            scores = model_scores(filtered); scores['scores'][0]['id'] = candidate_id; cases.append(scores)
        scores = model_scores(filtered); scores['rubric_version'] = 'future'; cases.append(scores)
        scores = model_scores(filtered); scores['filter_sha256'] = 'a' * 64; cases.append(scores)
        scores = model_scores(filtered); scores['scores'][0]['evaluation']['total'] = 1; cases.append(scores)
        scores = model_scores(filtered); scores['scores'][0]['evaluation']['reason'] = ''; cases.append(scores)
        for scores in cases:
            with self.assertRaises(ValueError):
                finish_round(self.state, filtered, scores, engine=self.engine)
            self.assertEqual(self.state.to_dict(), before)

    def test_weighted_total_and_stable_ties(self):
        filtered = self.filtered()
        scores = model_scores(filtered)
        dims = dict(zip(WEIGHTS, [1, 0.8, 0.6, 0.4, 0.2]))
        for item in scores['scores']:
            item['evaluation']['dimensions'] = dims.copy()
        scores['scores'].reverse()  # tie order belongs to generation, never the scoring model
        report = finish_round(self.state, filtered, scores, engine=self.engine)
        self.assertEqual(report['ranking'], ['c0', 'c1'])
        self.assertEqual(report['recommended_id'], 'c0')
        self.assertEqual(report['records'][0]['quality_score'], 0.66)
        self.assertEqual(report['status'], 'completed')

    def test_missing_score_is_null_and_partial_result(self):
        filtered = self.filtered()
        scores = model_scores(filtered)
        scores['scores'][0]['evaluation'] = None
        report = finish_round(self.state, filtered, scores, engine=self.engine)
        self.assertEqual(report['ranking'], ['c1'])
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['missing_scores'], ['c0'])
        self.assertIsNone(report['records'][0]['quality_score'])
        self.assertEqual(report['records'][0]['decision'], 'pending')

    def test_zero_is_evaluated_and_higher_total_ranks_first(self):
        filtered = self.filtered()
        scores = model_scores(filtered, 0)
        scores['scores'][1]['evaluation']['dimensions']['theme'] = 1
        report = finish_round(self.state, filtered, scores, engine=self.engine)
        self.assertEqual(report['ranking'], ['c1', 'c0'])
        self.assertEqual(report['records'][0]['quality_score'], 0)
        self.assertEqual(report['records'][0]['decision'], 'retained')
        self.assertEqual(report['records'][1]['quality_score'], 0.25)

    def test_parallelism_is_optional_model_observation_for_lushi(self):
        self.state = self.new_state('五言律诗')
        self.context = export_context(self.state, 2)
        filtered = self.filtered()
        scores = model_scores(filtered)
        scores['scores'][0]['evaluation']['parallelism_observation'] = '对应句尚缺，当前无法判断语义对仗。'
        report = finish_round(self.state, filtered, scores, engine=self.engine)
        self.assertIn('parallelism_observation', report['records'][0]['literary_evaluation'])
        self.assertEqual(report['records'][0]['checks'], filtered['records'][0]['checks'])

    def test_json_publication_is_atomic_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'result.json'
            with patch('poetry_round.os.link', side_effect=OSError('controlled publication failure')):
                with self.assertRaises(OSError): write_new_json(target, {'state': 'complete'})
            self.assertEqual(list(Path(tmp).iterdir()), [])
            write_new_json(target, {'state': 'complete'})
            with self.assertRaises(FileExistsError): write_new_json(target, {'state': 'replacement'})
            self.assertEqual(read_json(target.read_text(encoding='utf-8')), {'state': 'complete'})
            self.assertEqual(list(Path(tmp).iterdir()), [target])

    def test_unavailable_reasons(self):
        for texts, code in [(['天水水水天'], 'ALL_CANDIDATES_FAILED'),
                            (['天风水水天'], 'ALL_CANDIDATES_UNRESOLVED'),
                            (['天水水水天', '天风水水天'], 'NO_PASSED_CANDIDATES'),
                            (['天地水火人'], 'SCORING_MISSING')]:
            state = self.new_state()
            context = export_context(state, 2)
            filtered = filter_candidates(state, context, model_candidates(context, texts), engine=self.engine)
            report = finish_round(state, filtered, engine=self.engine)
            self.assertEqual(report['code'], code)
            self.assertIsNone(report['recommended_id'])
            self.assertEqual(report['ranking'], [])
            self.assertEqual(len(state.to_dict()['candidate_history']), len(texts))

    def test_cross_batch_and_same_revision_different_context(self):
        other_context = export_context(self.state, 2)
        self.assertNotEqual(self.context['batch_id'], other_context['batch_id'])
        self.assert_error('WRONG_BATCH', lambda: filter_candidates(self.state, other_context, model_candidates(self.context)))
        for field in ('lines', 'imagery', 'rhyme', 'constraints', 'meter_template'):
            left, right = self.state.fork(), self.state.fork()
            if field == 'lines':
                left.append_line('月'); right.append_line('水')
            elif field == 'meter_template':
                t = self.state.to_dict()['meter_template']['data']
                left.bind_template(t, 'a' * 64); right.bind_template(t, 'b' * 64)
            elif field == 'rhyme':
                left.set_plan(rhyme={**left.to_dict()['rhyme'], 'group': '二冬'})
                right.set_plan(rhyme={**right.to_dict()['rhyme'], 'group': '三江'})
            else:
                left.set_plan(**{field: ['甲']}); right.set_plan(**{field: ['乙']})
            self.assertEqual(left.to_dict()['revision'], right.to_dict()['revision'])
            c = export_context(left, 2)
            f = filter_candidates(left, c, model_candidates(c), engine=self.engine)
            before = right.to_dict()
            self.assert_error('STALE_CONTEXT', lambda: filter_candidates(right, c, model_candidates(c)))
            self.assert_error('STALE_CONTEXT', lambda: finish_round(right, f, model_scores(f), engine=self.engine))
            self.assertEqual(right.to_dict(), before)
        first, second = self.filtered(), filter_candidates(self.state, other_context, model_candidates(other_context), engine=self.engine)
        self.assert_error('WRONG_BATCH', lambda: validate_scores(first, model_scores(second)))

    def test_edit_and_budget_invalidate_old_artifacts(self):
        for change in (lambda s: s.append_line('月'), lambda s: s.set_plan(theme='水'),
                       lambda s: s.set_plan(structure=[{'unit': '第一句', 'intent': '新规划'}]),
                       lambda s: s.set_budget(candidates_per_round=3)):
            state = self.state.fork()
            filtered = self.filtered()
            change(state)
            before = state.to_dict()
            self.assert_error('STALE_CONTEXT', lambda: finish_round(state, filtered, model_scores(filtered), engine=self.engine))
            self.assertEqual(state.to_dict(), before)

    def test_filter_tampering_and_model_rule_overwrite_rejected(self):
        filtered = self.filtered()
        scores = model_scores(filtered)
        filtered['records'][2]['category'] = 'passed'
        self.assert_error('FILTER_MISMATCH', lambda: finish_round(self.state, filtered, scores, engine=self.engine))
        scores = model_scores(self.filtered()); scores['checks'] = {'valid': True}
        self.assert_error('INVALID_ENVELOPE', lambda: validate_scores(self.filtered(), scores))
        self.assertEqual(self.state.to_dict()['candidate_history'], [])

    def test_finish_history_and_no_poem_mutations(self):
        before = self.state.to_dict()
        filtered = self.filtered(['天地水火人', '天地水火人。', '天水水水天', '天风水水天'])
        report = finish_round(self.state, filtered, model_scores(filtered), engine=self.engine)
        after = self.state.to_dict()
        self.assertEqual({k: v for k, v in before.items() if k != 'candidate_history'},
                         {k: v for k, v in after.items() if k != 'candidate_history'})
        self.assertEqual([c['decision'] for c in after['candidate_history']], ['retained', 'rejected', 'rejected', 'pending'])
        for record, history in zip(report['records'], after['candidate_history']):
            self.assertEqual(record['text'], history['text'])
            self.assertEqual(record['history_id'], history['id'])
            self.assertEqual(record['checks'] or {}, history['checks'])
            self.assertIn(self.context['batch_id'], history['parent_path'])
            self.assertFalse(json.loads(history['reason'])['adopted'])
        self.assertIsNone(report['adopted_id'])
        self.assert_error('BATCH_ALREADY_RECORDED', lambda: finish_round(self.state, filtered, model_scores(filtered), engine=self.engine))
        self.assertEqual(self.state.to_dict(), after)
        self.state.append_line('月')
        self.assertEqual(PoemState.from_dict(self.state.to_dict()).to_dict(), self.state.to_dict())

    def test_storage_id_collision_has_no_partial_write(self):
        filtered = self.filtered()
        collision = self.context['batch_id'] + ':c3'
        self.state.record_candidate(collision, '月', 2)
        before = self.state.to_dict()
        with self.assertRaises(ValueError):
            finish_round(self.state, filtered, model_scores(filtered), engine=self.engine)
        self.assertEqual(self.state.to_dict(), before)

    def test_atomic_batch_api_refuses_wrong_result_scope(self):
        filtered = self.filtered()
        record = filtered['records'][0]
        item = {'id': record['history_id'], 'text': record['text'], 'line_index': 2,
                'parent_path': 'test', 'checks': record['checks'], 'quality_score': 0.8,
                'decision': 'retained', 'reason': 'test'}
        wrong = deepcopy(item); wrong['id'] = 'other'
        before = self.state.to_dict()
        with self.assertRaises(ValueError): self.state.record_candidate_batch([item, wrong])
        self.assertEqual(self.state.to_dict(), before)
        wrong['checks'] = self.engine.check_poem(self.state)
        with self.assertRaises(ValueError): self.state.record_candidate_batch([wrong])
        self.assertEqual(self.state.to_dict(), before)

    def test_json_parser_and_bounded_attempts(self):
        for raw in ('{', '{"a": 1, "a": 2}', '{"a": NaN}', '{"a": 1e999}'):
            with self.assertRaises(RoundError): read_json(raw)
        for attempt in (0, 4, True, '1'):
            candidates = model_candidates(self.context); candidates['attempt'] = attempt
            self.assert_error('RETRY_EXHAUSTED', lambda: filter_candidates(self.state, self.context, candidates))
        candidates = model_candidates(self.context); candidates['attempt'] = 3
        self.assertEqual(len(filter_candidates(self.state, self.context, candidates, engine=self.engine)['records']), 4)

    def test_relocation_full_cli_outside_project(self):
        project = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / 'relocated'
            shutil.copytree(project, copied, ignore=shutil.ignore_patterns('__pycache__', '.day3-test-*'))
            workspace = Path(tmp) / 'workspace'
            files = workspace / 'examples' / 'runtime'
            files.mkdir(parents=True)
            self.state.save(files / 'state.json')
            script = copied / 'scripts' / 'poetry_round.py'
            def run(*args, check=True):
                return subprocess.run([sys.executable, str(script), *args, '--work-dir', str(workspace)], cwd=project.parent,
                                      capture_output=True, check=check)
            def load(name):
                return read_json((files / name).read_text(encoding='utf-8'))
            def save(name, value):
                (files / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            run('export', '--state', 'examples/runtime/state.json', '--line', '2', '--output', 'examples/runtime/context.json')
            context = load('context.json')
            save('candidates.json', model_candidates(context, ['风红东孔同', '风孔东火同', '风红重孔同']))
            run('filter', '--state', 'examples/runtime/state.json', '--context', 'examples/runtime/context.json',
                '--candidates', 'examples/runtime/candidates.json', '--output', 'examples/runtime/filter.json')
            filtered = load('filter.json')
            self.assertEqual([r['category'] for r in filtered['records']], ['passed', 'failed', 'unresolved'])
            save('scores.json', model_scores(filtered))
            run('finish', '--state', 'examples/runtime/state.json', '--filtered', 'examples/runtime/filter.json',
                '--scores', 'examples/runtime/scores.json', '--output', 'examples/runtime/result.json')
            result = load('result.json')
            self.assertEqual(result['report']['ranking'], ['c0'])
            self.assertEqual(result['state']['lines'], [])
            self.assertEqual(len(PoemState.from_dict(result['state']).to_dict()['candidate_history']), 3)
            # A bundle can be used directly as the next CLI input; reject Skill-tree output.
            run('export', '--state', 'examples/runtime/result.json', '--line', '2', '--output', 'examples/runtime/context-next.json')
            bad = run('export', '--state', 'examples/runtime/state.json', '--line', '2',
                      '--output', str(copied / 'escape.json'), check=False)
            self.assertEqual(bad.returncode, 2)
            self.assertEqual(json.loads(bad.stderr)['code'], 'INVALID_INPUT')
            self.assertFalse((copied / 'escape.json').exists())


if __name__ == '__main__':
    unittest.main()
