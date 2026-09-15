"""Pingshui memberships, never fabricated pronunciation records."""

import hashlib
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / 'data' / 'rhyme'
TONES = {'上平声部': '平', '下平声部': '平', '上声部': '仄', '去声部': '仄', '入声部': '仄'}


class RhymeLexicon:
    def __init__(self, data=None):
        if data is None:
            source = json.loads((DATA_DIR / 'source.json').read_text(encoding='utf-8'))
            raw = (DATA_DIR / 'Pingshui_Rhyme.json').read_bytes()
            if hashlib.sha256(raw).hexdigest() != source['sha256']:
                raise ValueError('Rhyme data digest mismatch')
            data = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(data, dict) or not data:
            raise ValueError('Rhyme lexicon must be a nonempty object')
        index, groups = {}, set()
        for section, entries in data.items():
            if section not in TONES or not isinstance(entries, dict):
                raise ValueError('Invalid rhyme section')
            for group, chars in entries.items():
                if not isinstance(group, str) or not group or not isinstance(chars, list):
                    raise ValueError('Invalid rhyme group')
                groups.add((section, group))
                for char in chars:
                    if not isinstance(char, str) or len(char) != 1:
                        raise ValueError('Rhyme entries must be single code points')
                    index.setdefault(char, set()).add((section, group))
        self._index = {c: tuple(sorted(v)) for c, v in index.items()}
        self.groups = frozenset(groups)

    def lookup(self, char):
        return [{'section': s, 'group': g, 'tone': TONES[s]} for s, g in self._index.get(char, ())]

    def resolve(self, char, reading=None):
        records = self.lookup(char)
        if not records:
            return [], 'CHAR_NOT_FOUND'
        if reading is None:
            return records, None
        if isinstance(reading, str):
            # The source does not link pinyin to memberships.
            return [], 'READING_MAPPING_UNAVAILABLE'
        records = [r for r in records if all(r[k] == v for k, v in reading.items())]
        return (records, None) if records else ([], 'READING_NOT_ATTESTED')

    def group_records(self, group):
        return {(s, g) for s, g in self.groups if group in (g, s + '/' + g)}


def validate_readings(readings, lines):
    """JSON keys line:pos; values pinyin or an attested section/group selector."""
    if readings is None:
        return {}
    if not isinstance(readings, dict):
        raise ValueError('readings must be an object keyed by line:pos')
    coordinates = {f"{line['line']}:{c['pos']}" for line in lines for c in line['cells']}
    for key, value in readings.items():
        if not isinstance(key, str) or key not in coordinates:
            raise ValueError('Reading coordinate does not exist: ' + str(key))
        if isinstance(value, str) and value.strip():
            continue
        if (not isinstance(value, dict) or not value or set(value) - {'section', 'group'}
                or any(not isinstance(v, str) or not v.strip() for v in value.values())):
            raise ValueError('Reading must be nonempty pinyin or section/group selector')
    return readings
