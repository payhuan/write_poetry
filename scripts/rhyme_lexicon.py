"""Pingshui memberships, never fabricated pronunciation records."""

import hashlib
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / 'data' / 'rhyme'
TONES = {'上平声部': '平', '下平声部': '平', '上声部': '仄', '去声部': '仄', '入声部': '仄'}
NUMBER_PREFIX = re.compile(r'^[一二三四五六七八九十百]+')


class AmbiguousRhymeGroup(ValueError):
    def __init__(self, group, choices):
        self.group, self.choices = group, tuple(choices)
        super().__init__('Ambiguous rhyme group alias: ' + group + ' -> ' + ', '.join(self.choices))


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
        index, groups, senses = {}, set(), {}
        for section, entries in data.items():
            if section not in TONES or not isinstance(entries, dict):
                raise ValueError('Invalid rhyme section')
            for group, chars in entries.items():
                if not isinstance(group, str) or not group or not isinstance(chars, list):
                    raise ValueError('Invalid rhyme group')
                groups.add((section, group))
                for entry in chars:
                    if isinstance(entry, str):
                        char, annotations = entry, []  # Legacy injected/string-list tables.
                    elif (isinstance(entry, dict) and set(entry) == {'char', 'senses'}
                          and isinstance(entry['senses'], list)
                          and all(isinstance(s, str) and s.strip() for s in entry['senses'])):
                        char, annotations = entry['char'], entry['senses']
                    else:
                        raise ValueError('Rhyme entry must be a character or {char, senses} record')
                    if not isinstance(char, str) or len(char) != 1:
                        raise ValueError('Rhyme entries must be single code points')
                    index.setdefault(char, set()).add((section, group))
                    target = senses.setdefault((char, section, group), [])
                    for annotation in annotations:
                        if annotation not in target:
                            target.append(annotation)
        self._index = {c: tuple(sorted(v)) for c, v in index.items()}
        self._senses = {key: tuple(value) for key, value in senses.items()}
        self.groups = frozenset(groups)

    def lookup(self, char, *, include_senses=False):
        """Return memberships; opt in to source notes without changing legacy records.

        Notes belong only to this character/section/group. They are evidence for
        readers, never an automatic reading selector for the rule engine.
        """
        return [{'section': s, 'group': g, 'tone': TONES[s],
                 **({'senses': list(self._senses[char, s, g])} if include_senses else {})}
                for s, g in self._index.get(char, ())]

    def resolve(self, char, reading=None, *, include_senses=False):
        records = self.lookup(char, include_senses=include_senses)
        if not records:
            return [], 'CHAR_NOT_FOUND'
        if reading is None:
            return records, None
        if isinstance(reading, str):
            # The source does not link pinyin to memberships.
            return [], 'READING_MAPPING_UNAVAILABLE'
        records = [r for r in records if all(r[k] == v for k, v in reading.items())]
        return (records, None) if records else ([], 'READING_NOT_ATTESTED')

    def match_groups(self, group):
        """Return exact or suffix-alias matches without silently merging aliases."""
        if not isinstance(group, str) or not group.strip():
            return set()
        value = group.strip()
        section, separator, name = value.partition('/')
        if not separator:
            section, name = None, value
        candidates = {(s, g) for s, g in self.groups if section in (None, s)}
        exact = {(s, g) for s, g in candidates if g == name}
        if exact:
            return exact
        return {(s, g) for s, g in candidates if NUMBER_PREFIX.sub('', g) == name}

    def normalize_group(self, group, *, strict=False):
        """Return one canonical group name; ambiguous aliases always need review."""
        matches = self.match_groups(group)
        if len(matches) > 1:
            choices = sorted(s + '/' + g for s, g in matches)
            raise AmbiguousRhymeGroup(group, choices)
        if matches:
            return next(iter(matches))[1]
        if strict:
            raise ValueError('Rhyme group unavailable: ' + str(group))
        return None

    def group_records(self, group):
        matches = self.match_groups(group)
        return matches if len(matches) == 1 else set()


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
