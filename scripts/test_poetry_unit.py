from copy import deepcopy
import unittest

from day4_example import fixture_state, fixture_plan, fixture_engine, response_for
from poetry_search import Search
from poetry_round import RoundError
from assessment_binding import digest
from poem_state import PoemState
from poem_templates import TemplateLibrary
from round_content import export_content_plan


class PoetryUnitTests(unittest.TestCase):
    def at_middle_couplet(self):
        state = fixture_state('五言律诗')
        search = Search.create(state, poem_plan=fixture_plan(state), engine=fixture_engine())
        for _ in range(20):
            job = search.next_job()
            if job['stage'] == 'unit_generate':
                return search
            search.submit(response_for(job))
        self.fail('middle-couplet work was not issued')

    def test_middle_couplet_is_one_job_and_one_revision(self):
        search = self.at_middle_couplet()
        job = search.next_job()
        self.assertEqual(job['context']['line_indices'], [3, 4])
        before = deepcopy(search.data['active'][search.data['cursor']]['state'])
        search.submit(response_for(job))
        self.assertEqual(search.next_job()['stage'], 'unit_score')
        search.submit(response_for(search.next_job(), value=.1))
        path = next(p for p in search.data['active'] if p['depth'] == 4)
        self.assertEqual(path['state']['revision'], before['revision'] + 1)
        self.assertEqual(len(path['semantic_locks'][-1]['line_indices']), 2)

    def test_dual_layer_alignment_and_score_consistency(self):
        search = self.at_middle_couplet()
        search.submit(response_for(search.next_job()))
        score_job = search.next_job()
        self.assertEqual(score_job['stage'], 'unit_score')
        self.assertEqual(score_job['filtered']['score_request']['rubric_version'], 'literary-5')
        result = response_for(score_job)
        pair = result['payload']['scores'][0]['parallelism']
        self.assertTrue(pair['character_alignment'])
        self.assertTrue(pair['structure_alignment'])
        pair['structure_score'] = .9
        rejected = search.submit(result)
        self.assertEqual(rejected['error'], 'PARALLELISM_SCORE_MISMATCH')

    def test_bad_second_line_leaves_parent_untouched(self):
        search = self.at_middle_couplet()
        before = deepcopy(search.data)
        response = response_for(search.next_job())
        response['payload']['candidates'][0]['lines'][1]['line_index'] = 7
        result = search.submit(response)
        self.assertEqual(result['error'], 'INVALID_UNIT_LINES')
        self.assertEqual(search.data['active'], before['active'])
        self.assertEqual(search.next_job()['attempt'], 2)

    def test_replan_required_stops_without_text(self):
        search = self.at_middle_couplet()
        job = search.next_job()
        context = job['context']
        payload = {k: context[k] for k in
                   ('contract_version', 'batch_id', 'context_sha256', 'generation_sha256')}
        payload.update(attempt=1, status='replan_required', candidates={
            'reason': '当前增量与后文保护内容冲突', 'conflict': '无法自然保持两者',
            'suggested_replan_scope': '仅调整颔联任务'})
        response = {**{k: job[k] for k in ('run_id', 'job_id', 'path_id', 'batch_id', 'context_sha256')},
                    'payload': payload}
        search.submit(response)
        self.assertEqual(search.data['status'], 'needs_clarification')
        self.assertEqual(search.data['events'][-1]['code'], 'REPLAN_REQUIRED')

    def test_middle_couplet_reading_review_precedes_scoring(self):
        search = self.at_middle_couplet()
        job = search.next_job()
        response = response_for(job)
        requirement = job['context']['line_requirements'][0]
        position = next(item['pos'] for item in requirement['tone_requirements']
                        if item['tune'] == '平')
        line = response['payload']['candidates'][0]['lines'][0]
        line['text'] = line['text'][:position-1] + '风' + line['text'][position:]
        search.submit(response)
        self.assertEqual(search.next_job()['stage'], 'unit_reading')
        search.submit(response_for(search.next_job()))
        self.assertEqual(search.next_job()['stage'], 'unit_score')

    def test_new_search_requires_plan(self):
        with self.assertRaises(RoundError) as caught:
            Search.create(fixture_state())
        self.assertEqual(caught.exception.code, 'POEM_PLAN_REQUIRED')

    def test_middle_couplet_content_review_is_line_evidenced(self):
        request = {'raw_request': '五言律诗写山意，第三句化用山意', 'theme': '山意',
                   'poem_type': '五言律诗', 'meter': {'start': '仄起', 'first_line_rhymes': False},
                   'required_fragments': [{'id': 'f1', 'text': '山意', 'kind': 'phrase',
                                           'mode': 'adapt', 'line_index': 3,
                                           'source_excerpt': '第三句化用山意'}]}
        state = PoemState.create(request)
        TemplateLibrary().bind(state)
        state.set_plan(rhyme={'system': '平水韵', 'group': '一东', 'first_line_rhymes': False})
        plan = export_content_plan(state)
        plan['assignments'][0]['reason'] = '用户指定第三句'
        search = Search.create(state, poem_plan=fixture_plan(state), content_plan=plan,
                               engine=fixture_engine())
        stages = []
        for _ in range(100):
            if search.data['status'] != 'waiting_for_llm':
                break
            job = search.next_job()
            stages.append(job['stage'])
            if job['stage'] == 'unit_content':
                self.assertIn('context', job['filtered'])
                for item in job['filtered']['content_request']['requests']:
                    self.assertNotIn('context', item['request'])
            search.submit(response_for(job))
        self.assertEqual(search.data['status'], 'ready_for_review', search.data['events'])
        self.assertIn('unit_content', stages)

    def test_legacy_search_is_auditable_but_read_only(self):
        state = fixture_state()
        current = Search.create(state, poem_plan=fixture_plan(state), engine=fixture_engine()).to_dict()
        legacy = deepcopy(current['search'])
        legacy['version'] = 'poetry-search-1'
        legacy.pop('poem_plan')
        restored = Search.from_dict({'search': legacy, 'sha256': digest(legacy)}, engine=fixture_engine())
        self.assertEqual(restored.data['status'], 'legacy_readonly')
        self.assertIsNone(restored.next_job())
        with self.assertRaises(RoundError):
            restored.submit({})


if __name__ == '__main__':
    unittest.main()
