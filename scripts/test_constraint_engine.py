"""Controlled memberships test rules; bundled memberships test integration."""

from copy import deepcopy
from itertools import product
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import hashlib
import unittest
from unittest.mock import patch

from constraint_engine import ConstraintEngine, check_candidate, check_poem
from poem_state import PoemState
from poem_templates import TemplateLibrary, validate_template
from rhyme_lexicon import DATA_DIR, RhymeLexicon
from text_normalizer import normalize_line
from assessment_binding import BINDING_FIELDS
from reading_disambiguation import make_request, apply_review, recheck_candidate


FIXTURE = {'上平声部': {'一东': list('天地人风同𠀀'), '二冬': ['同', '冬']},
           '上声部': {'一董': list('水火土风')}}


class ConstraintTests(unittest.TestCase):
    def setUp(self):
        self.library = TemplateLibrary()
        self.engine = ConstraintEngine(RhymeLexicon(FIXTURE))

    def state(self, template=None):
        template = template or self.library.candidates('五言绝句', meter={'pattern': '仄起首句不入韵'})[0]
        state = PoemState.create({'raw_request': '测试', 'theme': '测试', 'poem_type': template['type'],
                                  'meter': {'template_id': template['id']}})
        state.bind_template(template, self.library.sha256)
        state.set_plan(rhyme={'system': '平水韵', 'group': '一东',
                              'first_line_rhymes': template['rhyme']['first_line_rhyme']})
        return state

    def candidate(self, text, state=None, line=2, readings=None):
        return self.engine.check_candidate({'id': 'c', 'text': text, 'line_index': line},
                                           state or self.state(), readings=readings)

    def test_all_sixteen_templates_and_candidate_positions(self):
        templates = self.library.candidates()
        self.assertEqual(len(templates), 16)
        for template in templates:
            with self.subTest(template=template['id']):
                state = self.state(template)
                for i, pattern in enumerate(validate_template(template), 1):
                    # Exhaust synthetic tone strings, not generated literary poems.
                    texts = (''.join(chars) for chars in product('天水', repeat=len(pattern)))
                    text = next(t for t in texts if self.candidate(t, state, i)['valid'] is True)
                    state.append_line(text)
                before = state.to_dict()
                result = self.engine.check_poem(state)
                self.assertTrue(result['valid'])
                self.assertEqual(state.to_dict(), before)
                self.assertEqual(result['revision'], before['revision'])
                self.assertFalse(result['checks']['duplicate']['required'])
                state.set_assessment(result)

    def test_length_shape_invalid_char_and_codepoints(self):
        for text, code in [('天天水水', 'LINE_LENGTH'), ('天天，水水天', 'LINE_SEGMENTS'),
                           ('天A水水天', 'INVALID_CHARACTER'), ('天1水水天', 'INVALID_CHARACTER'),
                           ('天😀水水天', 'INVALID_CHARACTER')]:
            r = self.candidate(text)
            self.assertFalse(r['valid'])
            self.assertIn(code, [x['code'] for x in r['checks']['length']['issues']])
        self.assertTrue(self.candidate(' 𠀀天水水天。')['valid'])
        line = normalize_line(' 𠀀天水水天。', 2)
        self.assertEqual(line['cells'][0]['offset'], 1)
        self.assertEqual(len(line['cells']), 5)
        self.assertEqual(line['original'], ' 𠀀天水水天。')

    def test_incomplete_poem(self):
        r = self.engine.check_poem(self.state())
        self.assertFalse(r['valid'])
        self.assertEqual(r['checks']['length']['issues'][0]['code'], 'LINE_COUNT')

    def test_fixed_mismatch_and_rhyme_mismatch(self):
        r = self.candidate('天水水水天')
        issue = next(x for x in r['checks']['pingze']['issues'] if x['code'] == 'TONE_MISMATCH')
        self.assertEqual((issue['line'], issue['pos']), (2, 2))
        r = self.candidate('天天水水冬')
        self.assertEqual(r['checks']['rhyme']['status'], 'fail')
        self.assertEqual(r['checks']['rhyme']['issues'][0]['pos'], 5)

    def test_missing_group_character_ambiguous_membership(self):
        state = self.state()
        rhyme = state.to_dict()['rhyme']; rhyme['group'] = None
        state.set_plan(rhyme=rhyme)
        self.assertIsNone(self.candidate('天天水水天', state)['valid'])
        self.assertIsNone(self.candidate('龘天水水天')['valid'])
        self.assertIsNone(self.candidate('天风水水天')['valid'])
        r = self.candidate('天天水水同')
        self.assertEqual(r['checks']['pingze']['status'], 'pass')
        self.assertEqual(r['checks']['rhyme']['status'], 'unknown')

    def test_position_readings_shared_by_checks(self):
        r = self.candidate('天风水水风', readings={'2:2': {'section': '上平声部'},
                                                  '2:5': {'section': '上声部'}})
        self.assertEqual(r['checks']['rhyme']['status'], 'fail')
        positions = r['checks']['pingze']['lines'][0]['positions']
        self.assertEqual(positions[1]['status'], 'pass')
        self.assertEqual(positions[4]['status'], 'fail')
        for reading in ('feng1', {'group': '不存在'}):
            self.assertIsNone(self.candidate('天天水水风', readings={'2:5': reading})['valid'])

    def test_ambiguous_character_emits_senses_and_requires_review(self):
        lexicon = RhymeLexicon({
            '上平声部': {'一东': [{'char': '风', 'senses': ['吹动的空气']}]},
            '上声部': {'一董': [{'char': '风', 'senses': ['讽诵']}]},
        })
        engine = ConstraintEngine(lexicon)
        candidate = {'id': 'c', 'text': '风风风风风', 'line_index': 2}
        result = engine.check_candidate(candidate, self.state())
        self.assertIsNone(result['valid'])
        issue = result['checks']['reading']['issues'][0]
        self.assertEqual(issue['coordinate'], '2:1')
        self.assertEqual({tuple(x['senses']) for x in issue['options']},
                         {('吹动的空气',), ('讽诵',)})
        self.assertEqual(result['checks']['reading']['context']['theme'], '测试')

    def test_bound_ai_reading_review_is_rechecked_by_rules(self):
        result = self.candidate('天风水水天')
        request = make_request(result)
        decisions = []
        for occurrence in request['occurrences']:
            option = next(x for x in occurrence['options'] if x['section'] == '上平声部')
            decisions.append({'coordinate': occurrence['coordinate'], 'char': occurrence['char'],
                              'status': 'selected', 'section': option['section'],
                              'group': option['group'], 'reason': '句中取平声义。'})
        payload = {'review_version': request['review_version'],
                   'request_sha256': request['request_sha256'], 'decisions': decisions}
        reviewed = apply_review(request, payload)
        self.assertTrue(reviewed['complete'])
        checked = recheck_candidate(self.engine,
                                    {'id': 'c', 'text': '天风水水天', 'line_index': 2},
                                    self.state(), request, payload)
        self.assertTrue(checked['result']['valid'])
        tampered = deepcopy(payload)
        tampered['decisions'][0]['group'] = '不存在'
        with self.assertRaises(ValueError):
            apply_review(request, tampered)

    def test_ai_may_keep_reading_unknown(self):
        result = self.candidate('天风水水天')
        request = make_request(result)
        payload = {'review_version': request['review_version'],
                   'request_sha256': request['request_sha256'],
                   'decisions': [{'coordinate': x['coordinate'], 'char': x['char'],
                                  'status': 'unknown', 'section': None, 'group': None,
                                  'reason': '上下文不足。'} for x in request['occurrences']]}
        reviewed = apply_review(request, payload)
        self.assertFalse(reviewed['complete'])
        self.assertEqual(reviewed['readings'], {})

    def test_rules_apply_to_zhong(self):
        r = self.candidate('水天水水天')
        self.assertEqual(r['checks']['pingze']['status'], 'fail')
        self.assertIn('GUPING', [x['code'] for x in r['checks']['pingze']['issues']])
        r = self.candidate('水天天水天')
        self.assertIsNone(r['valid'])  # recognised self-rescue, not implemented
        r = self.candidate('水水天天天', line=4)
        self.assertIn('THREE_LEVEL_ENDING', [x['code'] for x in r['checks']['pingze']['issues']])
        r = self.candidate('水水风天天', line=4)
        self.assertEqual(r['checks']['pingze']['status'], 'unknown')

    def test_same_additional_rules_for_all_forms(self):
        for form in ('五言绝句', '五言律诗', '七言绝句', '七言律诗'):
            template = self.library.candidates(form)[0]
            template['constraints'] = []  # universal rules cannot be switched off by omission
            state = self.state(template)
            patterns = validate_template(template)
            for index, pattern in enumerate(patterns, 1):
                prefix = ''.join('天' if t == '平' else '水' for t in pattern[:-5])
                if pattern[-4] == '平' and pattern[-2:] == '仄平':
                    r = self.candidate(prefix + '水天水水天', state, index)
                    self.assertIn('GUPING', [x['code'] for x in r['checks']['pingze']['issues']])
                    r = self.candidate(prefix + '水天天水天', state, index)
                    self.assertIsNone(r['valid'])
                if pattern[-2:] == '平平':
                    r = self.candidate(prefix + '水水天天天', state, index)
                    self.assertIn('THREE_LEVEL_ENDING', [x['code'] for x in r['checks']['pingze']['issues']])

    def test_unavailable_rhyme_group_and_other_system(self):
        for system, group, code in [('平水韵', '三讲', 'RHYME_GROUP_UNAVAILABLE'),
                                    ('新韵', '一东', 'UNSUPPORTED_RHYME_SYSTEM')]:
            state = self.state()
            rhyme = state.to_dict()['rhyme']; rhyme.update(system=system, group=group)
            state.set_plan(rhyme=rhyme)
            r = self.candidate('天天水水天', state)
            self.assertIsNone(r['valid'])
            self.assertEqual(r['checks']['rhyme']['issues'][0]['code'], code)

    def test_snapshot_change_changes_verdict_without_library_change(self):
        state = self.state()
        template = state.to_dict()['meter_template']['data']
        for cell in template['tunes']:
            if (cell['line'], cell['pos']) == (2, 1):
                cell['tune'] = '仄'
        state.bind_template(template, self.library.sha256)
        r = self.candidate('天天水水天', state)
        self.assertFalse(r['valid'])
        self.assertEqual(r['checks']['pingze']['issues'][0]['pos'], 1)

    def test_unsupported_rule_and_missing_snapshot(self):
        template = self.library.candidates('五言绝句')[0]
        template['constraints'].append('尚未实现规则')
        self.assertIsNone(self.candidate('天天水水天', self.state(template))['valid'])
        state = PoemState.create({'raw_request': '测试', 'theme': '测试', 'poem_type': '五言绝句'})
        self.assertIsNone(self.candidate('天天水水天', state)['valid'])

    def test_duplicate_replacement_policy_and_nonrhyme(self):
        state = self.state(); state.append_line('天水人地火')
        r = self.candidate('天水人地火', state, 1)
        self.assertTrue(r['valid'])
        self.assertEqual(r['checks']['rhyme']['status'], 'not_applicable')
        self.assertEqual(r['checks']['duplicate']['occurrences'], [])
        r = self.candidate('天天水水天')
        self.assertTrue(r['valid'])
        PoemState._validate_assessment(r, r['revision'], full_poem=False)
        state.set_plan(constraints=['禁止重复字'])
        self.assertFalse(self.candidate('天天水水天', state)['valid'])

    def test_snapshot_does_not_reload_library(self):
        state = self.state()
        with patch('poem_templates.TemplateLibrary', side_effect=AssertionError('Library reloaded')):
            r = self.candidate('天天水水天', state.to_dict())
            self.assertTrue(r['valid'])

    def test_stale_and_legacy_required(self):
        state = self.state()
        state.record_candidate('c', '天天水水天', 2)
        r = self.candidate('天天水水天', state)
        state.evaluate_candidate('c', checks=r, decision='retained')
        state.append_line('天水人地火')
        with self.assertRaises(ValueError):
            state.evaluate_candidate('c', checks=r)
        with self.assertRaises(ValueError):
            state.set_assessment(r)
        old = deepcopy(r)
        old['checks']['duplicate'].pop('required')
        with self.assertRaises(ValueError):
            PoemState._validate_assessment(old, old['revision'], full_poem=False)
        old['valid'] = False
        PoemState._validate_assessment(old, old['revision'], full_poem=False)
        for value in (None, 0, 'false'):
            bad = deepcopy(r); bad['checks']['duplicate']['required'] = value
            with self.assertRaises(ValueError):
                PoemState._validate_assessment(bad, bad['revision'], full_poem=False)
        bad = deepcopy(r); bad['checks']['reading']['required'] = False
        with self.assertRaises(ValueError):
            PoemState._validate_assessment(bad, bad['revision'], full_poem=False)

    def test_call_errors(self):
        for line in (0, 5, True):
            with self.assertRaises(ValueError): self.candidate('天天水水天', line=line)
        for readings in ({'天': 'tian1'}, {'2:6': 'tian1'}, {'2:1': {'tone': '平'}}):
            with self.assertRaises(ValueError): self.candidate('天天水水天', readings=readings)
        with self.assertRaises(ValueError): self.candidate('')
        with self.assertRaises(ValueError): check_poem({'revision': 0})

    def test_real_lexicon_and_external_working_directory(self):
        lexicon = RhymeLexicon()
        self.assertEqual(len(lexicon.groups), 106)
        self.assertGreaterEqual(len(lexicon.lookup('重')), 3)
        self.assertEqual(lexicon.lookup('月')[0]['tone'], '仄')
        self.assertEqual(len(lexicon.lookup('东')), 1)
        r = check_candidate({'id': 'real', 'text': '东东月月东', 'line_index': 2}, self.state())
        self.assertTrue(r['valid'])
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / 'relocated'
            shutil.copytree(Path(__file__).resolve().parent.parent, copied,
                            ignore=shutil.ignore_patterns('__pycache__'))
            state_path = Path(tmp) / 'state.json'; self.state().save(state_path)
            candidate = Path(tmp) / 'candidate.json'
            candidate.write_text(json.dumps({'id': 'real', 'text': '东东月月东', 'line_index': 2}), encoding='utf-8')
            process = subprocess.run([sys.executable, str(copied / 'scripts' / 'constraint_engine.py'),
                                      '--state', str(state_path), '--candidate', str(candidate)],
                                     cwd=tmp, capture_output=True, check=True)
            self.assertTrue(json.loads(process.stdout)['valid'])

    def test_replacement_source_integrity_and_sanjiang(self):
        lexicon = RhymeLexicon()
        source = json.loads((DATA_DIR / 'source.json').read_text(encoding='utf-8'))
        raw = (DATA_DIR / source['file']).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), source['sha256'])
        self.assertEqual(source['schema_version'], 3)
        self.assertIsNone(source['source_url'])
        self.assertEqual(source['counts']['records'], 6605)
        expected = set('讲港项蚌傋棒夯玤庬')
        data = json.loads(raw)
        self.assertIn('三讲', data['上声部'])
        self.assertEqual({r['char'] for r in data['上声部']['三讲']}, expected)
        self.assertEqual(len(data['上声部']['三讲']), 9)
        for char in expected:
            self.assertIn({'section': '上声部', 'group': '三讲', 'tone': '仄'}, lexicon.lookup(char))
        self.assertEqual(lexicon.group_records('上声部/三讲'), {('上声部', '三讲')})
        data['上声部'].pop('三讲')
        base = RhymeLexicon(data)
        self.assertEqual(len(base.groups), 105)
        for char in expected:
            for old_record in base.lookup(char):
                self.assertIn(old_record, lexicon.lookup(char))
        # Gloss characters are not accidentally treated as rhyme entries.
        for char in '水会也':
            self.assertNotIn({'section': '上声部', 'group': '三讲', 'tone': '仄'}, lexicon.lookup(char))
        self.assertNotIn({'section': '上声部', 'group': '三讲', 'tone': '仄'},
                         lexicon.lookup('漎'))
        r = check_candidate({'id': 'mismatch', 'text': '东讲月月东', 'line_index': 2}, self.state())
        self.assertFalse(r['valid'])
        self.assertEqual(r['checks']['pingze']['lines'][0]['positions'][1]['status'], 'fail')

    def test_rhyme_data_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / 'rhyme'
            shutil.copytree(DATA_DIR, copied)
            (copied / 'Pingshui_Rhyme.json').write_text('{}', encoding='utf-8')
            with patch('rhyme_lexicon.DATA_DIR', copied):
                with self.assertRaisesRegex(ValueError, 'Rhyme data digest mismatch'):
                    RhymeLexicon()

    def full_state(self):
        state = self.state()
        for text in ('月月东东月', '东东月月东', '东东东月月', '月月月东东'):
            state.append_line(text)
        return state

    def test_candidate_result_cannot_complete_invalid_poem(self):
        state = self.state()
        for text in ('东', '东东月月东', '东', '东'):
            state.append_line(text)
        state.set_status('reviewing')
        candidate = {'id': 'good', 'text': '东东月月东', 'line_index': 2}
        result = check_candidate(candidate, state)
        self.assertTrue(result['valid'])
        self.assertFalse(check_poem(state)['valid'])
        before = state.to_dict()
        with self.assertRaisesRegex(ValueError, 'object or context'):
            state.set_assessment(result)
        with self.assertRaises(ValueError): state.set_status('completed')
        self.assertEqual(state.to_dict(), before)

    def test_candidate_binding_checks_id_text_and_line(self):
        state = self.state()
        candidate = {'id': 'good', 'text': '东东月月东', 'line_index': 2}
        result = check_candidate(candidate, state)
        # Register after checking: candidate bookkeeping must not alter context digest.
        state.record_candidate('good', candidate['text'], 2)
        state.record_candidate('bad', '东', 2)
        state.record_candidate('same-text', candidate['text'], 2)
        for target in ('bad', 'same-text'):
            before = state.to_dict()
            with self.assertRaises(ValueError):
                state.evaluate_candidate(target, checks=result, decision='retained')
            self.assertEqual(state.to_dict(), before)
        for text, line in (('东', 2), (candidate['text'], 4), (' 东东月月东。', 2)):
            branch = self.state()
            branch.record_candidate('good', text, line)
            self.assertEqual(branch.to_dict()['revision'], state.to_dict()['revision'])
            with self.assertRaises(ValueError):
                branch.evaluate_candidate('good', checks=result, decision='retained')
        state.evaluate_candidate('good', checks=result, decision='retained')
        self.assertEqual(state.to_dict()['candidate_history'][0]['decision'], 'retained')

    def test_same_revision_branches_reject_cross_context_results(self):
        original = self.full_state()
        for field in ('lines', 'rhyme', 'constraints', 'meter_template'):
            with self.subTest(field=field):
                left, right = original.fork(), original.fork()
                if field == 'lines':
                    left.replace_line(1, '月月东东月。')
                    right.replace_line(1, '月月东东月！')
                elif field == 'rhyme':
                    left.set_plan(rhyme={**left.to_dict()['rhyme'], 'group': '上平声部/一东'})
                    right.set_plan(rhyme={**right.to_dict()['rhyme'], 'group': '二冬'})
                elif field == 'constraints':
                    left.set_plan(constraints=['提示重复字'])
                    right.set_plan(constraints=['禁止重复字'])
                else:
                    # Same template contents but a different bound source snapshot.
                    t = original.to_dict()['meter_template']['data']
                    left.bind_template(t, 'a' * 64)
                    right.bind_template(t, 'b' * 64)
                self.assertEqual(left.to_dict()['revision'], right.to_dict()['revision'])
                candidate = {'id': 'c', 'text': '东东月月东', 'line_index': 2}
                result = check_candidate(candidate, left)
                poem_result = check_poem(left)
                right.record_candidate('c', candidate['text'], 2)
                before = right.to_dict()
                with self.assertRaises(ValueError): right.set_assessment(poem_result)
                with self.assertRaises(ValueError):
                    right.evaluate_candidate('c', checks=result, decision='retained')
                self.assertEqual(before, right.to_dict())

    def test_bound_full_poem_can_complete_and_roundtrip(self):
        state = self.full_state()
        result = check_poem(state)
        self.assertTrue(result['valid'])
        self.assertEqual(result['scope'], 'poem')
        state.record_candidate('c', '东东月月东', 2)
        with self.assertRaises(ValueError):
            state.evaluate_candidate('c', checks=result, decision='retained')
        state.set_assessment(result)
        state.set_status('reviewing')
        state.set_status('completed')
        self.assertEqual(PoemState.from_dict(state.to_dict()).to_dict(), state.to_dict())

    def test_legacy_results_readable_but_not_new_promotions(self):
        state = self.full_state()
        candidate = {'id': 'c', 'text': '东东月月东', 'line_index': 2}
        state.record_candidate('c', candidate['text'], 2)
        old_poem = {k: v for k, v in check_poem(state).items() if k not in BINDING_FIELDS}
        old_candidate = {k: v for k, v in check_candidate(candidate, state).items() if k not in BINDING_FIELDS}
        data = state.to_dict()
        data['checks'] = old_poem
        data['status'] = 'reviewing'
        data['candidate_history'][0].update(checks=old_candidate, decision='retained')
        restored = PoemState.from_dict(data)
        self.assertEqual(restored.to_dict(), data)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'old.json'; restored.save(path)
            self.assertEqual(PoemState.load(path).to_dict(), data)
        before = restored.to_dict()
        with self.assertRaises(ValueError): restored.set_status('completed')
        with self.assertRaises(ValueError): restored.set_assessment(old_poem)
        with self.assertRaises(ValueError):
            restored.evaluate_candidate('c', checks=old_candidate, decision='retained')
        self.assertEqual(restored.to_dict(), before)
        # Already completed legacy snapshots retain their historical status on read.
        data['status'] = 'completed'
        self.assertEqual(PoemState.from_dict(data).to_dict()['status'], 'completed')
        restored.set_assessment(check_poem(restored))
        restored.set_status('completed')

    def test_binding_validation_and_history(self):
        state = self.full_state()
        result = check_poem(state)
        for key, value in (('scope', 'candidate'), ('binding_version', True),
                           ('context_sha256', 'x' * 64), ('subject', {})):
            bad = deepcopy(result); bad[key] = value
            with self.assertRaises(ValueError): state.set_assessment(bad)
        bad = deepcopy(result); del bad['scope']
        with self.assertRaises(ValueError): state.set_assessment(bad)
        # Raw state restoration also refuses bound results for a different object.
        data = state.to_dict(); data['checks'] = result; data['lines'][0] += '。'
        with self.assertRaises(ValueError): PoemState.from_dict(data)
        candidate = {'id': 'c', 'text': '东东月月东', 'line_index': 2}
        state.record_candidate('c', candidate['text'], 2)
        state.evaluate_candidate('c', checks=check_candidate(candidate, state), decision='retained')
        state.replace_line(1, '月月东东月。')
        self.assertEqual(PoemState.from_dict(state.to_dict()).to_dict(), state.to_dict())


if __name__ == '__main__':
    unittest.main()
