"""Generate the bundled 52shici Pingshui table, including lazy-loaded rare words.

Only the Python standard library is required. Paths are relative to the project,
independent of cwd. Successful HTTP responses are cached for offline replay.
"""

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parent.parent
PAGE_URL = 'https://www.52shici.com/zd/yun.php'
SCRIPT_URL = 'https://www.52shici.com/zd/yun_do.js?r=2026'
UNCOMMON_URL = 'https://www.52shici.com/zd/ajax_load_yun_uncommon.php'
SECTIONS = ('上平声部', '下平声部', '上声部', '去声部', '入声部')
GROUP_NAMES = (
    '东冬江支微鱼虞齐佳灰真文元寒删',
    '先萧肴豪歌麻阳庚青蒸尤侵覃盐咸',
    '董肿讲纸尾语麌荠蟹贿轸吻阮旱潸铣筱巧皓哿马养梗迥有寝感俭豏',
    '送宋绛寘未御遇霁泰卦队震问愿翰谏霰啸效号个祃漾敬径宥沁勘艳陷',
    '屋沃觉质物月曷黠屑药陌锡职缉合叶洽',
)
VOID = set('area base br col embed hr img input link meta param source track wbr'.split())


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def descendants(self, tag):
        for child in self.children:
            if isinstance(child, Node):
                if child.tag == tag:
                    yield child
                yield from child.descendants(tag)

    def text(self, direct=False):
        return ''.join(c if isinstance(c, str) else '' if direct else c.text()
                       for c in self.children)


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, text):
        self.stack[-1].children.append(text)


