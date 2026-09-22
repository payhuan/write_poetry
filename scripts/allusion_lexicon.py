"""Offline allusion lookup and conservative name normalization.

The bundled snapshot is partial. Only one exact canonical/alias/navigation-name
match may be normalized automatically; fuzzy search results are suggestions.
"""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unicodedata


DATA_DIR = Path(__file__).resolve().parent.parent / 'data' / 'allusions'
OUTER_PUNCTUATION = ' \t\r\n“”‘’\'"「」『』《》〈〉【】〔〕[]（）()'
EXACT_KINDS = ('canonical', 'alias', 'navigation')


def normalize_query(value):
    if not isinstance(value, str):
        return ''
    value = unicodedata.normalize('NFKC', value).strip()
    previous = None
    while value and value != previous:
        previous = value
        value = value.strip(OUTER_PUNCTUATION)
    return ' '.join(value.split())


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_record(record):
    required = {'allusion_id', 'page_no', 'seq_in_page', 'raw_nav_name', 'name',
                'head_persons', 'aliases', 'persons', 'refs', 'sources', 'gloss'}
    if not isinstance(record, dict) or set(record) != required:
        raise ValueError('Invalid allusion record fields')
    if (type(record['allusion_id']) is not int or type(record['page_no']) is not int or
            type(record['seq_in_page']) is not int or not _text(record['name']) or
            not _text(record['raw_nav_name']) or not isinstance(record['gloss'], str)):
        raise ValueError('Invalid allusion record identity')
    for field in ('head_persons', 'persons'):
        if not isinstance(record[field], list) or not all(_text(x) for x in record[field]):
            raise ValueError('Invalid allusion people')
    if not isinstance(record['aliases'], list):
        raise ValueError('Invalid allusion aliases')
    for item in record['aliases']:
        if (not isinstance(item, dict) or set(item) != {'word', 'word_id'} or
                not _text(item['word']) or item['word_id'] is not None and type(item['word_id']) is not int):
            raise ValueError('Invalid allusion alias')
    if not isinstance(record['refs'], list):
        raise ValueError('Invalid allusion references')
    for item in record['refs']:
        if (not isinstance(item, dict) or set(item) != {'name', 'ref_id'} or
                not _text(item['name']) or
                item['ref_id'] is not None and type(item['ref_id']) is not int):
            raise ValueError('Invalid related allusion')
    if not isinstance(record['sources'], list) or not record['sources']:
        raise ValueError('Allusion source passage required')
    for item in record['sources']:
        if (not isinstance(item, dict) or set(item) != {'book', 'text'} or
                not _text(item['book']) or not _text(item['text'])):
            raise ValueError('Invalid allusion source')


def _record_digest(record):
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


