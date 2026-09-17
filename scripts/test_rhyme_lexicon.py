"""Source extraction, annotated memberships and complete offline table checks."""

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from rhyme_lexicon import AmbiguousRhymeGroup, DATA_DIR, RhymeLexicon
from scrape_pingshui import (Document, Fetcher, PAGE_URL, catalog, extract_entries,
                             json_bytes, merge_entries, parse_page, parse_uncommon, project_path)
from runtime_workspace import configure_workspace


def synthetic_page():
    groups = ''.join(f'<li id="{g["id"]}" y="{g["label"]}" style="display:none">'
                     '<a>忠</a><a class="btnLoadUncommon" type="psy">显示生僻字</a></li>'
                     for g in catalog())
    return '<ul id="psy-word-list" class="extra word-list">' + groups + '</ul>'


class ExtractionTests(unittest.TestCase):
    def test_direct_text_and_em_are_separate(self):
        node = Document('<a> 忠 </a><a>珑</a><a>\n丰 <em>丰收</em>\t</a>'
                        '<a>𪁒<em> 义项 <b>一</b> </em><em>义项二</em></a>'
                        '<a>东<span>不能混入字形</span></a>'
                        '<a class="btnLoadUncommon" type="psy">显示生僻字</a>').root
        records, controls = extract_entries(node)
        self.assertEqual(records, [{'char': '忠', 'senses': []}, {'char': '珑', 'senses': []},
                                   {'char': '丰', 'senses': ['丰收']},
                                   {'char': '𪁒', 'senses': ['义项 一', '义项二']},
                                   {'char': '东', 'senses': []}])
        self.assertEqual(controls, 1)

    def test_entities_and_duplicate_senses_preserve_source_records(self):
        records, _ = extract_entries(Document('<a>&#20016;<em>丰&#25910;</em></a>'
                                               '<a>丰<em>丰采</em></a>').root)
        self.assertEqual(records, [{'char': '丰', 'senses': ['丰收']},
                                   {'char': '丰', 'senses': ['丰采']}])

    def test_invalid_anchor_is_not_silently_dropped(self):
        for html in ('<a>丰收</a>', '<a><em>丰收</em></a>', '<a>丰<em> </em></a>'):
            with self.subTest(html=html), self.assertRaises(ValueError):
                extract_entries(Document(html).root)

    def test_common_and_uncommon_duplicates_merge_without_losing_senses(self):
        common = [{'char': '丰', 'senses': ['丰收']}, {'char': '忠', 'senses': []}]
        rare = [{'char': '丰', 'senses': ['丰收', '另一义项']}, {'char': '忠', 'senses': []}]
        before = deepcopy(common + rare)
        self.assertEqual(merge_entries(common + rare),
                         [{'char': '丰', 'senses': ['丰收', '另一义项']}, {'char': '忠', 'senses': []}])
        self.assertEqual(common + rare, before)

    def test_all_hidden_groups_and_only_pingshui(self):
        html = '<ul id="clzy-word-list"><li><a>错字</a></li></ul>' + synthetic_page()
        groups = parse_page(html)
        self.assertEqual(len(groups), 106)
        self.assertEqual(Counter(g['section'] for g in groups),
                         dict(zip(('上平声部', '下平声部', '上声部', '去声部', '入声部'), (15, 15, 29, 30, 17))))
        self.assertEqual(groups[57]['label'], '二十八俭上声')
        self.assertEqual(groups[57]['group'], '二十八琰')
        self.assertEqual(groups[-1]['group'], '入声十七洽')

    def test_missing_or_reordered_groups_rejected(self):
        html = synthetic_page()
        for bad in (html.replace('psy-word-list', 'other'),
                    html.replace('psy1"', 'psy2"', 1),
                    html.replace('一东平声', '二冬平声'),
                    html[:html.rfind('<li')]+ '</ul>',
                    html.replace('word-list', 'other')):
            with self.assertRaises(ValueError):
                parse_page(bad)

    def test_uncommon_response_and_api_failure(self):
        self.assertEqual(parse_uncommon(json.dumps({'status': 'ok', 'str': '<a>漎<em>水会也</em></a>'})),
                         [{'char': '漎', 'senses': ['水会也']}])
        self.assertEqual(parse_uncommon('{"status":"ok","str":""}'), [])
        for raw in ('<html>blocked</html>', '{"status":"error","message":"later"}',
                    '{"status":"ok","str":"<p>blocked</p>"}', '[]'):
            with self.assertRaises(ValueError):
                parse_uncommon(raw)

    def test_offline_cache_checks_integrity_and_never_fetches(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            raw = b'public response'
            meta = dict(url=PAGE_URL, parameters=None, method='GET', sha256=hashlib.sha256(raw).hexdigest())
            (cache / 'yun.html').write_bytes(raw)
            (cache / 'yun.html.meta.json').write_bytes(json_bytes(meta))
            fetcher = Fetcher(cache, offline=True)
            with patch('scrape_pingshui.urlopen', side_effect=AssertionError('Network used')):
                self.assertEqual(fetcher.fetch('yun.html', PAGE_URL), raw)
                with self.assertRaisesRegex(ValueError, 'Offline response missing'):
                    fetcher.fetch('missing', PAGE_URL)
                (cache / 'yun.html').write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError, 'Cached response mismatch'):
                    fetcher.fetch('yun.html', PAGE_URL)

    def test_paths_stay_in_project(self):
        self.assertEqual(project_path('data/rhyme'), DATA_DIR)
        with self.assertRaises(ValueError):
            project_path('../outside')

    def test_network_failures_stop_after_three_attempts_without_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            with patch('scrape_pingshui.urlopen', side_effect=URLError('unavailable')) as request, \
                    patch('scrape_pingshui.time.sleep'):
                with self.assertRaises(URLError):
                    Fetcher(cache).fetch('yun.html', PAGE_URL)
            self.assertEqual(request.call_count, 3)
            self.assertEqual(list(cache.iterdir()), [])

    def test_missing_response_does_not_replace_existing_outputs(self):
        from scrape_pingshui import main
        self.addCleanup(configure_workspace, None)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            output = project / 'data' / 'rhyme'
            output.mkdir(parents=True)
            for name in ('Pingshui_Rhyme.json', 'source.json'):
                (output / name).write_bytes(b'original')
            with patch('scrape_pingshui.PROJECT', project), \
                    patch('sys.argv', ['scrape_pingshui.py', '--offline',
                                       '--work-dir', str(project / 'work')]), \
                    patch('scrape_pingshui.urlopen', side_effect=AssertionError('Network used')), \
                    patch('sys.stderr'):
                with self.assertRaises(SystemExit) as error:
                    main()
            self.assertEqual(error.exception.code, 2)
            self.assertTrue(all(p.read_bytes() == b'original' for p in output.iterdir()))