def number(n):
    digits = '零一二三四五六七八九'
    return digits[n] if n < 10 else (digits[n // 10] if n >= 20 else '') + '十' + (digits[n % 10] if n % 10 else '')


def catalog():
    """Validate source labels, retaining the previous public group identifiers."""
    result = []
    for section, names in zip(SECTIONS, GROUP_NAMES):
        tone = '平声' if section in SECTIONS[:2] else section[:2]
        for n, name in enumerate(names, 1):
            label = number(n) + name
            group = '二十八琰' if label == '二十八俭' else label
            if section == '入声部':
                group = '入声' + group
            result.append({'id': 'psy' + str(len(result) + 1), 'label': label + tone,
                           'section': section, 'group': group})
    return result


def extract_entries(node):
    records, controls = [], 0
    for anchor in node.descendants('a'):
        if 'btnLoadUncommon' in anchor.attrs.get('class', '').split():
            if anchor.attrs.get('type') != 'psy':
                raise ValueError('Unexpected uncommon-word control')
            controls += 1
            continue
        char = ''.join(anchor.text(direct=True).split())
        if len(char) != 1:
            raise ValueError('Expected a single direct-text code point: ' + repr(char))
        # Keep each em separately; trim/collapse layout whitespace, not content.
        senses = [' '.join(em.text().split()) for em in anchor.descendants('em')]
        if any(not sense for sense in senses):
            raise ValueError('Empty em annotation')
        records.append({'char': char, 'senses': senses})
    return records, controls


def parse_page(html):
    root = Document(html).root
    lists = [u for u in root.descendants('ul') if u.attrs.get('id') == 'psy-word-list'
             and 'word-list' in u.attrs.get('class', '').split()]
    if len(lists) != 1:
        raise ValueError('Expected exactly one ul#psy-word-list.word-list')
    groups = list(lists[0].descendants('li'))
    expected = catalog()
    if len(groups) != len(expected):
        raise ValueError('Expected all 106 Pingshui groups')
    output = []
    for node, info in zip(groups, expected):
        if (node.attrs.get('id'), node.attrs.get('y')) != (info['id'], info['label']):
            raise ValueError('Unexpected group order/label: ' + repr(node.attrs))
        records, controls = extract_entries(node)
        if not records or controls != 1:
            raise ValueError('Missing words or uncommon-word control: ' + info['id'])
        output.append({**info, 'records': records})
    return output


def parse_uncommon(raw):
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get('status') != 'ok' or not isinstance(payload.get('str'), str):
        raise ValueError('Uncommon API did not return status=ok and HTML str')
    records, controls = extract_entries(Document(payload['str']).root)
    if controls or (payload['str'].strip() and not records):
        raise ValueError('Unexpected uncommon HTML response')
    return records


def merge_entries(entries):
    """One record per character in this group; retain all distinct source notes."""
    merged = {}
    for entry in entries:
        target = merged.setdefault(entry['char'], {'char': entry['char'], 'senses': []})
        for sense in entry['senses']:
            if sense not in target['senses']:
                target['senses'].append(sense)
    return list(merged.values())


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def project_path(value):
    path = (PROJECT / value).resolve()
    if not path.is_relative_to(PROJECT):
        raise ValueError('Path must stay within project: ' + str(value))
    return path


class Fetcher:
    def __init__(self, cache, *, offline=False, refresh=False, delay=0.25):
        self.cache, self.offline, self.refresh, self.delay = cache, offline, refresh, delay
        self.requests = []

    def fetch(self, name, url, parameters=None):
        body_path, meta_path = self.cache / name, self.cache / (name + '.meta.json')
        method = 'POST' if parameters else 'GET'
        if not self.refresh and body_path.exists() and meta_path.exists():
            raw = body_path.read_bytes()
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            if (meta['sha256'] != digest(raw) or meta['url'] != url
                    or meta['parameters'] != parameters or meta['method'] != method):
                raise ValueError('Cached response mismatch: ' + name)
        else:
            if self.offline:
                raise ValueError('Offline response missing: ' + name)
            errors = []
            for attempt in range(3):
                try:
                    time.sleep(self.delay if attempt == 0 else 2 ** attempt)
                    req = Request(url, data=urlencode(parameters).encode('utf-8') if parameters else None,
                                  headers={'User-Agent': 'write-poetry-rhyme-import/1.0',
                                           'Referer': PAGE_URL, 'X-Requested-With': 'XMLHttpRequest'})
                    with urlopen(req, timeout=30) as response:
                        raw = response.read()
                        meta = {'url': url, 'method': method, 'parameters': parameters,
                                'retrieved_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                'http_status': response.status, 'sha256': digest(raw),
                                'byte_count': len(raw), 'attempts': attempt + 1,
                                'retry_errors': errors}
                    break
                except (URLError, TimeoutError, ConnectionError) as exc:
                    errors.append(type(exc).__name__)
                    if attempt == 2:
                        raise
            self.cache.mkdir(parents=True, exist_ok=True)
            body_path.write_bytes(raw)
            meta_path.write_bytes(json_bytes(meta))
        self.requests.append({'response': name, **meta})
        return raw


def generate(fetcher):
    groups = parse_page(fetcher.fetch('yun.html', PAGE_URL).decode('utf-8-sig'))
    # Keep the public switch/API script's digest as evidence; never execute it.
    fetcher.fetch('yun_do.js', SCRIPT_URL)
    data, counts = {s: {} for s in SECTIONS}, []
    for group in groups:
        rare = parse_uncommon(fetcher.fetch(group['id'] + '.json', UNCOMMON_URL,
                                           {'type': 'psy', 'y': group['label']}))
        entries = merge_entries(group['records'] + rare)
        data[group['section']][group['group']] = entries
        counts.append({k: group[k] for k in ('id', 'label', 'section', 'group')})
        counts[-1].update(common_count=len(group['records']), uncommon_count=len(rare),
                          record_count=len(entries),
                          merged_records=len(group['records']) + len(rare) - len(entries))
        print(f"{group['id']}/106 {group['label']}: {len(group['records'])} + {len(rare)}", file=sys.stderr, flush=True)
    all_records = [r for section in data.values() for entries in section.values() for r in entries]
    raw = json_bytes(data)
    source = {
        'schema_version': 2, 'source_name': '诗词吾爱网 · 平水韵', 'source_url': PAGE_URL,
        'file': 'Pingshui_Rhyme.json', 'sha256': digest(raw),
        'generator': 'scripts/scrape_pingshui.py',
        'retrieved_on': fetcher.requests[0]['retrieved_at'][:10],
        'capture_started_at': min(r['retrieved_at'] for r in fetcher.requests),
        'capture_finished_at': max(r['retrieved_at'] for r in fetcher.requests),
        'extraction': 'All 106 li under ul#psy-word-list.word-list, including hidden groups; '
                      'append each successful uncommon API str as the page does. Each a direct text '
                      'minus whitespace is char; descendant em texts are senses. Skip only btnLoadUncommon. '
                      'Merge repeated characters within each group, union distinct em notes in source order. '
                      'Preserve memberships across groups and original code points.',
        'switching': 'yun_do.js binds #psy-list a by index to #psy-word-list li.eq(i); '
                     'all common groups are in the initial HTML. Rare words use POST '
                     'ajax_load_yun_uncommon.php with type=psy and y=the li y attribute.',
        'group_names': 'Keep legacy section/group identifiers: source 二十八俭上声 -> 上声部/二十八琰; '
                       'retain 入声 prefix on entering-tone group names. Labels and psy IDs are recorded below.',
        'counts': {'group_count': len(counts), 'section_group_counts': {s: len(v) for s, v in data.items()},
                   'common_records': sum(g['common_count'] for g in counts),
                   'uncommon_records': sum(g['uncommon_count'] for g in counts),
                   'merged_records': sum(g['merged_records'] for g in counts),
                   'records': len(all_records), 'unique_characters': len({r['char'] for r in all_records}),
                   'records_with_senses': sum(bool(r['senses']) for r in all_records),
                   'senses': sum(len(r['senses']) for r in all_records)},
        'limitations': ['Coverage means all 106 website groups and their common/uncommon responses, '
                        'not every historical character or pronunciation.',
                        'Annotations mix senses, usage and pronunciation hints; preserved as text, '
                        'not parsed into a complete pinyin mapping; no automatic disambiguation.',
                        'No script conversion, inferred variants, or supplementation from the old table.',
                        'Source may change; HTTP hashes and cache allow a specific capture to be audited. '
                        'Responses can have different capture times after resume; this is not an atomic website snapshot. '
                        'The ignored runs/rhyme-scrape cache is local capture evidence, not a required runtime resource.'],
        'license_note': 'No explicit open-data redistribution license identified in the fetched page. '
                        'The retained LICENSE belongs to the previous GitHub source and does not license this website data.',
        'previous_source': {'repository': 'https://github.com/charlesix59/chinese_word_rhyme',
                            'commit': 'ff0e9c13fb037c43e0eaa5dc929c0fe4fa2ffb18',
                            'license_file': 'LICENSE', 'used_in_current_table': False},
        'groups': counts, 'requests': fetcher.requests,
    }
    return raw, json_bytes(source)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', default='runs/rhyme-scrape')
    parser.add_argument('--output-dir', default='data/rhyme')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--offline', action='store_true')
    mode.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    try:
        output = project_path(args.output_dir)
        raw, source = generate(Fetcher(project_path(args.cache), offline=args.offline, refresh=args.refresh))
        # Nothing touches the final files until every group/response has passed validation.
        output.mkdir(parents=True, exist_ok=True)
        (output / 'Pingshui_Rhyme.json').write_bytes(raw)
        (output / 'source.json').write_bytes(source)
        print(json.dumps(json.loads(source)['counts'], ensure_ascii=False))
    except (ValueError, OSError) as exc:
        parser.exit(2, f'Pingshui import failed: {exc}\n')


if __name__ == '__main__':
    main()
