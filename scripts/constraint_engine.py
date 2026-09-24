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
from duplicate_disambiguation import ALLOWED_DEVICES, validate_for_engine


class ConstraintEngine:
    def __init__(self, lexicon=None, *, duplicate_policy='review'):
        if duplicate_policy not in ('review', 'report'):
            raise ValueError("duplicate_policy must be 'review' or 'report'")
        self.lexicon = lexicon if lexicon is not None else RhymeLexicon()
        self.duplicate_policy = duplicate_policy

    def check_candidate(self, candidate, state, *, readings=None, duplicate_review=None):
        if (not isinstance(candidate, dict) or set(candidate) != {'id', 'text', 'line_index'}
                or not isinstance(candidate['id'], str) or not candidate['id'].strip()
                or type(candidate['line_index']) is not int):
            raise ValueError('Candidate requires id, text and one-based line_index')
        return self._check(state, candidate, readings, duplicate_review)

    def check_poem(self, state, *, readings=None, duplicate_review=None):
        return self._check(state, None, readings, duplicate_review)

    def _check(self, state, candidate, readings, duplicate_review):
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
            meters, rhymes, reading_issues, selected_readings, equivalent_readings = [], [], [], [], []
            for line in targets:
                resolved = []
                for cell in line['cells']:
                    coordinate = f"{cell['line']}:{cell['pos']}"
                    options = self.lexicon.lookup(cell['char'], include_senses=True)
                    selector = readings.get(coordinate)
                    records, error = self.lexicon.resolve(cell['char'], selector, include_senses=True)
                    is_rhyme_position = (cell['line'] in template['rhyme']['positions'] and
                                         cell['pos'] == width)
                    equivalent = (len(options) > 1 and selector is None and not is_rhyme_position and
                                  len({item['tone'] for item in options}) == 1)
                    if equivalent:
                        # The exact membership remains linguistically ambiguous, but every
                        # offered reading has the same deterministic effect at this position.
                        records, error = options, None
                        equivalent_readings.append({
                            'coordinate': coordinate, 'char': cell['char'],
                            'reason': 'same_tone_non_rhyme', 'options': options,
                        })
                    resolved.append((records, error))
                    if len(options) <= 1:
                        continue
                    if equivalent:
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
                'equivalent': equivalent_readings,
                'context': {'theme': d['theme'], 'emotion': d['emotion'],
                            'lines': [text for _, text in sorted(context.items())]},
                'policy': 'ai_context_choice_then_rule_recheck',
            }
        occurrences = {}
        for line in normalized:
            for cell in line['cells']:
                if cell['is_han']:
                    occurrences.setdefault(cell['char'], []).append({k: cell[k] for k in ('line', 'pos', 'offset')})
        rhyme_coordinates = set()
        if template is not None:
            rhyme_coordinates = {(line_no, width) for line_no in template['rhyme']['positions']}
        duplicates = []
        for char, locations in occurrences.items():
            if len(locations) < 2:
                continue
            rhyme_positions = [p for p in locations if (p['line'], p['pos']) in rhyme_coordinates]
            matched_pairs = []
            for left, right in ((1, 2), (3, 4), (5, 6), (7, 8)):
                left_positions = {p['pos'] for p in locations if p['line'] == left}
                right_positions = {p['pos'] for p in locations if p['line'] == right}
                for pos in sorted(left_positions & right_positions):
                    matched_pairs.append({'positions': [[left, pos], [right, pos]]})
            duplicates.append({'char': char, 'count': len(locations), 'positions': locations,
                               'rhyme_positions': rhyme_positions,
                               'same_position_couplet_pairs': matched_pairs})
        duplicate_context = {'theme': d['theme'], 'emotion': d['emotion'],
                             'line_indices': sorted(context),
                             'lines': [text for _, text in sorted(context.items())]}
        if not duplicates:
            if duplicate_review is not None:
                raise ValueError('Duplicate review supplied but current text has no repeats')
            checks['duplicate'] = {'status': 'pass', 'required': self.duplicate_policy == 'review',
                                   'occurrences': [], 'issues': [], 'reviewed': [],
                                   'context': duplicate_context,
                                   'policy': 'ai_semantic_relation_then_rule_recheck'}
        elif self.duplicate_policy == 'report':
            if duplicate_review is not None:
                raise ValueError('Duplicate review is unavailable in report policy')
            checks['duplicate'] = {'status': 'fail', 'required': False, 'occurrences': duplicates,
                                   'issues': [], 'reviewed': [], 'context': duplicate_context,
                                   'policy': 'report_only_fixture_mode'}
        elif any(len(item['rhyme_positions']) > 1 for item in duplicates):
            repeated_rhyme = [item for item in duplicates if len(item['rhyme_positions']) > 1]
            checks['duplicate'] = {
                'status': 'fail', 'required': True, 'occurrences': duplicates,
                'issues': [{'code': 'DUPLICATE_RHYME_WORD', 'status': 'fail', **item}
                           for item in repeated_rhyme],
                'reviewed': [], 'context': duplicate_context,
                'policy': 'hard_reject_repeated_rhyme_character',
            }
        elif duplicate_review is None:
            checks['duplicate'] = {
                'status': 'unknown', 'required': True, 'occurrences': duplicates,
                'issues': [{'code': 'DUPLICATE_REVIEW_REQUIRED', 'status': 'unknown', **item}
                           for item in duplicates],
                'reviewed': [], 'context': duplicate_context,
                'policy': 'ai_rhetorical_evidence_then_rule_recheck',
            }
        else:
            binding = {**make_binding(d, candidate), 'revision': d['revision']}
            decisions = validate_for_engine(duplicate_review, binding, duplicates)
            by_char = {item['char']: item for item in decisions}
            issues = []
            for item in duplicates:
                decision = by_char[item['char']]
                if decision['device'] == 'error':
                    issues.append({'code': 'DUPLICATE_CHARACTER', 'status': 'fail',
                                   **item, 'reason': decision['reason']})
                elif decision['device'] == 'unknown':
                    issues.append({'code': 'DUPLICATE_REVIEW_UNCERTAIN', 'status': 'unknown',
                                   **item, 'reason': decision['reason']})
            duplicate_status = ('fail' if any(x['status'] == 'fail' for x in issues) else
                                'unknown' if issues else 'pass')
            checks['duplicate'] = {
                'status': duplicate_status, 'required': True, 'occurrences': duplicates,
                'issues': issues, 'reviewed': decisions, 'context': duplicate_context,
                'allowed_devices': sorted(ALLOWED_DEVICES),
                'policy': 'ai_rhetorical_evidence_then_rule_recheck',
            }
        hard = [v['status'] for v in checks.values() if v.get('required', True)]
        status = aggregate(hard)
        valid = False if status == 'fail' else None if status == 'unknown' else True
        errors = [name + ': ' + issue['code'] for name, check in checks.items() if check['required']
                  for issue in check.get('issues', []) if issue.get('status', check['status']) == 'fail']
        return {**make_binding(d, candidate), 'revision': d['revision'], 'valid': valid, 'checks': checks, 'errors': errors,
                'warnings': ['重复字：' + '、'.join(x['char'] for x in duplicates)] if duplicates else []}


def check_candidate(candidate, state, *, readings=None, duplicate_review=None):
    return ConstraintEngine().check_candidate(candidate, state, readings=readings,
                                              duplicate_review=duplicate_review)


def check_poem(state, *, readings=None, duplicate_review=None):
    return ConstraintEngine().check_poem(state, readings=readings, duplicate_review=duplicate_review)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--readings', type=Path)
    parser.add_argument('--duplicate-review', type=Path)
    args = parser.parse_args()
    def read(path):
        return json.loads(path.read_text(encoding='utf-8-sig')) if path else None
    try:
        state, readings = PoemState.load(args.state), read(args.readings)
        duplicate_review = read(args.duplicate_review)
        result = (check_candidate(read(args.candidate), state, readings=readings,
                                  duplicate_review=duplicate_review) if args.candidate
                  else check_poem(state, readings=readings, duplicate_review=duplicate_review))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(2, f'Constraint error: {exc}\n')


if __name__ == '__main__':
    main()
