"""Rhyme decisions use exactly the same resolved memberships as meter."""

from meter_checker import verdict


def check_line(line, width, rhyme_positions, rhyme, lexicon, resolved):
    index = line['line']
    if index not in rhyme_positions:
        return {'status': 'not_applicable', 'issues': []}
    code, status = None, 'unknown'
    cell = line['cells'][-1] if line['cells'] else {'line': index, 'pos': width}
    if len(line['cells']) != width or line['segments'] != 1:
        code = 'RHYME_POSITION_UNAVAILABLE'
    elif rhyme['system'] != '平水韵':
        code = 'UNSUPPORTED_RHYME_SYSTEM'
    elif rhyme['group'] is None:
        code = 'RHYME_GROUP_UNSELECTED'
    elif len(lexicon.match_groups(rhyme['group'])) != 1:
        matches = lexicon.match_groups(rhyme['group'])
        code = 'RHYME_GROUP_AMBIGUOUS' if matches else 'RHYME_GROUP_UNAVAILABLE'
    else:
        records, reason = resolved[-1]
        if reason:
            code = reason
        else:
            group = lexicon.group_records(rhyme['group'])
            status = verdict((r['section'], r['group']) in group for r in records)
            code = 'AMBIGUOUS_READING' if status == 'unknown' else 'RHYME_MISMATCH'
    return {'status': status, 'issues': [] if status == 'pass' else [
        {**cell, 'code': code, 'status': status, 'expected': rhyme['group'],
         **({'choices': sorted(s + '/' + g for s, g in matches)}
            if code == 'RHYME_GROUP_AMBIGUOUS' else {})}]}