class AllusionLexicon:
    """Validated in-memory index over the bundled JSONL snapshot."""

    def __init__(self, records=None, *, verify_source=True):
        if records is None:
            source = json.loads((DATA_DIR / 'source.json').read_text(encoding='utf-8'))
            raw = (DATA_DIR / source['file']).read_bytes()
            if verify_source and hashlib.sha256(raw).hexdigest() != source['sha256']:
                raise ValueError('Allusion data digest mismatch')
            records = [json.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.strip()]
            expected = source.get('counts', {}).get('allusions')
            if expected is not None and len(records) != expected:
                raise ValueError('Allusion data count mismatch')
            self.source = deepcopy(source)
        else:
            if not isinstance(records, list):
                raise ValueError('Allusion records must be an array')
            records = deepcopy(records)
            self.source = {'source_name': 'injected records', 'source_url': None,
                           'counts': {'allusions': len(records)}, 'limitations': []}
        ids = set()
        self._records = []
        self._exact = {}
        self._fields = []
        for record in records:
            _validate_record(record)
            if record['allusion_id'] in ids:
                raise ValueError('Duplicate allusion ID')
            ids.add(record['allusion_id'])
            index = len(self._records)
            self._records.append(record)
            fields = []
            self._add_exact(index, 'canonical', record['name'], fields)
            self._add_exact(index, 'navigation', record['raw_nav_name'], fields)
            for alias in record['aliases']:
                self._add_exact(index, 'alias', alias['word'], fields)
            for person in record['head_persons'] + record['persons']:
                fields.append(('person', normalize_query(person), person))
            for ref in record['refs']:
                fields.append(('related', normalize_query(ref['name']), ref['name']))
            if record['gloss'].strip():
                fields.append(('gloss', normalize_query(record['gloss']), record['gloss']))
            for source in record['sources']:
                fields.extend((('source_book', normalize_query(source['book']), source['book']),
                               ('source_text', normalize_query(source['text']), source['text'])))
            self._fields.append(fields)

    def _add_exact(self, index, kind, raw, fields):
        term = normalize_query(raw)
        if not term:
            return
        entry = (index, kind, raw)
        self._exact.setdefault(term, []).append(entry)
        fields.append((kind, term, raw))

    def _summary(self, index, matches):
        record = self._records[index]
        unique = []
        for kind, term in matches:
            item = {'kind': kind, 'term': term}
            if item not in unique:
                unique.append(item)
        return {'allusion_id': record['allusion_id'], 'canonical_name': record['name'],
                'raw_nav_name': record['raw_nav_name'], 'matches': unique,
                'head_persons': deepcopy(record['head_persons']),
                'persons': deepcopy(record['persons']), 'gloss': record['gloss'],
                'aliases': deepcopy(record['aliases']), 'refs': deepcopy(record['refs']),
                'sources': deepcopy(record['sources']), 'page_no': record['page_no'],
                'source_url': ('https://sou-yun.cn/AllusionsIndex.aspx?page=' + str(record['page_no'])),
                'record_sha256': _record_digest(record)}

    def search(self, query, *, limit=10):
        """Return deterministic exact and substring matches with full source evidence."""
        value = normalize_query(query)
        if not value:
            raise ValueError('query must be nonempty text')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('limit must be an integer from 1 to 100')
        by_record = {}
        for index, kind, raw in self._exact.get(value, []):
            by_record.setdefault(index, []).append((kind, raw))
        for index, fields in enumerate(self._fields):
            for kind, normalized, raw in fields:
                if value in normalized:
                    by_record.setdefault(index, []).append((kind, raw))
        rank = {kind: i for i, kind in enumerate((*EXACT_KINDS, 'person', 'related',
                                                  'gloss', 'source_book', 'source_text'))}
        def key(item):
            index, matches = item
            exact = any(normalize_query(term) == value and kind in EXACT_KINDS for kind, term in matches)
            best = min(rank.get(kind, len(rank)) for kind, _ in matches)
            return (not exact, best, self._records[index]['page_no'],
                    self._records[index]['seq_in_page'], self._records[index]['allusion_id'])
        results = [self._summary(index, matches) for index, matches in sorted(by_record.items(), key=key)[:limit]]
        return {'query': query, 'normalized_query': value, 'count': len(results),
                'partial_snapshot': True, 'results': results}

    def resolve(self, query):
        """Normalize only a unique exact canonical, alias, or navigation-name hit."""
        value = normalize_query(query)
        if not value:
            raise ValueError('query must be nonempty text')
        grouped = {}
        for index, kind, raw in self._exact.get(value, []):
            grouped.setdefault(index, []).append((kind, raw))
        if len(grouped) == 1:
            index, matches = next(iter(grouped.items()))
            return {'status': 'resolved', 'query': query, 'normalized_query': value,
                    'record': self._summary(index, matches)}
        if grouped:
            choices = [self._summary(index, matches) for index, matches in
                       sorted(grouped.items(), key=lambda item: self._records[item[0]]['allusion_id'])]
            return {'status': 'ambiguous', 'query': query, 'normalized_query': value,
                    'choices': choices}
        suggestions = self.search(query, limit=5)['results']
        return {'status': 'not_found', 'query': query, 'normalized_query': value,
                'suggestions': suggestions, 'partial_snapshot': True}

    def resolve_requirements(self, allusions):
        if not isinstance(allusions, list):
            raise ValueError('allusions must be an array')
        result = []
        for item in allusions:
            if not isinstance(item, dict) or not _text(item.get('id')) or not _text(item.get('name')):
                raise ValueError('Invalid allusion requirement')
            resolved = self.resolve(item['name'])
            result.append({'requirement_id': item['id'], **resolved})
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('search', 'resolve'):
        command = sub.add_parser(name)
        command.add_argument('--query', required=True)
        if name == 'search':
            command.add_argument('--limit', type=int, default=10)
    args = parser.parse_args()
    try:
        lexicon = AllusionLexicon()
        result = lexicon.search(args.query, limit=args.limit) if args.command == 'search' else lexicon.resolve(args.query)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, 'Allusion lexicon error: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
