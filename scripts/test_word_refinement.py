import unittest

from day4_example import fixture_state
from word_refinement import VERSION, structural_units, refinement_request, validate_refinement


class WordRefinementTests(unittest.TestCase):
    def request(self, form='五言绝句'):
        state = fixture_state(form)
        count = state.to_dict()['form']['line_count']
        for i in range(count):
            state.append_line('天地人水火' if state.to_dict()['form']['line_length'] == 5 else '天地人水火天地')
        unit = structural_units(state)[0]
        lock = {'protected_meaning': '保持原意', 'facts': [], 'images': ['山'], 'relations': [],
                'emotion_direction': '平静', 'structural_function': unit['unit'],
                'confirmed_allusions': []}
        return refinement_request(state, unit, lock, [])

    def payload(self, request, status='NO_CHANGE'):
        result = {'lines': request['original_lines']}
        if status == 'REFLOW_REQUIRED':
            result = {'problem_type': '整句语流不自然', 'protected_content': ['保持原意'],
                      'suggested_scope': 'couplet' if len(request['original_lines']) == 2 else 'line'}
        return {'refinement_version': VERSION, 'request_sha256': request['request_sha256'],
                'status': status, 'reason': '固定测试理由', 'result': result}

    def refined(self, request):
        lines = [dict(x) for x in request['original_lines']]
        old = lines[0]['text'][0]
        lines[0]['text'] = '山' + lines[0]['text'][1:]
        return {'refinement_version': VERSION, 'request_sha256': request['request_sha256'],
                'status': 'REFINED', 'reason': '首字更贴合核心意象', 'result': {
                    'lines': lines, 'changes': [{'line_index': lines[0]['line_index'], 'position': 1,
                        'original': old, 'selected': '山', 'candidates': [old, '山'], 'reason': '意象更具体'}],
                    'net_gain': {'dimension': '意象', 'reason': '由泛指转为具体山景', 'evidence': '山'},
                    'tradeoffs': ['语气稍凝重'], 'semantic_lock_preserved': True}}

    def test_units_and_no_change(self):
        self.assertEqual(len(structural_units(fixture_state('五言绝句'))), 4)
        self.assertEqual([x['line_indices'] for x in structural_units(fixture_state('五言律诗'))],
                         [[1, 2], [3, 4], [5, 6], [7, 8]])
        request = self.request()
        self.assertEqual(validate_refinement(self.payload(request), request)['status'], 'NO_CHANGE')

    def test_refined_requires_original_and_exact_declared_scope(self):
        request = self.request()
        payload = self.refined(request)
        self.assertEqual(validate_refinement(payload, request)['status'], 'REFINED')
        for mutation in ('original_missing', 'undeclared', 'lock', 'no_gain'):
            bad = self.refined(request)
            if mutation == 'original_missing':
                bad['result']['changes'][0]['candidates'] = ['山']
            elif mutation == 'undeclared':
                bad['result']['lines'][0]['text'] = '山地人水山'
            elif mutation == 'lock':
                bad['result']['semantic_lock_preserved'] = False
            else:
                bad['result']['net_gain']['reason'] = ''
            with self.assertRaises(ValueError, msg=mutation):
                validate_refinement(bad, request)

    def test_reflow_scope_is_current_unit(self):
        request = self.request('五言律诗')
        self.assertEqual(validate_refinement(self.payload(request, 'REFLOW_REQUIRED'), request)['status'],
                         'REFLOW_REQUIRED')
        bad = self.payload(request, 'REFLOW_REQUIRED')
        bad['result']['suggested_scope'] = 'line'
        with self.assertRaises(ValueError):
            validate_refinement(bad, request)


if __name__ == '__main__':
    unittest.main()