class AnnotatedLexiconTests(unittest.TestCase):
    def test_unique_group_alias_normalizes_and_ambiguity_is_explicit(self):
        lexicon = RhymeLexicon({
            '上平声部': {'一东': ['甲'], '十一真': ['乙']},
            '下平声部': {'二东': ['丙']},
        })
        self.assertEqual(lexicon.normalize_group('真'), '十一真')
        self.assertEqual(lexicon.normalize_group('十一真'), '十一真')
        self.assertEqual(lexicon.normalize_group('上平声部/真'), '十一真')
        self.assertEqual(lexicon.group_records('真'), {('上平声部', '十一真')})
        self.assertIsNone(lexicon.normalize_group('不存在'))
        with self.assertRaises(AmbiguousRhymeGroup) as error:
            lexicon.normalize_group('东')
        self.assertEqual(set(error.exception.choices),
                         {'上平声部/一东', '下平声部/二东'})
        self.assertEqual(lexicon.group_records('东'), set())

    def test_legacy_calls_and_membership_scoped_senses(self):
        data = {'上平声部': {'一东': ['忠', {'char': '丰', 'senses': ['丰收']},
                                      {'char': '丰', 'senses': ['丰收', '另一义项']}],
                              '二冬': [{'char': '丰', 'senses': ['丰采']}]},
                '去声部': {'二宋': [{'char': '丰', 'senses': []}]}}
        before = deepcopy(data)
        lexicon = RhymeLexicon(data)
        self.assertEqual(lexicon.lookup('忠'), [{'section': '上平声部', 'group': '一东', 'tone': '平'}])
        self.assertEqual(len(lexicon.lookup('丰')), 3)
        detailed = lexicon.lookup('丰', include_senses=True)
        self.assertEqual([r['senses'] for r in detailed], [['丰收', '另一义项'], ['丰采'], []])
        detailed[0]['senses'].clear()
        self.assertEqual(lexicon.lookup('丰', include_senses=True)[0]['senses'], ['丰收', '另一义项'])
        self.assertEqual(data, before)
        self.assertEqual(lexicon.resolve('丰', {'group': '二冬'}),
                         ([{'section': '上平声部', 'group': '二冬', 'tone': '平'}], None))
        self.assertEqual(lexicon.resolve('丰', 'feng1')[1], 'READING_MAPPING_UNAVAILABLE')
        self.assertEqual(lexicon.resolve('龘')[1], 'CHAR_NOT_FOUND')

    def test_invalid_record_schema_rejected(self):
        for entry in ({'char': '丰'}, {'char': '丰收', 'senses': []},
                      {'char': '丰', 'senses': '丰收'}, {'char': '丰', 'senses': ['']},
                      {'char': '丰', 'senses': [None]}, {'char': '丰', 'senses': [], 'pinyin': 'feng1'}):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                RhymeLexicon({'上平声部': {'一东': [entry]}})

    def test_real_zhong_long_feng_and_polyphonic_zhong(self):
        lexicon = RhymeLexicon()
        for char in '忠珑':
            records = lexicon.lookup(char, include_senses=True)
            self.assertTrue(records)
            self.assertTrue(all(r['senses'] == [] for r in records))
        self.assertEqual({r['group']: r['senses'] for r in lexicon.lookup('丰', include_senses=True)},
                         {'一东': ['丰收'], '二冬': ['丰采']})
        self.assertEqual({r['group']: r['senses'] for r in lexicon.lookup('重', include_senses=True)},
                         {'二冬': ['重复'], '二肿': ['郑重'], '二宋': ['轻重']})

    def test_bundled_replacement_counts_and_integrity(self):
        data = json.loads((DATA_DIR / 'Pingshui_Rhyme.json').read_text(encoding='utf-8'))
        source = json.loads((DATA_DIR / 'source.json').read_text(encoding='utf-8'))
        records = [r for section in data.values() for group in section.values() for r in group]
        counts = source['counts']
        self.assertEqual(counts['group_count'], 106)
        self.assertEqual(sum(len(v) for v in data.values()), counts['group_count'])
        self.assertEqual(counts['section_record_counts'],
                         {s: sum(len(items) for items in groups.values())
                          for s, groups in data.items()})
        self.assertEqual(counts['records'], len(records))
        self.assertEqual(counts['unique_characters'], len({r['char'] for r in records}))
        memberships = {}
        for section, groups in data.items():
            for group, items in groups.items():
                for record in items:
                    memberships.setdefault(record['char'], set()).add((section, group))
        self.assertEqual(counts['characters_with_multiple_memberships'],
                         sum(len(items) > 1 for items in memberships.values()))
        self.assertEqual(counts['records_with_senses'], sum(bool(r['senses']) for r in records))
        self.assertEqual(counts['senses'], sum(len(r['senses']) for r in records))
        self.assertTrue(all(len(r['char']) == 1 and set(r) == {'char', 'senses'} for r in records))
        for section in data.values():
            for group in section.values():
                self.assertTrue(group)
                self.assertEqual(len(group), len({r['char'] for r in group}))


if __name__ == '__main__':
    unittest.main()
