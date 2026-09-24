import unittest

from duplicate_scoring import adjust_full_poem_score, adjust_score, duplicate_group_count


class DuplicateScoringTests(unittest.TestCase):
    def test_first_repeated_character_group_is_free_and_each_next_group_costs_point_two(self):
        self.assertEqual(duplicate_group_count(['天地天']), 1)
        self.assertEqual(duplicate_group_count(['天地天水地']), 2)
        score, details = adjust_full_poem_score(.9, ['天地天水地'])
        self.assertAlmostEqual(score, .7)
        self.assertEqual(details['duplicate_score_penalty'], .2)

    def test_candidate_applies_only_the_new_group_penalty(self):
        score, details = adjust_score(.8, ['天地天'], ['天地天水地'])
        self.assertAlmostEqual(score, .6)
        self.assertEqual(details['duplicate_score_penalty'], .2)

    def test_removing_a_group_restores_only_its_penalty(self):
        score, details = adjust_score(.6, ['天地天水地'], ['天地天'])
        self.assertAlmostEqual(score, .8)
        self.assertEqual(details['duplicate_score_relief'], .2)


if __name__ == '__main__':
    unittest.main()
