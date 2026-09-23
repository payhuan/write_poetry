"""Day 5 controlled responses: orchestration and proof ownership, not literary quality."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from assessment_binding import digest, make_binding
from poem_state import PoemState
from poem_templates import TemplateLibrary
from poetry_search import Search
from poetry_polish import Polish, REDLINE_CHECKS, validate_review, validate_redline
from poetry_round import export_context
from round_content import export_content_plan
from rhyme_lexicon import RhymeLexicon
from day4_example import fixture_state, fixture_engine, fixture_plan, drive, line_for
from day5_example import (review_for, package_for, envelope, comparison_for, content_for,
                          redline_for, no_change_for, run_example)
from word_refinement import VERSION as REFINEMENT_VERSION


class PolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        state = fixture_state()
        cls.source = drive(Search.create(state, poem_plan=fixture_plan(state), engine=fixture_engine())).to_dict()
        cls.path = cls.source['search']['drafts'][0]['path_id']

    def create(self, **config):
        return Polish.create(self.source, self.path, engine=fixture_engine(), **config)

    def send(self, p, payload):
        return p.submit(envelope(p.next_job(), payload))

    def revise(self, p, positions=(1,)):
        self.send(p, review_for(p.data['accepted'], revise=True, positions=positions))
        self.assertEqual(p.next_job()['stage'], 'generate')

    def generate(self, p, package=None, positions=(1,)):
        self.send(p, dict(packages=[package or package_for(p.data['accepted'], positions=positions)],
                          needs_user_change=False, reason='固定候选'))

    def finish(self, p):
        self.reach_redline(p)
        self.send(p, redline_for(p.data['accepted']))
        self.assertEqual(p.next_job()['stage'], 'final')
        self.send(p, dict(title='测试题目', notes=[]))
        self.assertEqual(p.data['status'], 'completed', p.data['reason'])

    def reach_redline(self, p):
        while p.next_job() and p.next_job()['stage'] in ('review', 'refinement', 'final_content'):
            if p.next_job()['stage'] == 'review':
                self.send(p, review_for(p.data['accepted']))
            elif p.next_job()['stage'] == 'refinement':
                self.send(p, no_change_for(p.next_job()))
            else:
                self.send(p, content_for(p.next_job()))
        self.assertEqual(p.next_job()['stage'], 'redline')

    def refinement_for(self, job, status='REFINED'):
        request = job['context']['refinement_request']
        if status == 'REFLOW_REQUIRED':
            return {'refinement_version': REFINEMENT_VERSION,
                    'request_sha256': request['request_sha256'], 'status': status,
                    'reason': '局部换字不足以修复语流', 'result': {
                        'problem_type': '结构性语流问题', 'protected_content': ['保持原意'],
                        'suggested_scope': 'couplet' if len(request['original_lines']) == 2 else 'line'}}
        lines = deepcopy(request['original_lines'])
        old = lines[0]['text'][0]
        replacement = {'天': '地', '地': '天', '人': '天', '水': '火', '火': '水', '土': '水'}[old]
        lines[0]['text'] = replacement + lines[0]['text'][1:]
        return {'refinement_version': REFINEMENT_VERSION,
                'request_sha256': request['request_sha256'], 'status': status,
                'reason': '首字更具体', 'result': {'lines': lines, 'changes': [{
                    'line_index': lines[0]['line_index'], 'position': 1, 'original': old,
                    'selected': replacement, 'candidates': [old, replacement], 'reason': '比较原字后选择'}],
                    'net_gain': {'dimension': '自然度', 'reason': '语气更顺', 'evidence': replacement},
                    'tradeoffs': [], 'semantic_lock_preserved': True}}

    def content_source(self, mode='exact', kind='line', *, allusion=False):
        base = fixture_state()
        lines = [line_for(export_context(base, i)) for i in range(1, 5)]
        req = base.to_dict()['request']
        req.pop('input')
        req['raw_request'] += '必用梦蝶' if allusion else lines[0]
        if allusion:
            req['allusions'] = [dict(id='a', name='梦蝶', required=True, source_excerpt='必用梦蝶')]
        else:
            req['required_fragments'] = [dict(id='f', text=lines[0] if kind == 'line' else lines[0][:2],
                kind=kind, mode=mode, line_index=1, source_excerpt=lines[0])]
        state = PoemState.create(req)
        TemplateLibrary().bind(state)
        state.set_plan(rhyme=base.to_dict()['rhyme'])
        for line in lines:
            state.append_line(line)
        plan = export_content_plan(state)
        if allusion:
            plan['assignments'][0].update(line_index=1, reason='第一句用典')
        search = drive(Search.create(state, poem_plan=fixture_plan(state), content_plan=plan, engine=fixture_engine()))
        self.assertEqual(search.data['status'], 'ready_for_review')
        return Polish.create(search.to_dict(), search.data['drafts'][0]['path_id'], engine=fixture_engine())

    def test_no_changes_completes(self):
        p = self.create()
        original = deepcopy(p.data['accepted']['lines'])
        self.finish(p)
        self.assertEqual(p.data['accepted']['lines'], original)
        self.assertEqual(p.data['history'], [])
        self.assertEqual(p.data['used'], dict(rounds=0, interactions=7))
        self.assertEqual(p.result(), dict(title='测试题目', lines=original, status='completed'))

    def test_refined_unit_is_rechecked_compared_and_committed_atomically(self):
        p = self.create()
        self.send(p, review_for(p.data['accepted']))
        old = deepcopy(p.data['accepted'])
        self.send(p, self.refinement_for(p.next_job()))
        self.assertEqual(p.next_job()['stage'], 'refinement_compare')
        target = p.next_job()['context']['target']
        self.send(p, {'candidate_review': review_for(target, score=.9), 'decision': 'adopt',
                      'net_gain_confirmed': True, 'reason': '全诗复检确认有净增益且无回退'})
        self.assertEqual(p.data['accepted']['revision'], old['revision'] + 1)
        self.assertEqual(p.next_job()['stage'], 'review')
        self.assertEqual(p.data['history'][-1]['decision'], 'adopted')

    def test_reflow_required_returns_to_complete_unit_generation(self):
        p = self.create()
        self.send(p, review_for(p.data['accepted']))
        self.send(p, self.refinement_for(p.next_job(), 'REFLOW_REQUIRED'))
        self.assertEqual(p.next_job()['stage'], 'reflow')
        self.assertEqual(p.next_job()['context']['refinement_request']['unit']['line_indices'], [1])

    def test_each_final_redline_and_unknown_blocks_output(self):
        for name in REDLINE_CHECKS:
            with self.subTest(check=name):
                p = self.create()
                self.reach_redline(p)
                self.send(p, redline_for(p.data['accepted'], answer='yes', check=name))
                self.assertEqual(p.data['status'], 'incomplete')
                self.assertEqual(p.data['reason'], 'FINAL_REDLINE_BLOCKED')
                self.assertIsNone(p.data['output'])
                self.assertEqual(p.data['accepted']['status'], 'reviewing')
        p = self.create()
        self.reach_redline(p)
        self.send(p, redline_for(p.data['accepted'], answer='unknown',
                                 check='allusion_facts_reversed'))
        self.assertEqual(p.data['reason'], 'FINAL_REDLINE_BLOCKED')

    def test_final_redline_contract_requires_all_checks_and_evidence(self):
        p = self.create()
        state = p.data['accepted']
        payload = redline_for(state)
        payload['checks'].pop('user_facts_reversed')
        with self.assertRaises(ValueError):
            validate_redline(payload, state)
        payload = redline_for(state)
        payload['checks']['allusion_facts_reversed']['evidence'] = []
        with self.assertRaises(ValueError):
            validate_redline(payload, state)

    def test_single_line_adopt_and_fresh_review(self):
        p = self.create()
        old = deepcopy(p.data['accepted'])
        self.revise(p)
        self.generate(p)
        self.assertEqual(p.next_job()['stage'], 'compare')
        self.send(p, comparison_for(p.next_job()))
        self.assertEqual(p.data['accepted']['revision'], old['revision'] + 1)
        self.assertEqual(p.data['accepted']['lines'][1:], old['lines'][1:])
        self.assertIsNone(p.data['review'])
        self.assertEqual(p.data['history'][0]['base'], old)
        self.assertEqual(p.data['history'][0]['decision'], 'adopted')
        self.assertEqual(p.data['accepted']['status'], 'reviewing')
        self.finish(p)

    def test_atomic_couplet_one_revision(self):
        p = self.create()
        old = deepcopy(p.data['accepted'])
        self.revise(p, (1, 2))
        self.generate(p, positions=(1, 2))
        self.assertEqual(p.data['accepted'], old)
        self.send(p, comparison_for(p.next_job()))
        self.assertEqual(p.data['accepted']['revision'], old['revision'] + 1)
        self.assertTrue(all(a != b for a, b in zip(old['lines'][:2], p.data['accepted']['lines'][:2])))
        self.assertEqual(old['lines'][2:], p.data['accepted']['lines'][2:])

    def test_rule_failure_retains_and_never_requests_comparison(self):
        p = self.create()
        old = deepcopy(p.data['accepted'])
        self.revise(p)
        self.generate(p, package_for(old, invalid=True))
        self.assertEqual(p.data['accepted'], old)
        self.assertEqual(p.data['history'][0]['reason'], 'RULES_FAILED_OR_UNKNOWN')
        self.assertEqual(p.next_job()['stage'], 'review')
        self.finish(p)

    def test_unknown_retains_passed_original(self):
        p = self.create()
        old = deepcopy(p.data['accepted'])
        self.revise(p)
        package = package_for(old)
        package['edits'][0]['new'] = '风' + old['lines'][0][1:]
        self.generate(p, package)
        self.assertEqual(p.next_job()['stage'], 'candidate_reading')
        request = p.next_job()['context']['reading_request']
        self.send(p, {'review_version': request['review_version'],
                      'request_sha256': request['request_sha256'],
                      'decisions': [{'coordinate': x['coordinate'], 'char': x['char'],
                                     'status': 'unknown', 'section': None, 'group': None,
                                     'reason': '当前句义不足以可靠判定'}
                                    for x in request['occurrences']]})
        self.assertIsNone(p.data['history'][0]['state']['checks']['valid'])
        self.assertEqual(p.data['accepted'], old)
        self.finish(p)

    def test_candidate_reading_review_can_resume_polish(self):
        p = self.create()
        old = deepcopy(p.data['accepted'])
        self.revise(p)
        package = package_for(old)
        package['edits'][0]['new'] = '风' + old['lines'][0][1:]
        self.generate(p, package)
        job = p.next_job()
        self.assertEqual(job['stage'], 'candidate_reading')
        request = job['context']['reading_request']
        decisions = []
        for occurrence in request['occurrences']:
            option = occurrence['options'][0]
            decisions.append({'coordinate': occurrence['coordinate'], 'char': occurrence['char'],
                              'status': 'selected', 'section': option['section'],
                              'group': option['group'], 'reason': '按当前句义选择已收录义项'})
        self.send(p, {'review_version': request['review_version'],
                      'request_sha256': request['request_sha256'], 'decisions': decisions})
        self.assertEqual(p.next_job()['stage'], 'compare')
        self.assertTrue(p.data['candidate']['readings'])

    def test_exact_line_and_phrase_position_protected(self):
        for kind in ('line', 'phrase'):
            with self.subTest(kind=kind):
                p = self.content_source(kind=kind)
                old = deepcopy(p.data['accepted'])
                self.revise(p)
                self.generate(p)
                self.assertEqual(p.data['accepted'], old)
                self.assertEqual(p.data['history'][0]['reason'], 'LOCKED_EXACT_CONTENT')
                self.finish(p)
                req = p.data['accepted']['request']['required_fragments'][0]
                self.assertEqual(req['line_index'], 1)
                self.assertIn(req['text'], p.data['accepted']['lines'][0])

    def test_semantics_rechecked_after_any_edit(self):
        for allusion in (False, True):
            for status in ('violated', 'unknown'):
                with self.subTest(allusion=allusion, status=status):
                    p = self.content_source('adapt', allusion=allusion)
                    old = deepcopy(p.data['accepted'])
                    # Change a DIFFERENT line to require context-sensitive re-review of line 1.
                    self.revise(p, (3,))
                    self.generate(p, positions=(3,))
                    self.assertEqual(p.next_job()['stage'], 'candidate_content')
                    self.assertEqual(p.next_job()['context']['filtered']['context']['line_index'], 1)
                    self.send(p, content_for(p.next_job(), status))
                    self.assertEqual(p.data['accepted'], old)
                    self.assertEqual(p.data['history'][0]['decision'], 'rejected')
                    self.assertEqual(p.next_job()['stage'], 'review')

    def test_local_beauty_whole_poem_regression_rejected(self):
        p = self.create()
        old = deepcopy(p.data['accepted'])
        self.revise(p)
        self.generate(p)
        c = comparison_for(p.next_job())
        c['candidate_review']['dimensions']['structure']['score'] = .7
        c['dimensions']['structure']['verdict'] = 'worse'
        self.send(p, c)
        self.assertEqual(p.data['accepted'], old)
        self.assertEqual(p.data['history'][0]['reason'], 'ADOPTION_STANDARD_NOT_MET')

    def test_small_gain_and_explicit_retain(self):
        for score, decision in ((.81, 'adopt'), (.95, 'retain')):
            p = self.create()
            old = deepcopy(p.data['accepted'])
            self.revise(p)
            self.generate(p)
            self.send(p, comparison_for(p.next_job(), score=score, decision=decision))
            self.assertEqual(p.data['accepted'], old)
            self.assertEqual(p.data['history'][0]['decision'], 'rejected')

    def test_wrong_job_path_run_revision_and_binding_atomic(self):
        for key, value in (('path_id', 'other'), ('run_id', 'other'), ('revision', 0),
                           ('binding', {}), ('work_sha256', '0' * 64)):
            p = self.create()
            before = p.to_dict()
            response = envelope(p.next_job(), review_for(p.data['accepted']))
            response[key] = value
            with self.assertRaises(ValueError):
                p.submit(response)
            self.assertEqual(p.to_dict(), before)
        a, b = self.create(), self.create()
        self.assertEqual(a.data['accepted']['revision'], b.data['accepted']['revision'])
        with self.assertRaises(ValueError):
            b.submit(envelope(a.next_job(), review_for(a.data['accepted'])))

    def test_old_review_rejected_after_edit(self):
        p = self.create()
        old_review = review_for(p.data['accepted'])
        self.revise(p)
        self.generate(p)
        self.send(p, comparison_for(p.next_job()))
        current = deepcopy(p.data['accepted'])
        self.send(p, old_review)
        self.assertEqual(p.data['accepted'], current)
        self.assertEqual(p.next_job()['attempt'], 2)
        self.assertIsNone(p.data['review'])
        self.assertNotEqual(p.data['status'], 'completed')

    def test_invalid_second_edit_never_partially_writes(self):
        p = self.create()
        self.revise(p, (1, 2))
        old = deepcopy(p.data['accepted'])
        package = package_for(old, positions=(1, 2))
        package['edits'][1]['old'] = '过期'
        self.generate(p, package)
        self.assertEqual(p.data['accepted'], old)
        self.assertEqual(p.data['history'], [])
        self.assertIsNone(p.data['candidate'])

    def test_invalid_edit_shapes(self):
        for mutation in ('duplicate', 'out_of_scope', 'empty', 'undeclared', 'stale', 'extra_line', 'noncouplet'):
            p = self.create()
            self.revise(p, (1, 2, 3))
            package = package_for(p.data['accepted'])
            if mutation == 'duplicate':
                package['edits'].append(deepcopy(package['edits'][0]))
            elif mutation == 'out_of_scope':
                package['edits'][0]['line_index'] = 4
            elif mutation == 'empty':
                package['edits'][0]['new'] = ''
            elif mutation == 'undeclared':
                package['lines'] = ['私改全文']
            elif mutation == 'stale':
                package['binding'] = make_binding({**p.data['accepted'], 'lines': ['另一分支']})
            elif mutation == 'extra_line':
                package = package_for(p.data['accepted'], positions=(1, 2, 3))
            else:
                package = package_for(p.data['accepted'], positions=(1, 3))
            old = deepcopy(p.data['accepted'])
            self.generate(p, package)
            self.assertEqual(p.data['accepted'], old, mutation)
            self.assertEqual(p.next_job()['attempt'], 2, mutation)

    def test_review_score_quote_conclusion_validation(self):
        state = self.create().data['accepted']
        for mutation in ('score', 'bool', 'na', 'quote', 'position', 'conclusion', 'duplicate', 'major', 'low'):
            r = review_for(state, revise=True)
            if mutation == 'score':
                r['dimensions']['theme']['score'] = 2
            elif mutation == 'bool':
                r['dimensions']['theme']['score'] = True
            elif mutation == 'na':
                r['dimensions']['parallelism']['score'] = 1
            elif mutation == 'quote':
                r['issues'][0]['locations'][0]['quote'] = '不在诗中'
            elif mutation == 'position':
                r['issues'][0]['locations'][0]['line_index'] = 5
            elif mutation == 'conclusion':
                r['conclusion'] = 'ready'
            elif mutation == 'duplicate':
                r['issues'].append(deepcopy(r['issues'][0]))
            elif mutation == 'major':
                r['issues'][0]['severity'] = 'major'
            else:
                r['dimensions']['theme']['score'] = .3
            with self.assertRaises(ValueError, msg=mutation):
                validate_review(r, state)
        r = review_for(state)
        r['total'] = 1
        with self.assertRaises(ValueError):
            validate_review(r, state)

    def test_lushi_requires_character_by_character_parallelism(self):
        state = fixture_state('五言律诗')
        search = drive(Search.create(state, poem_plan=fixture_plan(state), engine=fixture_engine()))
        p = Polish.create(search.to_dict(), search.data['drafts'][0]['path_id'], engine=fixture_engine())
        r = review_for(p.data['accepted'])
        r['dimensions']['parallelism']['couplets'][1]['character_alignment'][2]['upper_char'] = '错'
        self.send(p, r)
        self.assertEqual(p.next_job()['attempt'], 2)
        self.finish(p)

    def test_normalized_weight_total_and_no_history_score(self):
        p = self.create()
        self.assertIsNone(p.data['accepted']['quality_score'])
        r = review_for(p.data['accepted'])
        r['dimensions']['theme']['score'] = .9
        result = validate_review(r, p.data['accepted'])
        self.assertAlmostEqual(result['total'], (.9 * .19 + .8 * .66) / .85)

    def test_locked_requirements_need_clarification(self):
        p = self.create()
        old = deepcopy(p.data['accepted'])
        r = review_for(old, revise=True)
        r['issues'][0]['requires_user_change'] = True
        r['conclusion'] = 'needs_clarification'
        self.send(p, r)
        self.assertEqual(p.data['status'], 'needs_clarification')
        self.assertEqual(p.data['accepted'], old)
        p = self.create()
        self.revise(p)
        self.send(p, dict(packages=[], needs_user_change=True, reason='需要改变锁定要求'))
        self.assertEqual(p.data['status'], 'needs_clarification')

    def test_required_unknown_and_blocking_review_cannot_complete(self):
        p = self.create()
        r = review_for(p.data['accepted'])
        r['uncertainties'] = [dict(reason='关键语义不确定', blocking=True)]
        r['conclusion'] = 'unresolved'
        self.send(p, r)
        self.assertEqual(p.data['status'], 'incomplete')
        self.assertEqual(p.data['accepted']['status'], 'reviewing')

    def test_format_repair_and_interaction_caps(self):
        p = self.create()
        for expected in (2, 3, None):
            self.send(p, '{invalid json')
            self.assertEqual(p.next_job()['attempt'] if p.next_job() else None, expected)
        self.assertEqual(p.data['used']['interactions'], 3)
        self.assertEqual(p.data['reason'], 'FORMAT_REPAIRS_EXHAUSTED')
        p = self.create(max_interactions=1)
        self.send(p, review_for(p.data['accepted']))
        self.assertEqual(p.data['status'], 'budget_exhausted')
        self.assertEqual(p.data['accepted']['status'], 'reviewing')
        p = self.create(max_interactions=1)
        self.send(p, '{}')
        self.assertEqual(p.data['status'], 'budget_exhausted')

    def test_round_and_candidate_budgets(self):
        p = self.create(max_rounds=0)
        self.send(p, review_for(p.data['accepted'], revise=True))
        self.assertEqual(p.data['reason'], 'MAX_POLISH_ROUNDS')
        p = self.create(max_rounds=1, candidates_per_round=1)
        self.revise(p)
        self.send(p, dict(packages=[package_for(p.data['accepted'], modification_id=x) for x in ('m1', 'm2')],
                          needs_user_change=False, reason='两个候选'))
        self.assertEqual(p.next_job()['attempt'], 2)
        self.send(p, dict(packages=[], needs_user_change=False, reason='没有更好的局部修改'))
        self.send(p, review_for(p.data['accepted'], revise=True))
        self.assertEqual(p.data['reason'], 'MAX_POLISH_ROUNDS')
        self.assertEqual(p.data['accepted']['status'], 'reviewing')

    def test_replays_success_invalid_and_changed(self):
        p = self.create()
        r = envelope(p.next_job(), '{}')
        p.submit(r)
        before = p.to_dict()
        self.assertEqual(p.submit(r)['status'], 'already_rejected')
        self.assertEqual(p.to_dict(), before)
        r = envelope(p.next_job(), review_for(p.data['accepted']))
        p.submit(r)
        before = p.to_dict()
        self.assertEqual(p.submit(r)['status'], 'already_processed')
        self.assertEqual(p.to_dict(), before)
        r['payload']['recommend_revision'] = True
        with self.assertRaises(ValueError):
            p.submit(r)

    def test_save_restore_pending_budget_and_history(self):
        p = self.create()
        self.revise(p)
        self.generate(p)
        self.send(p, '{}')
        p = Polish.from_dict(p.to_dict(), engine=fixture_engine())
        self.assertEqual(p.next_job()['attempt'], 2)
        self.send(p, comparison_for(p.next_job()))
        p = Polish.from_dict(p.to_dict(), engine=fixture_engine())
        self.finish(p)
        restored = Polish.from_dict(p.to_dict(), engine=fixture_engine())
        self.assertEqual(restored.to_dict(), p.to_dict())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'polish.json'
            p.save(path)
            self.assertEqual(Polish.load(path, engine=fixture_engine()).to_dict(), p.to_dict())
            with self.assertRaises(FileExistsError):
                p.save(path)

    def test_final_missing_review_content_and_tampered_restore(self):
        for key in ('review', 'content'):
            p = self.content_source('adapt')
            self.reach_redline(p)
            self.send(p, redline_for(p.data['accepted']))
            p.data[key] = None
            with self.assertRaises(ValueError):
                self.send(p, dict(title='测试', notes=[]))
            self.assertNotEqual(p.data['status'], 'completed')
            self.assertEqual(p.data['accepted']['status'], 'reviewing')
        p = self.create()
        self.finish(p)
        for key, value in (('review', None), ('used', dict(rounds=0, interactions=0)), ('history', [{}])):
            snapshot = p.to_dict()
            snapshot['polish'][key] = value
            snapshot['sha256'] = digest(snapshot['polish'])
            with self.assertRaises(ValueError):
                Polish.from_dict(snapshot, engine=fixture_engine())

    def test_final_rules_failure(self):
        p = self.create()
        self.reach_redline(p)
        self.send(p, redline_for(p.data['accepted']))
        original = p.engine.check_poem
        def unknown(state, **kwargs):
            result = original(state, **kwargs)
            result['checks']['pingze']['status'] = 'unknown'
            result['valid'] = None
            return result
        with patch.object(p.engine, 'check_poem', side_effect=unknown):
            self.send(p, dict(title='题目', notes=[]))
        self.assertEqual(p.data['status'], 'incomplete')
        self.assertEqual(p.data['reason'], 'FINAL_RULES_FAILED_OR_UNKNOWN')

    def test_title_not_body_nor_content_evidence(self):
        p = self.content_source('adapt')
        self.send(p, review_for(p.data['accepted']))
        while p.next_job()['stage'] == 'refinement':
            self.send(p, no_change_for(p.next_job()))
        self.send(p, content_for(p.next_job(), 'violated'))
        self.assertEqual(p.data['status'], 'incomplete')
        self.assertIsNone(p.data['output'])
        p = self.create()
        self.reach_redline(p)
        self.send(p, redline_for(p.data['accepted']))
        title = '题目可以很长也无需按五言或平水韵验收'
        self.send(p, dict(title=title, notes=[]))
        self.assertEqual(len(p.result()['lines']), 4)
        self.assertEqual(p.result()['title'], title)
        self.assertEqual(p.data['accepted']['checks']['subject'], make_binding(p.data['accepted'])['subject'])

    def test_handoff_requires_snapshot_explicit_path_and_bound_evidence(self):
        for snapshot, path in ((self.source['search']['drafts'][0]['state'], self.path),
                               (self.source, None), (self.source, 'nonexistent')):
            with self.assertRaises(ValueError):
                Polish.create(snapshot, path, engine=fixture_engine())
        for mutation in ('status', 'binding', 'request', 'lines'):
            snapshot = deepcopy(self.source)
            s = snapshot['search']
            draft = s['drafts'][0]
            if mutation == 'status':
                s['status'] = 'budget_exhausted'
            elif mutation == 'binding':
                draft['final_content_binding'] = {}
            elif mutation == 'request':
                draft['state']['request']['theme'] = '另一个请求'
            else:
                draft['state']['lines'][0] = '同修订号其他草稿'
            snapshot['sha256'] = digest(s)
            with self.assertRaises(ValueError, msg=mutation):
                Polish.create(snapshot, self.path, engine=fixture_engine())

    def test_handoff_reruns_engine(self):
        engine = fixture_engine()
        with patch.object(engine, 'check_poem', wraps=engine.check_poem) as check:
            Polish.create(self.source, self.path, engine=engine)
            self.assertGreaterEqual(check.call_count, 1)
        check = engine.check_poem(PoemState.from_dict(self.source['search']['drafts'][0]['state']))
        check['valid'] = None
        check['checks']['pingze']['status'] = 'unknown'
        with patch.object(engine, 'check_poem', return_value=check):
            with self.assertRaisesRegex(ValueError, 'Current rule engine'):
                Polish.create(self.source, self.path, engine=engine)

    def test_context_evidence_and_detached_jobs(self):
        p = self.content_source('adapt')
        j = p.next_job()
        self.assertTrue(j['context']['content_evidence'])
        j['context']['accepted']['request']['theme'] = '私改'
        self.assertNotEqual(j, p.next_job())

    def test_multiple_candidates_reject_then_adopt(self):
        p = self.create()
        self.revise(p)
        self.send(p, dict(packages=[package_for(p.data['accepted'], modification_id='bad', invalid=True),
                                   package_for(p.data['accepted'], modification_id='good')],
                          needs_user_change=False, reason='两个候选'))
        self.assertEqual(p.next_job()['stage'], 'compare')
        self.send(p, comparison_for(p.next_job()))
        self.assertEqual([h['decision'] for h in p.data['history']], ['rejected', 'adopted'])
        self.finish(p)

    def test_adopted_but_budget_exhausted_cannot_complete(self):
        p = self.create(max_interactions=3)
        self.revise(p)
        self.generate(p)
        self.send(p, comparison_for(p.next_job()))
        self.assertEqual(p.data['history'][0]['decision'], 'adopted')
        self.assertIsNone(p.data['review'])
        self.assertEqual(p.data['status'], 'budget_exhausted')
        self.assertEqual(p.data['accepted']['status'], 'reviewing')
        self.assertIsNone(p.result()['title'])
        self.assertEqual(Polish.from_dict(p.to_dict(), engine=fixture_engine()).to_dict(), p.to_dict())

    def test_semantic_pending_restore_and_final_recheck(self):
        p = self.content_source('adapt')
        self.revise(p, (3,))
        self.generate(p, positions=(3,))
        before = p.to_dict()
        p = Polish.from_dict(before, engine=fixture_engine())
        self.assertEqual(p.to_dict(), before)
        while p.next_job()['stage'] == 'candidate_content':
            self.send(p, content_for(p.next_job()))
        self.send(p, comparison_for(p.next_job()))
        self.assertTrue(p.next_job()['context']['content_evidence'])
        self.assertEqual(p.next_job()['context']['content_evidence'][0]['context']['lines'], p.data['accepted']['lines'])
        self.finish(p)
        self.assertEqual(Polish.from_dict(p.to_dict(), engine=fixture_engine()).to_dict(), p.to_dict())

    def test_free_text_constraint_must_be_reviewed_and_satisfied(self):
        state = deepcopy(self.create().data['accepted'])
        state['request']['constraints'] = ['不写愁绪']
        state['constraints'] = ['不写愁绪']
        state['checks'] = {}
        PoemState.from_dict(state)
        r = review_for(state)
        r['user_constraints'] = []
        with self.assertRaises(ValueError):
            validate_review(r, state)
        r = review_for(state)
        r['user_constraints'][0]['status'] = 'unknown'
        with self.assertRaises(ValueError):
            validate_review(r, state)
        r['conclusion'] = 'unresolved'
        self.assertEqual(validate_review(r, state)['conclusion'], 'unresolved')

    def test_comparison_mismatch_and_missing_issue_resolution(self):
        p = self.create()
        self.revise(p)
        self.generate(p)
        c = comparison_for(p.next_job())
        c['dimensions']['structure']['verdict'] = 'same'
        original = deepcopy(p.data['accepted'])
        self.send(p, c)
        self.assertEqual(p.next_job()['attempt'], 2)
        self.assertEqual(p.data['accepted'], original)
        c = comparison_for(p.next_job())
        c['resolved_issue_ids'] = []
        self.send(p, c)
        self.assertEqual(p.data['history'][0]['reason'], 'ADOPTION_STANDARD_NOT_MET')
        self.assertEqual(p.data['accepted'], original)

    def test_pending_context_cannot_be_rebound_after_issuance(self):
        p = self.create()
        p.data['accepted']['imagery'] = ['不同分支的意象']
        p.data['accepted']['checks'] = {}
        response = envelope(p.next_job(), review_for(p.data['accepted']))
        before = p.to_dict()
        with self.assertRaises(ValueError):
            p.submit(response)
        self.assertEqual(p.to_dict(), before)

    def test_moving_exact_phrase_does_not_satisfy_fixed_position(self):
        p = self.content_source(kind='phrase')
        self.revise(p, (1, 2))
        original = deepcopy(p.data['accepted'])
        package = package_for(original, positions=(1, 2))
        phrase = original['request']['required_fragments'][0]['text']
        package['edits'][1]['new'] = phrase + package['edits'][1]['new'][2:]
        self.generate(p, package)
        self.assertEqual(p.data['accepted'], original)
        self.assertEqual(p.data['history'][0]['reason'], 'LOCKED_EXACT_CONTENT')

    def test_examples_and_migration_other_working_directory(self):
        self.assertEqual(run_example(adopt=True).data['status'], 'completed')
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / 'moved'
            shutil.copytree(Path(__file__).resolve().parent.parent, project, ignore=shutil.ignore_patterns('__pycache__'))
            workspace = Path(temp) / 'workspace'
            run = subprocess.run([sys.executable, str(project/'scripts/day5_example.py'),
                                  '--output', 'examples/day5-migrated.json', '--work-dir', str(workspace)],
                                 cwd=temp, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            saved = json.loads((workspace/'examples/day5-migrated.json').read_text(encoding='utf-8'))
            p = Polish.from_dict(saved, engine=fixture_engine())
            self.assertEqual(p.data['status'], 'completed')
            self.assertEqual(p.data['history'][0]['decision'], 'rejected')

    def test_migrated_cli_complete_with_bundled_real_lexicon(self):
        state = fixture_state()
        lexicon, used, lines = RhymeLexicon(), set(), []
        for line_index in range(1, 5):
            context = export_context(state, line_index)
            chars = []
            for item in context['tone_requirements']:
                is_rhyme = context['is_rhyme_line'] and item['pos'] == len(context['tone_requirements'])
                char = next(char for char in lexicon._index
                            if char not in used and len(lexicon.lookup(char)) == 1
                            and (item['tune'] == '中' or lexicon.lookup(char)[0]['tone'] == item['tune'])
                            and (not is_rhyme or lexicon.lookup(char)[0]['group'] == '一东'))
                used.add(char); chars.append(char)
            lines.append(''.join(chars))
        for line in lines:
            state.append_line(line)
        source = Search.create(state, poem_plan=fixture_plan(state))  # Real project rhyme library, no injected verdicts.
        self.assertEqual(source.data['status'], 'ready_for_review')
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / 'moved'
            shutil.copytree(Path(__file__).resolve().parent.parent, project, ignore=shutil.ignore_patterns('__pycache__'))
            workspace = Path(temp) / 'workspace'
            folder = workspace / 'examples' / 'day5-cli'
            folder.mkdir(parents=True)
            def write(name, value):
                (folder / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            def read(name):
                return json.loads((folder / name).read_text(encoding='utf-8'))
            def run(command, *args):
                result = subprocess.run([sys.executable, str(project/'scripts/poetry_polish.py'), command,
                                         *args, '--work-dir', str(workspace)],
                                        cwd=temp, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            root = 'examples/day5-cli/'
            write('source.json', source.to_dict())
            run('start', '--input', root+'source.json', '--path-id', source.data['drafts'][0]['path_id'],
                '--output', root+'p1.json')
            run('next', '--input', root+'p1.json', '--output', root+'job.json')
            job = read('job.json')
            write('review.json', envelope(job, review_for(job['context']['target'])))
            run('submit', '--input', root+'p1.json', '--response', root+'review.json', '--output', root+'p2.json')
            current = 'p2.json'
            for index in range(4):
                job = read(current)['polish']['pending']
                self.assertEqual(job['stage'], 'refinement')
                response_name = f'refinement-{index}.json'
                next_name = f'p-refinement-{index}.json'
                write(response_name, envelope(job, no_change_for(job)))
                run('submit', '--input', root+current, '--response', root+response_name,
                    '--output', root+next_name)
                current = next_name
            job = read(current)['polish']['pending']
            write('redline.json', envelope(job, redline_for(job['context']['target'])))
            run('submit', '--input', root+current, '--response', root+'redline.json', '--output', root+'p3.json')
            job = read('p3.json')['polish']['pending']
            write('final.json', envelope(job, dict(title='迁移流程测试', notes=[])))
            run('submit', '--input', root+'p3.json', '--response', root+'final.json', '--output', root+'p4.json')
            run('result', '--input', root+'p4.json', '--output', root+'result.json')
            self.assertEqual(read('result.json'), dict(title='迁移流程测试', lines=lines, status='completed'))


if __name__ == '__main__':
    unittest.main()
