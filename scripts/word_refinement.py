"""Strict model contract for minimal, semantic-lock-preserving word refinement."""

from copy import deepcopy

from assessment_binding import digest, make_binding
from poetry_round import require

VERSION = 'word-refinement-1'
STATUSES = ('NO_CHANGE', 'REFINED', 'REFLOW_REQUIRED')


def _text(value, limit=1000):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def structural_units(state):
    data = state.to_dict() if hasattr(state, 'to_dict') else state
    if data['form']['line_count'] == 4:
        return [{'unit_id': f'line-{i}', 'unit': f'第{i}句', 'line_indices': [i]}
                for i in range(1, 5)]
    names = ('首联', '颔联', '颈联', '尾联')
    return [{'unit_id': f'couplet-{i}', 'unit': names[i-1],
             'line_indices': [i * 2 - 1, i * 2]} for i in range(1, 5)]


def refinement_request(state, unit, semantic_lock, history):
    data = state.to_dict() if hasattr(state, 'to_dict') else deepcopy(state)
    result = {'refinement_version': VERSION, 'binding': make_binding(data),
              'revision': data['revision'], 'unit': deepcopy(unit),
              'original_lines': [{'line_index': i, 'text': data['lines'][i-1]}
                                 for i in unit['line_indices']],
              'semantic_lock': deepcopy(semantic_lock), 'history': deepcopy(history),
              'instruction': ('逐字审查本结构单位。没有明确净增益时返回 NO_CHANGE。REFINED 最多改变两个字符位置，'
                              '原字必须进入候选比较并说明增益与代价；需要改写整句或整联时返回 REFLOW_REQUIRED。')}
    result['request_sha256'] = digest(result)
    return result


def _positions(original, refined):
    result = []
    for line_no, (before, after) in enumerate(zip(original, refined)):
        for pos, (old, new) in enumerate(zip(before['text'], after['text']), 1):
            if old != new:
                result.append((before['line_index'], pos, old, new))
    return result


def validate_refinement(payload, request):
    require(isinstance(payload, dict) and set(payload) ==
            {'refinement_version', 'request_sha256', 'status', 'reason', 'result'},
            'INVALID_REFINEMENT', 'Unexpected refinement response fields')
    require(payload['refinement_version'] == VERSION and payload['request_sha256'] == request['request_sha256'],
            'STALE_REFINEMENT', 'Refinement response belongs to another unit or revision')
    require(payload['status'] in STATUSES and _text(payload['reason']),
            'INVALID_REFINEMENT', 'A supported status and reason are required')
    original = request['original_lines']
    if payload['status'] == 'NO_CHANGE':
        require(payload['result'] == {'lines': original}, 'NO_CHANGE_TEXT_MISMATCH',
                'NO_CHANGE must return the original unit verbatim')
        return deepcopy(payload)
    if payload['status'] == 'REFLOW_REQUIRED':
        result = payload['result']
        require(isinstance(result, dict) and set(result) ==
                {'problem_type', 'protected_content', 'suggested_scope'} and
                _text(result['problem_type']) and _text(result['suggested_scope']) and
                isinstance(result['protected_content'], list) and all(_text(x) for x in result['protected_content']),
                'INVALID_REFLOW', 'REFLOW_REQUIRED needs a bounded problem and protected content')
        require(result['suggested_scope'] in ('line', 'couplet') and
                (result['suggested_scope'] == 'couplet') == (len(original) == 2),
                'INVALID_REFLOW_SCOPE', 'Reflow scope must equal the current structural unit')
        return deepcopy(payload)
    result = payload['result']
    require(isinstance(result, dict) and set(result) ==
            {'lines', 'changes', 'net_gain', 'tradeoffs', 'semantic_lock_preserved'},
            'INVALID_REFINED', 'REFINED result fields are incomplete')
    lines = result['lines']
    require(isinstance(lines, list) and len(lines) == len(original) and
            all(isinstance(x, dict) and set(x) == {'line_index', 'text'} and _text(x['text'])
                for x in lines) and [x['line_index'] for x in lines] == [x['line_index'] for x in original] and
            all(len(x['text']) == len(y['text']) for x, y in zip(lines, original)),
            'INVALID_REFINED_LINES', 'Refinement must preserve unit positions and character counts')
    actual = _positions(original, lines)
    require(1 <= len(actual) <= 2, 'INVALID_EDIT_SCOPE', 'REFINED must change one or two character positions')
    changes = result['changes']
    require(isinstance(changes, list) and len(changes) == len(actual),
            'INVALID_EDIT_DECLARATION', 'Declare every changed character and no others')
    declared = []
    for change in changes:
        require(isinstance(change, dict) and set(change) ==
                {'line_index', 'position', 'original', 'selected', 'candidates', 'reason'} and
                type(change['line_index']) is int and type(change['position']) is int and
                isinstance(change['original'], str) and len(change['original']) == 1 and
                isinstance(change['selected'], str) and len(change['selected']) == 1 and
                isinstance(change['candidates'], list) and change['original'] in change['candidates'] and
                change['selected'] in change['candidates'] and len(set(change['candidates'])) == len(change['candidates']) and
                all(isinstance(x, str) and len(x) == 1 for x in change['candidates']) and _text(change['reason']),
                'INVALID_EDIT_DECLARATION', 'Each position needs original, selected, candidates and reason')
        declared.append((change['line_index'], change['position'], change['original'], change['selected']))
    require(declared == actual, 'EDIT_DECLARATION_MISMATCH', 'Only declared positions may change')
    require(isinstance(result['net_gain'], dict) and set(result['net_gain']) ==
            {'dimension', 'reason', 'evidence'} and _text(result['net_gain']['dimension']) and
            _text(result['net_gain']['reason']) and _text(result['net_gain']['evidence']),
            'NET_GAIN_REQUIRED', 'Explain one concrete net gain with evidence')
    require(isinstance(result['tradeoffs'], list) and all(_text(x) for x in result['tradeoffs']),
            'INVALID_TRADEOFFS', 'Tradeoffs must be an array of strings')
    require(result['semantic_lock_preserved'] is True, 'SEMANTIC_LOCK_CHANGED',
            'Word refinement cannot declare a semantic-lock change')
    return deepcopy(payload)
