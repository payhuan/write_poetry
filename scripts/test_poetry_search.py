"""Day 4 uses fixed responses and an actual rule engine with a tiny deterministic lexicon."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from assessment_binding import make_binding
from poem_state import PoemState
from poetry_search import Search
from poetry_round import RoundError
from round_content import export_content_plan
from day4_example import fixture_state, fixture_engine, response_for, drive, line_for


class SearchTests(unittest.TestCase):
    def create(self, **budget):
        return Search.create(fixture_state(**budget), engine=fixture_engine())

    def test_basic_and_four_forms(self):
        for form in ('五言绝句', '七言绝句', '五言律诗', '七言律诗'):
            with self.subTest(form=form):
                s = drive(Search.create(fixture_state(form), engine=fixture_engine()))
                self.assertEqual(s.data['status'], 'ready_for_review', s.data['events'])
                draft = s.data['drafts'][0]['state']
                self.assertEqual(len(draft['lines']), draft['form']['line_count'])
                self.assertEqual(draft['status'], 'reviewing')
                self.assertTrue(draft['checks']['valid'])
                self.assertIsNone(draft['quality_score'])

    def test_global_beam(self):
        s = self.create(beam_width=2)
        for _ in range(6):
            s.submit(response_for(s.next_job(), two=True))
        self.assertEqual([p['depth'] for p in s.data['active']], [2, 2])
        self.assertEqual(len(s.data['alternatives']), 2)
        self.assertEqual([p['parent_path_id'] for p in s.data['active']], ['p1', 'p1'])

    def test_live_alternative_before_backtrack(self):
        s = drive(self.create(beam_width=2), fail_high=True)
        self.assertEqual(s.data['status'], 'ready_for_review')
        self.assertEqual(s.data['used']['backtracks'], 0)

    def test_recovery(self):
        s = drive(self.create(), fail_high=True)
        self.assertEqual(s.data['status'], 'ready_for_review')
        self.assertEqual(s.data['used']['backtracks'], 1)
        self.assertEqual(s.data['used']['rounds'], 5)
        self.assertTrue(any(e['code'] == 'BRANCH_RESTORED' for e in s.data['events']))

    def test_zero_backtracks(self):
        s = drive(self.create(max_backtracks=0), fail_high=True)
        self.assertEqual(s.data['status'], 'no_feasible_path')
        self.assertEqual(s.data['used']['backtracks'], 0)
        self.assertFalse(s.data['drafts'])

    def test_shared_round_budget(self):
        s = drive(self.create(beam_width=2, max_rounds=2), two=True)
        self.assertEqual(s.data['status'], 'budget_exhausted')
        self.assertEqual(s.data['used']['rounds'], 2)
        self.assertTrue(s.data['children'])

    def test_format_bounded(self):
        s = self.create()
        for i in range(3):
            r = response_for(s.next_job())
            r['payload']['candidates'] = []
            s.submit(r)
        self.assertEqual(s.data['used']['interactions'], 3)
        self.assertEqual(s.data['status'], 'no_feasible_path')
        self.assertEqual(s.data['used']['rounds'], 1)

    def test_repeated_invalid_result_is_idempotent(self):
        s = self.create()
        r = response_for(s.next_job())
        r['payload']['candidates'] = []
        s.submit(r)
        before = s.to_dict()
        self.assertEqual(s.submit(r)['status'], 'already_rejected')
        self.assertEqual(before, s.to_dict())

    def test_malformed_json_text_is_budgeted(self):
        s = self.create()
        for attempt in range(3):
            r = response_for(s.next_job())
            r['payload'] = {'attempt': attempt + 1, 'raw_json': '{broken json'}
            s.submit(r)
        self.assertEqual(s.data['status'], 'no_feasible_path')
        self.assertEqual(s.data['used']['interactions'], 3)

    def test_runtime_error(self):
        s = self.create()
        with patch.object(s.engine, 'check_candidate', side_effect=RuntimeError('fixture engine unavailable')):
            s.submit(response_for(s.next_job()))
        self.assertEqual(s.data['status'], 'run_error')
        self.assertFalse(s.data['children'])

    def test_model_cap(self):
        s = Search.create(fixture_state(), engine=fixture_engine(), max_interactions=1)
        s.submit(response_for(s.next_job()))
        self.assertEqual(s.data['status'], 'budget_exhausted')
        self.assertEqual(s.data['used']['interactions'], 1)

    def test_wrong_path_atomic(self):
        s = self.create()
        before = s.to_dict()
        r = response_for(s.next_job())
        r['path_id'] = 'another-same-revision'
        with self.assertRaises(RoundError):
            s.submit(r)
        self.assertEqual(s.to_dict(), before)

    def test_same_revision_sibling_results(self):
        s = self.create(beam_width=2)
        s.submit(response_for(s.next_job()))
        s.submit(response_for(s.next_job()))
        old = s.next_job()
        s.submit(response_for(old))
        s.submit(response_for(s.next_job()))
        new = s.next_job()
        self.assertEqual(old['context']['revision'], new['context']['revision'])
        r = response_for(new)
        r['payload'] = response_for(old)['payload']
        before = deepcopy(s.data['children'])
        s.submit(r)
        self.assertEqual(s.data['children'], before)
        self.assertEqual(s.next_job()['attempt'], 2)

    def test_fork_isolation(self):
        s = self.create(beam_width=2)
        s.submit(response_for(s.next_job()))
        s.submit(response_for(s.next_job()))
        first, second = s.data['active']
        saved = deepcopy(second)
        first['state']['lines'].append('独立')
        first['state']['candidate_history'].clear()
        first['content']['requirements'].append({'test': True})
        self.assertEqual(second, saved)

    def test_final_recheck(self):
        engine = fixture_engine()
        with patch.object(engine, 'check_poem', wraps=engine.check_poem) as check:
            s = drive(Search.create(fixture_state(), engine=engine))
            self.assertEqual(check.call_count, 1)
            p = s.data['drafts'][0]['state']
            for k, v in make_binding(p).items():
                self.assertEqual(p['checks'][k], v)
            self.assertNotEqual(p['candidate_history'][-1]['revision'], p['revision'])

    def test_final_unknown_rejected(self):
        engine = fixture_engine()
        original = engine.check_poem
        def unknown(state):
            r = original(state)
            r['valid'] = None
            r['checks']['pingze']['status'] = 'unknown'
            return r
        with patch.object(engine, 'check_poem', side_effect=unknown):
            s = drive(Search.create(fixture_state(max_backtracks=0), engine=engine))
        self.assertFalse(s.data['drafts'])
        self.assertEqual(s.data['status'], 'no_feasible_path')

    def test_save_resume_identical(self):
        s = self.create()
        s.submit(response_for(s.next_job()))
        # Reuse a fixed UUID factory for subsequently exported Day 3 batches.
        class Fixed:
            hex = 'a' * 32
        with patch('poetry_round.uuid4', return_value=Fixed()):
            a = drive(Search.from_dict(s.to_dict(), engine=fixture_engine()), fail_high=True)
            b = Search.from_dict(s.to_dict(), engine=fixture_engine())
            while b.next_job() and b.data['status'] == 'waiting_for_llm':
                b.submit(response_for(b.next_job(), fail_high=True))
                b = Search.from_dict(b.to_dict(), engine=fixture_engine())
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_replay(self):
        s = self.create()
        r = response_for(s.next_job())
        s.submit(r)
        before = s.to_dict()
        self.assertEqual(s.submit(r)['status'], 'already_processed')
        self.assertEqual(before, s.to_dict())
        r['payload']['attempt'] = 2
        with self.assertRaises(RoundError):
            s.submit(r)

    def test_stable_ties(self):
        s = self.create(beam_width=2)
        s.submit(response_for(s.next_job()))
        r = response_for(s.next_job())
        for entry in r['payload']['scores']:
            entry['evaluation']['dimensions'] = {k: .5 for k in entry['evaluation']['dimensions']}
        r['payload']['scores'].reverse()
        s.submit(r)
        self.assertEqual([p['adoptions'][0]['record']['id'] for p in s.data['active']], ['0', '1'])

    def test_bad_score_atomic(self):
        s = self.create()
        s.submit(response_for(s.next_job()))
        before = deepcopy(s.data['active'])
        r = response_for(s.next_job())
        r['payload']['scores'][-1]['evaluation']['dimensions']['theme'] = 2
        s.submit(r)
        self.assertEqual(before, s.data['active'])
        self.assertEqual(s.data['next_path'], 1)
        self.assertEqual(s.data['children'], [])

    def content_state(self, mode='exact'):
        d = fixture_state().to_dict()
        # Recreate through the input API, preserving normalization/validation.
        req = d['request']
        from poetry_round import export_context
        text = line_for(export_context(fixture_state(), 4))
        req['raw_request'] += text
        req.pop('input')
        req['required_fragments'] = [{'id': 'f', 'text': text, 'kind': 'line',
                                      'mode': mode, 'line_index': 4, 'source_excerpt': text}]
        state = PoemState.create(req)
        from poem_templates import TemplateLibrary
        TemplateLibrary().bind(state)
        state.set_plan(rhyme=d['rhyme'], structure=d['structure'])
        state.set_budget(beam_width=1)
        return state

    def test_fixed_requirements_survive_recovery(self):
        state = self.content_state()
        text = state.to_dict()['request']['required_fragments'][0]['text']
        s = drive(Search.create(state, engine=fixture_engine()), fail_high=True)
        self.assertEqual(s.data['status'], 'ready_for_review', s.data['events'])
        p = s.data['drafts'][0]
        self.assertEqual(p['state']['lines'][3], text)
        self.assertEqual(p['state']['request'], state.to_dict()['request'])
        self.assertEqual(p['state']['meter_template'], state.to_dict()['meter_template'])
        self.assertEqual(s.data['used']['backtracks'], 1)

    def test_final_semantic_unknown_not_promoted(self):
        s = Search.create(self.content_state('adapt'), engine=fixture_engine())
        while s.data['status'] == 'waiting_for_llm':
            j = s.next_job()
            r = response_for(j)
            if j['stage'] == 'final_content':
                r['payload']['reviews'][0]['requirements'][0]['status'] = 'unknown'
            s.submit(r)
        self.assertFalse(s.data['drafts'])
        self.assertTrue(any(e['code'] == 'FINAL_CONTENT_FAILED_OR_UNKNOWN' for e in s.data['events']))

    def test_storage_bound_and_invalid_config(self):
        s = drive(Search.create(fixture_state(), engine=fixture_engine(), max_alternatives=0))
        self.assertFalse(s.data['alternatives'])
        with self.assertRaises(RoundError):
            Search.create(fixture_state(), max_attempts=4, engine=fixture_engine())
        artifact = s.to_dict()
        artifact['search']['used']['rounds'] = 0
        with self.assertRaises(RoundError):
            Search.from_dict(artifact)
        from assessment_binding import digest
        with self.assertRaises(RoundError):
            Search.from_dict({'search': {}, 'sha256': digest({})})

    def test_allusion_arrangement_and_optional_unknown(self):
        req = fixture_state().to_dict()['request']
        req.pop('input')
        req['raw_request'] += ' 必用梦蝶 可选采薇'
        req['allusions'] = [
            {'id': 'required', 'name': '梦蝶', 'source_excerpt': '必用梦蝶', 'required': True},
            {'id': 'optional', 'name': '采薇', 'source_excerpt': '可选采薇', 'required': False}]
        state = PoemState.create(req)
        from poem_templates import TemplateLibrary
        TemplateLibrary().bind(state)
        state.set_plan(rhyme=fixture_state().to_dict()['rhyme'])
        state.set_budget(beam_width=1)
        self.assertEqual(Search.create(state, engine=fixture_engine()).data['status'], 'needs_clarification')
        plan = export_content_plan(state)
        plan['assignments'][0].update(line_index=4, reason='末句承担')
        s = Search.create(state, content_plan=plan, engine=fixture_engine())
        seen_final = False
        while s.data['status'] == 'waiting_for_llm':
            job = s.next_job()
            r = response_for(job)
            if job['stage'] in ('content', 'final_content'):
                seen_final |= job['stage'] == 'final_content'
                for review in r['payload']['reviews']:
                    for verdict in review['requirements']:
                        if verdict['requirement_id'] == 'optional':
                            verdict['status'] = 'unknown'
            s.submit(r)
        self.assertEqual(s.data['status'], 'ready_for_review', s.data['events'])
        self.assertTrue(seen_final)

    def test_initial_fixed_prefix_kept_on_recovery(self):
        state = fixture_state()
        from poetry_round import export_context
        prefix = line_for(export_context(state, 1))
        state.append_line(prefix)
        s = Search.create(state, engine=fixture_engine())
        while s.data['status'] == 'waiting_for_llm':
            job = s.next_job()
            r = response_for(job, two=job['context']['line_index'] == 2)
            if job['stage'] == 'generate' and len(job['context']['lines']) >= 2 and '天' in job['context']['lines'][1]:
                r['payload']['candidates'] = [{'id': 'fail', 'text': '错', 'line_index': job['context']['line_index']}]
            s.submit(r)
        self.assertEqual(s.data['status'], 'ready_for_review')
        self.assertEqual(s.data['used']['backtracks'], 1)
        self.assertEqual(s.data['drafts'][0]['state']['lines'][0], prefix)

    def test_completed_missing_exact_rejected(self):
        state = self.content_state()
        from poetry_round import export_context
        for index in range(1, 5):
            state.append_line(line_for(export_context(fixture_state(), index), variant=1))
        s = Search.create(state, engine=fixture_engine())
        self.assertEqual(s.data['status'], 'no_feasible_path')
        self.assertFalse(s.data['drafts'])

    def test_migration_other_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / 'moved'
            shutil.copytree(Path(__file__).resolve().parent.parent, project, ignore=shutil.ignore_patterns('__pycache__'))
            workspace = Path(temp) / 'workspace'
            run = subprocess.run([sys.executable, str(project/'scripts/day4_example.py'),
                                  '--output', 'examples/migration-day4.json', '--work-dir', str(workspace)],
                                 cwd=temp, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            saved = json.loads((workspace/'examples/migration-day4.json').read_text(encoding='utf-8'))
            s = Search.from_dict(saved, engine=fixture_engine())
            self.assertEqual(s.data['status'], 'ready_for_review')


if __name__ == '__main__':
    unittest.main()
