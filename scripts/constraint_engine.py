"""Pure, revision-preserving candidate and complete-poem checks (Pingshui only)."""

import argparse
import json
import sys
from pathlib import Path
from poem_state import PoemState
from assessment_binding import make_binding
from text_normalizer import normalize_line
from rhyme_lexicon import RhymeLexicon, validate_readings
from meter_checker import RULES, RULE_VERSION, aggregate, check_line as meter_line
from rhyme_checker import check_line as rhyme_line


class ConstraintEngine:
    def __init__(self, lexicon=None):
        self.lexicon = lexicon if lexicon is not None else RhymeLexicon()

    def check_candidate(self, candidate, state, *, readings=None):
        if (not isinstance(candidate, dict) or set(candidate) != {'id', 'text', 'line_index'}
                or not isinstance(candidate['id'], str) or not candidate['id'].strip()
                or type(candidate['line_index']) is not int):
            raise ValueError('Candidate requires id, text and one-based line_index')
        return self._check(state, candidate, readings)

    def check_poem(self, state, *, readings=None):
        return self._check(state, None, readings)

    def _check(self, state, candidate, readings):
        if isinstance(state, PoemState):
            d = state.to_dict()
        elif isinstance(state, dict):
            d = PoemState.from_dict(state).to_dict()
        else:
            raise ValueError('state must be PoemState or a state object')
        width, count = d['form']['line_length'], d['form']['line_count']
        context = {i: text for i, text in enumerate(d['lines'], 1)}
        if candidate is not None:
            index = candidate['line_index']
            if not 1 <= index <= count:
                raise ValueError('Candidate line_index outside form')
            context[index] = candidate['text']
        normalized = [normalize_line(text, i) for i, text in sorted(context.items())]
        readings = validate_readings(readings, normalized)
        targets = normalized if candidate is None else [x for x in normalized if x['line'] == index]
        length_issues = []
        if candidate is None and len(d['lines']) != count:
            length_issues.append({'code': 'LINE_COUNT', 'expected': count, 'actual': len(d['lines'])})
        for line in targets:
            if len(line['cells']) != width:
                length_issues.append({'code': 'LINE_LENGTH', 'line': line['line'],
                                      'expected': width, 'actual': len(line['cells'])})
            if line['segments'] != 1:
                length_issues.append({'code': 'LINE_SEGMENTS', 'line': line['line'],
                                      'expected': 1, 'actual': line['segments']})
            length_issues.extend({**c, 'code': 'INVALID_CHARACTER'} for c in line['cells'] if not c['is_han'])
        checks = {'length': {'status': 'fail' if length_issues else 'pass', 'required': True,
                             'issues': length_issues, 'lines': targets}}
        template = d.get('meter_template', {}).get('data')
        if template is None:
            for key in ('pingze', 'rhyme'):
                checks[key] = {'status': 'unknown', 'required': True,
                               'issues': [{'code': 'TEMPLATE_UNBOUND', 'status': 'unknown'}]}
        else:
            grid = {(c['line'], c['pos']): c['tune'] for c in template['tunes']}
            meters, rhymes, reading_issues, selected_readings = [], [], [], []
            for line in targets:
                resolved = []
                for cell in line['cells']:
                    coordinate = f"{cell['line']}:{cell['pos']}"
                    options = self.lexicon.lookup(cell['char'], include_senses=True)
                    selector = readings.get(coordinate)
                    records, error = self.lexicon.resolve(cell['char'], selector, include_senses=True)
                    resolved.append((records, error))
                    if len(options) <= 1:
                        continue
                    if selector is None or error is not None or len(records) != 1:
                        reading_issues.append({
                            'code': 'READING_REVIEW_REQUIRED', 'status': 'unknown',
                            'coordinate': coordinate, 'line': cell['line'], 'pos': cell['pos'],
                            'offset': cell['offset'], 'char': cell['char'], 'line_text': line['text'],
                            'options': options,
                        })
                    else:
                        selected_readings.append({
                            'coordinate': coordinate, 'char': cell['char'],
                            **{k: records[0][k] for k in ('section', 'group', 'tone', 'senses')},
                        })
                pattern = ''.join(grid[line['line'], p] for p in range(1, width + 1))
                meters.append({'line': line['line'], **meter_line(line, pattern, resolved)})
                rhymes.append({'line': line['line'], **rhyme_line(
                    line, width, template['rhyme']['positions'], d['rhyme'], self.lexicon, resolved)})
            for name, items in (('pingze', meters), ('rhyme', rhymes)):
                statuses = [x['status'] for x in items]
                issues = [issue for item in items for issue in item['issues']]
                if candidate is None and len(d['lines']) != count:
                    statuses.append('unknown')
                    issues.append({'code': 'POEM_INCOMPLETE', 'status': 'unknown'})
                status = aggregate(statuses)
                if name == 'rhyme' and candidate is not None and statuses == ['not_applicable']:
                    status = 'not_applicable'
                checks[name] = {'status': status, 'required': True, 'issues': issues, 'lines': items}
            unsupported = [r for r in template['constraints'] if r not in RULES]
            if unsupported:
                checks['pingze']['issues'].extend({'code': 'UNSUPPORTED_RULE', 'rule': r, 'status': 'unknown'} for r in unsupported)
                checks['pingze']['status'] = aggregate([checks['pingze']['status'], 'unknown'])
            checks['pingze']['rule_version'] = RULE_VERSION
            checks['pingze']['rule_mapping'] = RULES.copy()
            checks['reading'] = {
                'status': 'unknown' if reading_issues else 'pass', 'required': True,
                'issues': reading_issues, 'selected': selected_readings,
                'context': {'theme': d['theme'], 'emotion': d['emotion'],
                            'lines': [text for _, text in sorted(context.items())]},
                'policy': 'ai_context_choice_then_rule_recheck',
            }
        occurrences = {}
        for line in normalized:
            for cell in line['cells']:
                if cell['is_han']:
                    occurrences.setdefault(cell['char'], []).append({k: cell[k] for k in ('line', 'pos', 'offset')})
        duplicates = [{'char': char, 'count': len(locations), 'positions': locations}
                      for char, locations in occurrences.items() if len(locations) > 1]
        checks['duplicate'] = {'status': 'fail' if duplicates else 'pass',
                               'required': '禁止重复字' in d['constraints'], 'occurrences': duplicates}
        hard = [v['status'] for v in checks.values() if v.get('required', True)]
        status = aggregate(hard)
        valid = False if status == 'fail' else None if status == 'unknown' else True
        errors = [name + ': ' + issue['code'] for name, check in checks.items() if check['required']
                  for issue in check.get('issues', []) if issue.get('status', check['status']) == 'fail']
        if duplicates and checks['duplicate']['required']:
            errors.append('duplicate: DUPLICATE_CHARACTER')
        return {**make_binding(d, candidate), 'revision': d['revision'], 'valid': valid, 'checks': checks, 'errors': errors,
                'warnings': ['重复字：' + '、'.join(x['char'] for x in duplicates)] if duplicates else []}


def check_candidate(candidate, state, *, readings=None):
    return ConstraintEngine().check_candidate(candidate, state, readings=readings)


def check_poem(state, *, readings=None):
    return ConstraintEngine().check_poem(state, readings=readings)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--readings', type=Path)
    args = parser.parse_args()
    def read(path):
        return json.loads(path.read_text(encoding='utf-8-sig')) if path else None
    try:
        state, readings = PoemState.load(args.state), read(args.readings)
        result = (check_candidate(read(args.candidate), state, readings=readings) if args.candidate
                  else check_poem(state, readings=readings))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(2, f'Constraint error: {exc}\n')


if __name__ == '__main__':
    main()
