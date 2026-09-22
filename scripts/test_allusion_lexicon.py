"""Bundled allusion data, resolution policy and relocatable CLI tests."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import allusion_lexicon
from allusion_lexicon import AllusionLexicon, DATA_DIR, normalize_query


def record(aid, name, aliases=(), *, person='甲'):
    return {'allusion_id': aid, 'page_no': 0, 'seq_in_page': aid,
            'raw_nav_name': name + '（' + person + '）', 'name': name,
            'head_persons': [person],
            'aliases': [{'word': value, 'word_id': None} for value in aliases],
            'persons': [person], 'refs': [],
            'sources': [{'book': '测试出处', 'text': '用于确定性测试的原文。'}], 'gloss': ''}


class AllusionLexiconTests(unittest.TestCase):
    def test_unique_exact_alias_resolves_to_canonical_record(self):
        result = AllusionLexicon().resolve('「王质观棋」')
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['record']['allusion_id'], 1073)
        self.assertEqual(result['record']['canonical_name'], '柯烂忘归')
        self.assertEqual(result['record']['matches'], [{'kind': 'alias', 'term': '王质观棋'}])
        self.assertTrue(result['record']['sources'])

    def test_ambiguous_exact_term_never_silently_normalizes(self):
        lexicon = AllusionLexicon([record(1, '甲典', ['同名']), record(2, '乙典', ['同名'])])
        result = lexicon.resolve('同名')
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual([x['canonical_name'] for x in result['choices']], ['甲典', '乙典'])

    def test_fuzzy_result_is_suggestion_not_resolution(self):
        lexicon = AllusionLexicon([record(1, '烂柯', ['王质观棋'])])
        result = lexicon.resolve('王质')
        self.assertEqual(result['status'], 'not_found')
        self.assertEqual(result['suggestions'][0]['canonical_name'], '烂柯')
        self.assertTrue(result['partial_snapshot'])

    def test_search_covers_people_sources_and_normalizes_outer_quotes(self):
        lexicon = AllusionLexicon([record(1, '主典', ['别称'], person='王质')])
        self.assertEqual(normalize_query(' “ 别称 ” '), '别称')
        person = lexicon.search('王质')['results'][0]
        source = lexicon.search('确定性测试')['results'][0]
        self.assertIn('person', {x['kind'] for x in person['matches']})
        self.assertEqual(source['matches'][0]['kind'], 'source_text')

    def test_input_records_are_detached_and_invalid_data_rejected(self):
        raw = [record(1, '主典')]
        lexicon = AllusionLexicon(raw)
        raw[0]['name'] = '已改'
        self.assertEqual(lexicon.resolve('主典')['status'], 'resolved')
        for bad in ([record(1, '甲'), record(1, '乙')], [{**record(1, '甲'), 'sources': []}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                AllusionLexicon(bad)

    def test_bundled_counts_and_digest(self):
        source = json.loads((DATA_DIR / 'source.json').read_text(encoding='utf-8'))
        raw = (DATA_DIR / source['file']).read_bytes()
        rows = [json.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.strip()]
        self.assertEqual(hashlib.sha256(raw).hexdigest(), source['sha256'])
        self.assertEqual(len(rows), source['counts']['allusions'])
        self.assertEqual(sum(len(x['aliases']) for x in rows), source['counts']['aliases'])
        self.assertEqual(sum(len(x['sources']) for x in rows), source['counts']['sources'])
        self.assertEqual(sum(bool(x['gloss']) for x in rows), source['counts']['records_with_gloss'])

    def test_digest_mismatch_stops_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            shutil.copytree(DATA_DIR, data / 'allusions')
            (data / 'allusions' / 'allusions.jsonl').write_bytes(b'changed')
            with patch.object(allusion_lexicon, 'DATA_DIR', data / 'allusions'), self.assertRaisesRegex(ValueError, 'digest'):
                AllusionLexicon()

    def test_cli_runs_after_project_relocation_from_other_working_directory(self):
        project = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / 'relocated'
            shutil.copytree(project, copied, ignore=shutil.ignore_patterns('__pycache__', '.git'))
            completed = subprocess.run(
                [sys.executable, str(copied / 'scripts' / 'allusion_lexicon.py'),
                 'resolve', '--query', '王质观棋'], cwd=Path(tmp), capture_output=True,
                check=True, env={**os.environ, 'PYTHONUTF8': '1'})
            result = json.loads(completed.stdout.decode('utf-8'))
            self.assertEqual(result['record']['canonical_name'], '柯烂忘归')


if __name__ == '__main__':
    unittest.main()
