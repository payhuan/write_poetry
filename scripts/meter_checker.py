"""Versioned basic meter and sentence-wide rules for all four forms."""

from itertools import product

RULE_VERSION = 'pingshui-basic-1'
RULES = {'忌孤平': 'guping-wangli-1', '忌三平调': 'last-three-level-1',
         '二四六分明': 'bound-even-positions-1'}


def aggregate(statuses):
    statuses = list(statuses)
    return ('fail' if 'fail' in statuses else 'unknown'
            if any(s in ('unknown', 'not_run') for s in statuses) else 'pass')


def verdict(values):
    values = set(values)
    return 'pass' if values == {True} else 'fail' if values == {False} else 'unknown'


def check_line(line, pattern, resolved):
    issues, statuses, positions = [], [], []
    for cell, expected, (records, reason) in zip(line['cells'], pattern, resolved):
        tones = {r['tone'] for r in records}
        status = ('unknown' if reason else 'pass' if expected == '中'
                  else verdict(t == expected for t in tones))
        item = {**cell, 'expected': expected, 'status': status, 'memberships': records}
        positions.append(item)
        statuses.append(status)
        if status != 'pass':
            issues.append({**cell, 'expected': expected, 'status': status,
                           'code': reason or ('AMBIGUOUS_READING' if status == 'unknown' else 'TONE_MISMATCH')})
    if len(line['cells']) != len(pattern) or line['segments'] != 1:
        return {'status': aggregate(statuses + ['unknown']), 'issues': issues + [
            {'code': 'LINE_SHAPE_UNAVAILABLE', 'line': line['line'], 'status': 'unknown'}],
            'positions': positions, 'rules': []}
    choices = [{r['tone'] for r in records} or {'平', '仄'} for records, _ in resolved]
    # At most 2**7 assignments. Never select the favourable one.
    assignments = list(product(*choices))
    rule_results = []
    triples = verdict(not all(t == '平' for t in a[-3:]) for a in assignments)
    rule_results.append({'rule': '忌三平调', 'version': RULES['忌三平调'], 'status': triples})
    tail = pattern[-5:]
    applicable = tail[1] == '平' and tail[3:] == '仄平'
    outcomes = []
    if applicable:
        for a in assignments:
            suffix = ''.join(a[-5:])
            outcomes.append('fail' if suffix == '仄平仄仄平' else
                            'unknown' if suffix == '仄平平仄平' else 'pass')
        guping = outcomes[0] if len(set(outcomes)) == 1 else 'unknown'
    else:
        guping = 'not_applicable'
    rule_results.append({'rule': '忌孤平', 'version': RULES['忌孤平'], 'status': guping})
    for rule in rule_results:
        statuses.append(rule['status'])
        if rule['status'] in ('fail', 'unknown'):
            code = ('THREE_LEVEL_ENDING' if rule['rule'] == '忌三平调' else 'GUPING')
            if rule['status'] == 'unknown':
                code = 'UNRESOLVED_' + code
            issues.append({'code': code, 'line': line['line'], 'pos': len(pattern) - (2 if rule['rule'] == '忌三平调' else 4),
                           'status': rule['status'], 'rule': rule['rule']})
    return {'status': aggregate(statuses), 'issues': issues, 'positions': positions, 'rules': rule_results}
