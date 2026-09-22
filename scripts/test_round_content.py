"""Controlled agent reports test content integration, not semantic truth."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from assessment_binding import digest
from allusion_lexicon import AllusionLexicon
from constraint_engine import ConstraintEngine
from poem_state import PoemState
from poem_templates import TemplateLibrary
from poetry_round import (ENVELOPE, export_context, filter_candidates, finish_round,
                          review_content, validate_scores, read_json)
from round_content import (CONTENT_VERSION, ContentError, export_content_plan,
                           read_content_fulfillment)
from rhyme_lexicon import RhymeLexicon
from test_poetry_round import FIXTURE, model_candidates, model_scores


def fragment(rid='f1', text='天地', mode='exact', kind='phrase', line=2):
    return {'id': rid, 'text': text, 'kind': kind, 'mode': mode, 'line_index': line,
            'source_excerpt': '引用「' + text + '」'}


def allusion(rid='a1', required=True, name='庄周梦蝶'):
    return {'id': rid, 'name': name, 'description': '', 'required': required,
            'source_excerpt': '用「' + name + '」'}


def state_with(fragments=None, allusions=None):
    request = {'raw_request': '写山中五绝', 'theme': '山中', 'poem_type': '五言绝句',
               'meter': {'start': '仄起', 'first_line_rhymes': False}}
    for key, values in [('required_fragments', fragments), ('allusions', allusions)]:
        if values is not None:
            request[key] = deepcopy(values)
            request['raw_request'] += ''.join(x['source_excerpt'] for x in values)
    state = PoemState.create(request)
    TemplateLibrary().bind(state)
    state.set_plan(rhyme={'system': '平水韵', 'group': '一东', 'first_line_rhymes': False})
    return state


def planned(state, positions):
    plan = export_content_plan(state)
    for item in plan['assignments']:
        key = (item['kind'], item['requirement_id'])
        if key in positions:
            item.update(line_index=positions[key], reason='模型安排：在此句承接意象')
    return plan


def semantic_payload(filtered, status='satisfied'):
    request = filtered['content_request']
    return {**{k: request[k] for k in ENVELOPE}, 'filter_sha256': request['filter_sha256'],
            'content_version': CONTENT_VERSION, 'attempt': 1,
            'reviews': [{'id': c['id'], 'line_index': c['line_index'], 'text_sha256': c['text_sha256'],
                         'requirements': [{'kind': r['kind'], 'requirement_id': r['requirement_id'],
                                           'status': status, 'reason': '固定语义评价夹具；并非程序判断典故。',
                                           'interpretation': '以此候选的表达承接用户意象与主要意思。' if status == 'satisfied' else '',
                                           'evidence': [{'line_index': c['line_index'], 'quote': c['text']}] if status == 'satisfied' else []}
                                          for r in c['requirements']]}
                        for c in request['candidates']]}


class ContentRoundTests(unittest.TestCase):
    def setUp(self):
        self.engine = ConstraintEngine(RhymeLexicon(FIXTURE), duplicate_policy='report')

    def batch(self, state, texts=None, positions=None, line=2):
        plan = planned(state, positions) if positions is not None else None
        context = export_context(state, line, content_plan=plan)
        payload = None if context['locked_candidate'] else model_candidates(context, texts or ['天地水火人'])
        return filter_candidates(state, context, payload, engine=self.engine)

    def reviewed(self, state, filtered, status='satisfied'):
        return review_content(state, filtered, semantic_payload(filtered, status), engine=self.engine)

    def test_full_arrays_user_provenance_and_model_plan_are_separate(self):
        state = state_with([fragment(line=None)], [allusion(), allusion('a2', False)])
        before = state.to_dict()
        context = export_context(state, 2)
        self.assertEqual(context['status'], 'planning_required')
        self.assertEqual(context['allusions'], before['request']['allusions'])
        self.assertEqual(context['required_fragments'], before['request']['required_fragments'])
        self.assertEqual([a['required'] for a in context['allusions']], [True, False])
        plan = planned(state, {('fragment', 'f1'): 2, ('allusion', 'a1'): 4})
        context = export_context(state, 2, content_plan=plan)
        self.assertEqual(context['status'], 'ready')
        self.assertIsNone(context['required_fragments'][0]['line_index'])
        self.assertEqual(context['content_plan']['assignments'][0]['line_index'], 2)
        context['allusions'][0]['source_excerpt'] = '模型解释'
        self.assertEqual(state.to_dict(), before)

    def test_resolved_allusion_reference_is_attached_without_rewriting_request(self):
        state = state_with(allusions=[allusion(name='王质观棋')])
        before = state.to_dict()
        filtered = self.batch(state, positions={('allusion', 'a1'): 2})
        reference = filtered['context']['allusion_references'][0]
        self.assertEqual(reference['status'], 'resolved')
        self.assertEqual(reference['record']['canonical_name'], '柯烂忘归')
        semantic = filtered['content_request']['candidates'][0]['requirements'][0]
        self.assertEqual(semantic['allusion_reference'], reference)
        self.assertEqual(state.to_dict(), before)

    def test_required_ambiguous_allusion_blocks_planning_and_optional_cannot_fake_satisfaction(self):
        def item(aid, name):
            return {'allusion_id': aid, 'page_no': 0, 'seq_in_page': aid,
                    'raw_nav_name': name + '（甲）', 'name': name,
                    'head_persons': ['甲'], 'aliases': [{'word': '同名', 'word_id': None}],
                    'persons': ['甲'], 'refs': [],
                    'sources': [{'book': '出处', 'text': '原文'}], 'gloss': ''}
        import poetry_round
        old = poetry_round._ALLUSION_LEXICON
        poetry_round._ALLUSION_LEXICON = AllusionLexicon([item(1, '甲典'), item(2, '乙典')])
        self.addCleanup(setattr, poetry_round, '_ALLUSION_LEXICON', old)
        required = state_with(allusions=[allusion(name='同名')])
        context = export_context(required, 2, content_plan=planned(required, {('allusion', 'a1'): 2}))
        self.assertEqual(context['status'], 'planning_required')
        self.assertIn('ALLUSION_DISAMBIGUATION_REQUIRED', {x['code'] for x in context['reasons']})
        optional = state_with(allusions=[allusion(name='同名', required=False)])
        filtered = self.batch(optional)
        payload = semantic_payload(filtered, 'satisfied')
        with self.assertRaisesRegex(ValueError, 'Ambiguous allusion'):
            review_content(optional, filtered, payload, engine=self.engine)

    def test_plan_must_cover_requirements_and_preserve_explicit_positions(self):
        state = state_with([fragment()], [allusion()])
        plan = planned(state, {('allusion', 'a1'): 4})
        cases = []
        bad = deepcopy(plan); bad['assignments'].pop(); cases.append(bad)
        bad = deepcopy(plan); bad['assignments'].append(deepcopy(bad['assignments'][0])); cases.append(bad)
        for index in (1, 0, 5, True):
            bad = deepcopy(plan); bad['assignments'][0]['line_index'] = index; cases.append(bad)
        bad = deepcopy(plan); bad['assignments'][0]['source_excerpt'] = '解释'; cases.append(bad)
        for bad in cases:
            with self.assertRaises(ValueError): export_context(state, 2, content_plan=bad)

    def test_assigned_exact_conflicts_reuse_input_structure_checks(self):
        state = state_with([fragment('f1', '天地水火人', kind='line', line=None),
                            fragment('f2', '人天水火地', kind='line', line=None)])
        plan = planned(state, {('fragment', 'f1'): 2, ('fragment', 'f2'): 2})
        with self.assertRaisesRegex(ContentError, '同一句位'):
            export_context(state, 2, content_plan=plan)
        state = state_with([fragment(mode='inspiration', line=None)])
        with self.assertRaises(ContentError):
            export_context(state, 2, content_plan=planned(state, {('fragment', 'f1'): 2}))

    def test_exact_line_bypasses_model_generation_and_preserves_raw(self):
        original = ' 天地水火人。 '
        state = state_with([fragment(text=original, kind='line')])
        before = state.to_dict()
        context = export_context(state, 2)
        self.assertEqual(context['locked_candidate']['text'], original)
        filtered = filter_candidates(state, context, engine=self.engine)
        record = filtered['records'][0]
        self.assertEqual(record['text'], original)
        self.assertTrue(record['checks']['valid'])
        self.assertEqual(record['content_result']['requirements'][0]['status'], 'satisfied')
        with self.assertRaisesRegex(ValueError, 'locked'):
            filter_candidates(state, context, model_candidates(context, ['天地水火地']), engine=self.engine)
        self.assertEqual(state.to_dict(), before)

    def test_unpositioned_exact_line_is_locked_after_arrangement(self):
        state = state_with([fragment(text='天地水火人', kind='line', line=None)])
        filtered = self.batch(state, positions={('fragment', 'f1'): 2})
        self.assertEqual(filtered['records'][0]['text'], '天地水火人')
        self.assertEqual(filtered['records'][0]['content_result']['requirements'][0]['arrangement_source'], 'agent_plan')
        self.assertIsNone(state.to_dict()['request']['required_fragments'][0]['line_index'])

    def test_exact_rule_conflict_reports_user_adjustment_unknown_stays_unknown(self):
        for text, valid, code in [('天水水水天', False, 'LOCKED_EXACT_RULE_CONFLICT'),
                                   ('天风水水天', None, 'ALL_CANDIDATES_UNRESOLVED')]:
            state = state_with([fragment(text=text, kind='line')])
            filtered = self.batch(state)
            self.assertIs(filtered['records'][0]['checks']['valid'], valid)
            self.assertTrue(filtered['records'][0]['content_result']['valid'])
            self.assertFalse(filtered['score_request']['candidates'])
            report = finish_round(state, filtered, engine=self.engine)
            self.assertEqual(report['code'], code)
            self.assertIsNone(report['recommended_id'])
            self.assertEqual(state.to_dict()['lines'], [])

    def test_missing_changed_reordered_phrase_blocks_meter_pass(self):
        state = state_with([fragment()])
        filtered = self.batch(state, ['人天水火地', '天人水火地', '地天水火人'])
        for r in filtered['records']:
            self.assertTrue(r['checks']['valid'])
            self.assertEqual(r['category'], 'passed')
            self.assertFalse(r['content_result']['valid'])
            self.assertEqual(r['content_result']['requirements'][0]['status'], 'violated')
            self.assertNotIn('content', r['checks']['checks'])
        self.assertFalse(filtered['score_request']['candidates'])
        report = finish_round(state, filtered, engine=self.engine)
        self.assertEqual(report['code'], 'CONTENT_REQUIREMENTS_VIOLATED')
        self.assertTrue(all(r['decision'] == 'rejected' for r in report['records']))

    def test_exact_normalization_uses_existing_whitespace_punctuation_only(self):
        state = state_with([fragment(text='天 地')])
        filtered = self.batch(state, [' 天地水火人。 ', '天 地水火人', '天地，水火人'])
        self.assertEqual([r['content_result']['valid'] for r in filtered['records']], [True, True, False])
        self.assertEqual(filtered['records'][0]['text'], ' 天地水火人。 ')

    def test_adapt_uses_semantics_without_literal_matching(self):
        state = state_with([fragment(text='水火', mode='adapt')])
        filtered = self.batch(state, ['天地土土人'])
        self.assertNotIn('水火', filtered['records'][0]['text'])
        self.assertIsNone(filtered['records'][0]['content_result']['valid'])
        self.assertFalse(filtered['score_request']['candidates'])
        reviewed = self.reviewed(state, filtered)
        self.assertTrue(reviewed['records'][0]['content_result']['valid'])
        report = finish_round(state, reviewed, model_scores(reviewed), engine=self.engine)
        self.assertEqual(report['ranking'], ['c0'])
        self.assertEqual(report['records'][0]['content_result']['requirements'][0]['method'], 'llm')

    def test_inspiration_and_optional_allusion_do_not_block(self):
        state = state_with([fragment(text='水火', mode='inspiration', line=None)], [allusion(required=False)])
        filtered = self.batch(state, ['天地土土人'])
        self.assertTrue(filtered['records'][0]['content_result']['valid'])
        self.assertEqual(len(filtered['content_request']['candidates'][0]['requirements']), 2)
        reviewed = self.reviewed(state, filtered, 'violated')
        self.assertTrue(reviewed['records'][0]['content_result']['valid'])
        report = finish_round(state, reviewed, model_scores(reviewed), engine=self.engine)
        self.assertEqual(report['ranking'], ['c0'])
        self.assertTrue(all(not r['blocking'] for r in report['records'][0]['content_result']['requirements']))

    def test_future_requirements_pending_do_not_reject_current_line(self):
        state = state_with([fragment(text='冬', line=4)], [allusion()])
        filtered = self.batch(state, positions={('allusion', 'a1'): 4})
        reqs = filtered['records'][0]['content_result']['requirements']
        self.assertEqual([x['status'] for x in reqs], ['pending', 'pending'])
        self.assertTrue(filtered['records'][0]['content_result']['valid'])
        self.assertFalse(filtered['content_request']['candidates'])
        report = finish_round(state, filtered, model_scores(filtered), engine=self.engine)
        self.assertEqual(report['ranking'], ['c0'])

    def test_required_allusion_name_is_not_semantic_evidence(self):
        state = state_with(allusions=[allusion(name='天地')])
        filtered = self.batch(state, positions={('allusion', 'a1'): 2})
        self.assertIn('天地', filtered['records'][0]['text'])
        self.assertIsNone(filtered['records'][0]['content_result']['valid'])
        self.assertFalse(filtered['score_request']['candidates'])
        report = finish_round(state, filtered, engine=self.engine)
        self.assertEqual(report['code'], 'CONTENT_REQUIREMENTS_UNKNOWN')
        self.assertIsNone(report['recommended_id'])

    def test_required_allusion_omission_cannot_be_averaged_away(self):
        state = state_with(allusions=[allusion()])
        filtered = self.batch(state, positions={('allusion', 'a1'): 2})
        reviewed = self.reviewed(state, filtered, 'violated')
        forged = model_scores(reviewed, 1)
        forged['scores'] = model_scores(self.reviewed(state, filtered), 1)['scores']
        before = state.to_dict()
        with self.assertRaisesRegex(ValueError, 'Only passed candidates'):
            finish_round(state, reviewed, forged, engine=self.engine)
        self.assertEqual(state.to_dict(), before)
        report = finish_round(state, reviewed, engine=self.engine)
        self.assertEqual(report['code'], 'CONTENT_REQUIREMENTS_VIOLATED')
        self.assertEqual(report['ranking'], [])

    def test_typed_requirement_ids_can_overlap_between_arrays(self):
        state = state_with([fragment('same', mode='adapt')], [allusion('same')])
        filtered = self.batch(state, positions={('allusion', 'same'): 2})
        reviewed = self.reviewed(state, filtered)
        reqs = reviewed['records'][0]['content_result']['requirements']
        self.assertEqual({(x['kind'], x['requirement_id']) for x in reqs}, {('fragment', 'same'), ('allusion', 'same')})

    def test_semantic_format_evidence_and_ownership_validation_atomic(self):
        state = state_with(allusions=[allusion()])
        filtered = self.batch(state, ['天地水火人', '人天火水地'], {('allusion', 'a1'): 2})
        good = semantic_payload(filtered)
        cases = []
        bad = deepcopy(good); bad['reviews'].pop(); cases.append(bad)
        bad = deepcopy(good); bad['reviews'].append(deepcopy(bad['reviews'][0])); cases.append(bad)
        bad = deepcopy(good); bad['reviews'][0]['id'] = 'absent'; cases.append(bad)
        bad = deepcopy(good); bad['reviews'][0]['text_sha256'] = digest('其他候选'); cases.append(bad)
        bad = deepcopy(good); bad['reviews'][0]['line_index'] = 3; cases.append(bad)
        bad = deepcopy(good); bad['filter_sha256'] = '0' * 64; cases.append(bad)
        bad = deepcopy(good); bad['reviews'][0]['requirements'] = []; cases.append(bad)
        for key, value in [('status', 'pending'), ('requirement_id', 'absent'), ('reason', ''),
                            ('interpretation', ''), ('evidence', []),
                            ('evidence', [{'line_index': 2, 'quote': '不在原句'}])]:
            bad = deepcopy(good); bad['reviews'][0]['requirements'][0][key] = value; cases.append(bad)
        bad = deepcopy(good); bad['reviews'][0]['requirements'].append(deepcopy(bad['reviews'][0]['requirements'][0])); cases.append(bad)
        bad = deepcopy(good); bad['attempt'] = 4; cases.append(bad)
        before = state.to_dict()
        for bad in cases:
            with self.assertRaises(ValueError): review_content(state, filtered, bad, engine=self.engine)
            self.assertEqual(state.to_dict(), before)

    def test_semantic_unknown_is_preserved_not_fabricated(self):
        state = state_with(allusions=[allusion(name='含义不明的典故')])
        filtered = self.batch(state, positions={('allusion', 'a1'): 2})
        reviewed = self.reviewed(state, filtered, 'unknown')
        req = reviewed['records'][0]['content_result']['requirements'][0]
        self.assertEqual(req['status'], 'unknown')
        self.assertEqual(req['evidence'], [])
        report = finish_round(state, reviewed, engine=self.engine)
        self.assertEqual(report['code'], 'CONTENT_REQUIREMENTS_UNKNOWN')

    def test_semantics_and_scores_bound_to_batch_and_review_version(self):
        state = state_with(allusions=[allusion()])
        filtered = self.batch(state, positions={('allusion', 'a1'): 2})
        other = self.batch(state, positions={('allusion', 'a1'): 2})
        with self.assertRaises(ValueError): review_content(state, other, semantic_payload(filtered), engine=self.engine)
        reviewed = self.reviewed(state, filtered)
        rejected = self.reviewed(state, filtered, 'violated')
        with self.assertRaises(ValueError): validate_scores(rejected, model_scores(reviewed, 1))
        tampered = deepcopy(reviewed)
        tampered['records'][0]['content_result']['requirements'][0]['interpretation'] = '篡改'
        with self.assertRaises(ValueError): finish_round(state, tampered, model_scores(reviewed), engine=self.engine)

    def test_replacing_responsible_line_invalidates_fulfillment(self):
        state = state_with([fragment()])
        state.append_line('水水天天水')
        state.append_line('天地水火人')
        filtered = self.batch(state)
        report = finish_round(state, filtered, model_scores(filtered), engine=self.engine)
        history_id = report['records'][0]['history_id']
        self.assertEqual(read_content_fulfillment(state, history_id)['requirements'][0]['status'], 'satisfied')
        state.replace_line(2, '人天火水地')
        with self.assertRaisesRegex(ContentError, 'context changed'): read_content_fulfillment(state, history_id)
        with self.assertRaises(ValueError): finish_round(state, filtered, model_scores(filtered), engine=self.engine)
        # A later round recomputes exact fulfillment from current text, not history.
        refreshed = self.batch(state, ['天天天水水'], line=3)
        requirement = refreshed['records'][0]['content_result']['requirements'][0]
        self.assertEqual(requirement['status'], 'violated')
        self.assertFalse(requirement['blocking'])
        self.assertEqual(PoemState.from_dict(state.to_dict()).to_dict(), state.to_dict())

    def test_changed_requirements_and_same_revision_branches_reject_old_results(self):
        state = state_with(allusions=[allusion()])
        filtered = self.batch(state, positions={('allusion', 'a1'): 2})
        reviewed = self.reviewed(state, filtered)
        changed = state_with(allusions=[allusion(name='另一个典故')])
        self.assertEqual(state.to_dict()['revision'], changed.to_dict()['revision'])
        for other in [changed]:
            before = other.to_dict()
            with self.assertRaises(ValueError): review_content(other, filtered, semantic_payload(filtered), engine=self.engine)
            with self.assertRaises(ValueError): finish_round(other, reviewed, model_scores(reviewed), engine=self.engine)
            self.assertEqual(other.to_dict(), before)
        left, right = state.fork(), state.fork()
        left.set_plan(imagery=['月']); right.set_plan(imagery=['风'])
        f = self.batch(left, positions={('allusion', 'a1'): 2})
        with self.assertRaises(ValueError): review_content(right, f, semantic_payload(f), engine=self.engine)

    def test_old_state_absent_fields_remains_unchanged(self):
        state = state_with()
        before = state.to_dict()
        context = export_context(state, 2)
        self.assertEqual(context['allusions'], [])
        self.assertEqual(context['required_fragments'], [])
        filtered = self.batch(state)
        self.assertNotIn('content_request', filtered)
        self.assertEqual(PoemState.from_dict(before).to_dict(), before)
        self.assertEqual(state.to_dict(), before)
        finish_round(state, filtered, model_scores(filtered), engine=self.engine)
        self.assertNotIn('allusions', state.to_dict()['request'])
        self.assertNotIn('required_fragments', state.to_dict()['request'])

    def test_relocated_cli_planning_review_and_fulfillment(self):
        project = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / 'relocated'
            shutil.copytree(project, copied, ignore=shutil.ignore_patterns('__pycache__', '.day3-test-*'))
            workspace = Path(tmp) / 'workspace'
            files = workspace / 'examples' / 'content-runtime'; files.mkdir(parents=True)
            state = state_with([fragment(text='风红东孔同', kind='line', line=None)], [allusion()])
            state.save(files / 'state.json')
            script = copied / 'scripts' / 'poetry_round.py'
            def run(*args):
                subprocess.run([sys.executable, str(script), *args, '--work-dir', str(workspace)], cwd=project.parent,
                               capture_output=True, check=True)
            def read(name):
                return read_json((files / name).read_text(encoding='utf-8'))
            def write(name, data):
                (files / name).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            root = 'examples/content-runtime/'
            run('plan', '--state', root + 'state.json', '--output', root + 'plan.json')
            plan = read('plan.json')
            for item in plan['assignments']: item.update(line_index=2, reason='模型安排于第二句')
            write('arranged.json', plan)
            run('export', '--state', root + 'state.json', '--line', '2', '--content-plan', root + 'arranged.json', '--output', root + 'context.json')
            run('filter', '--state', root + 'state.json', '--context', root + 'context.json', '--output', root + 'filtered.json')
            filtered = read('filtered.json')
            self.assertEqual(filtered['records'][0]['text'], '风红东孔同')
            write('reviews.json', semantic_payload(filtered))
            run('review-content', '--state', root + 'state.json', '--filtered', root + 'filtered.json',
                '--reviews', root + 'reviews.json', '--output', root + 'reviewed.json')
            reviewed = read('reviewed.json'); write('scores.json', model_scores(reviewed))
            run('finish', '--state', root + 'state.json', '--filtered', root + 'reviewed.json',
                '--scores', root + 'scores.json', '--output', root + 'result.json')
            result = read('result.json')
            self.assertEqual(result['report']['ranking'], ['locked:f1'])
            restored = PoemState.from_dict(result['state'])
            fulfillment = read_content_fulfillment(restored, result['report']['records'][0]['history_id'])
            self.assertEqual([r['status'] for r in fulfillment['requirements']], ['satisfied', 'satisfied'])
            self.assertEqual(restored.to_dict()['lines'], [])


if __name__ == '__main__':
    unittest.main()
