import unittest

from character_alignment import validate_soft_rating, validate_structure_alignment


class StructuralAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.lines = {3: '山光照远天', 4: '江月明近水'}
        self.alignment = [
            {'upper_span': [1, 2], 'lower_span': [1, 2], 'upper_text': '山光', 'lower_text': '江月',
             'upper_function': '主语', 'lower_function': '主语', 'correspondence': '山水并举',
             'status': 'pass', 'reason': '句法位置相应'},
            {'upper_span': [3, 3], 'lower_span': [3, 3], 'upper_text': '照', 'lower_text': '明',
             'upper_function': '谓语', 'lower_function': '谓语', 'correspondence': '动作与状态映照',
             'status': 'pass', 'reason': '谓语相应'},
            {'upper_span': [4, 5], 'lower_span': [4, 5], 'upper_text': '远天', 'lower_text': '近水',
             'upper_function': '修饰语加中心语', 'lower_function': '修饰语加中心语',
             'correspondence': '远近相对', 'status': 'pass', 'reason': '内部句法结构对应'},
        ]

    def test_complete_structural_partition(self):
        validate_structure_alignment(self.lines, self.alignment, [3, 4], 5)

    def test_structural_gaps_and_wrong_source_text_are_rejected(self):
        broken = [dict(item) for item in self.alignment]
        broken[1]['upper_span'] = [4, 4]
        with self.assertRaises(ValueError):
            validate_structure_alignment(self.lines, broken, [3, 4], 5)
        broken = [dict(item) for item in self.alignment]
        broken[0]['lower_text'] = '错月'
        with self.assertRaises(ValueError):
            validate_structure_alignment(self.lines, broken, [3, 4], 5)

    def test_weak_status_caps_scores_without_rejecting_candidates(self):
        weak = [dict(self.alignment[0], status='weak')]
        validate_soft_rating(weak, .74, 'Structure')
        with self.assertRaises(ValueError):
            validate_soft_rating(weak, .75, 'Structure')
        failed = [dict(self.alignment[0], status='fail')]
        validate_soft_rating(failed, .49, 'Structure')


if __name__ == '__main__':
    unittest.main()
