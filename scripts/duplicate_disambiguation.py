"""Bound AI review for intentional rhetorical uses of repeated characters."""

from copy import deepcopy

from assessment_binding import BINDING_FIELDS, digest
from text_normalizer import normalize_line

VERSION = 'duplicate-review-2'
ALLOWED_DEVICES = {
    'reduplication', 'anadiplosis', 'echo', 'internal_parallelism',
    'proper_name', 'fixed_expression', 'other_rhetorical',
}
DEVICES = ALLOWED_DEVICES | {'error', 'unknown'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def make_request(result):
    """Build a model request from one unresolved duplicate check."""
    _require(isinstance(result, dict), 'Constraint result must be an object')
    _require(BINDING_FIELDS <= set(result), 'Bound constraint result required')
    check = result.get('checks', {}).get('duplicate')
    _require(isinstance(check, dict) and check.get('status') == 'unknown',
             'No unresolved duplicate review in result')
    occurrences = deepcopy(check.get('occurrences'))
    _require(isinstance(occurrences, list) and bool(occurrences),
             'Duplicate review has no occurrences')
    request = {
        'review_version': VERSION,
        'constraint_binding': {k: deepcopy(result[k]) for k in
                               ('binding_version', 'scope', 'subject', 'context_sha256', 'revision')},
        'context': deepcopy(check.get('context')),
        'occurrences': occurrences,
        'instruction': (
            '逐组核对该字的全部出现位置。每个位置必须引用所在句中的原文片段并说明该字在此处的具体词义或作用；'
            '再指出明确修辞手法及其表达作用。可接受：叠字、顶真/联珠、反复/回环、句内对称重复、专名、固定词语；'
            '其他用法须写明具体手法和作用。仅称意群并列或递进不构成依据。无意同义重复、律诗中间两联无意复现、'
            '上下联同一位置用同字均应判 error，除非有明确修辞目的。含糊或证据不足判 unknown。'
            '若重复字符占据两个或更多押韵位置，属于程序禁止的重韵，不得尝试复核放行。'
        ),
    }
    request['request_sha256'] = digest(request)
    return request


def _validate_decisions(request, decisions):
    _require(isinstance(decisions, list), 'decisions must be an array')
    occurrences = {item['char']: item for item in request['occurrences']}
    _require(len(occurrences) == len(request['occurrences']),
             'Duplicate request contains repeated character groups')
    _require(len(decisions) == len(occurrences),
             'Every repeated character requires one decision')
    lines = request['context'].get('lines', [])
    line_indices = request['context'].get('line_indices', list(range(1, len(lines) + 1)))
    lines_by_number = dict(zip(line_indices, lines))
    normalized_by_number = {number: normalize_line(line, number) for number, line in lines_by_number.items()}
    result = []
    for index, decision in enumerate(decisions, 1):
        _require(isinstance(decision, dict) and set(decision) ==
                 {'char', 'device', 'reason', 'position_evidence'},
                 f'Invalid duplicate decision fields at decision {index}; expected char, device, reason, position_evidence')
        char = decision['char']
        _require(char in occurrences, f'Unknown repeated character at decision {index}')
        _require(decision['device'] in DEVICES, f'Invalid duplicate device at decision {index}')
        _require(isinstance(decision['reason'], str) and decision['reason'].strip(),
                 f'Duplicate decision requires a specific rhetorical reason at decision {index}')
        evidence = decision['position_evidence']
        positions = occurrences[char]['positions']
        _require(isinstance(evidence, list) and len(evidence) == len(positions),
                 f'Position evidence incomplete for {char} at decision {index}')
        expected = {(p['line'], p['pos']) for p in positions}
        seen = set()
        for item in evidence:
            _require(isinstance(item, dict) and set(item) == {'line', 'pos', 'quote', 'meaning'},
                     f'Invalid position evidence fields for {char} at decision {index}')
            coordinate = (item['line'], item['pos'])
            _require(coordinate in expected and coordinate not in seen,
                     f'Position evidence does not match {char} occurrence at decision {index}')
            seen.add(coordinate)
            _require(type(item['line']) is int and item['line'] in lines_by_number and
                     type(item['pos']) is int and 1 <= item['pos'] <= len(normalized_by_number[item['line']]['cells']) and
                     normalized_by_number[item['line']]['cells'][item['pos'] - 1]['char'] == char and
                     isinstance(item['quote'], str) and char in item['quote'] and
                     item['quote'] in lines_by_number[item['line']],
                     f'Quote must contain {char} at its stated line for decision {index}')
            _require(isinstance(item['meaning'], str) and item['meaning'].strip(),
                     f'Every {char} occurrence needs a specific meaning at decision {index}')
        result.append(deepcopy(decision))
    _require({item['char'] for item in result} == set(occurrences),
             'Duplicate or missing repeated character decision')
    return result


def apply_review(request, payload):
    """Validate model decisions and return an engine-bound review artifact."""
    _require(isinstance(request, dict) and request.get('review_version') == VERSION,
             'Unsupported duplicate request')
    expected = digest({k: v for k, v in request.items() if k != 'request_sha256'})
    _require(request.get('request_sha256') == expected, 'Duplicate request digest mismatch')
    _require(isinstance(payload, dict) and set(payload) ==
             {'review_version', 'request_sha256', 'decisions'}, 'Invalid duplicate review fields')
    _require(payload['review_version'] == VERSION and
             payload['request_sha256'] == request['request_sha256'],
             'Duplicate review belongs to another request')
    decisions = _validate_decisions(request, payload['decisions'])
    return {
        'review_version': VERSION,
        'constraint_binding': deepcopy(request['constraint_binding']),
        'request_sha256': request['request_sha256'],
        'occurrences_sha256': digest(request['occurrences']),
        'decision_sha256': digest(decisions),
        'decisions': decisions,
        'complete': all(item['device'] != 'unknown' for item in decisions),
    }


def validate_for_engine(review, binding, occurrences):
    """Validate a review artifact against the exact object and duplicate positions."""
    _require(isinstance(review, dict) and set(review) == {
        'review_version', 'constraint_binding', 'request_sha256', 'occurrences_sha256', 'decision_sha256',
        'decisions', 'complete'}, 'Invalid duplicate review artifact')
    _require(review['review_version'] == VERSION, 'Unsupported duplicate review artifact')
    _require(review['constraint_binding'] == binding, 'Duplicate review belongs to another context')
    _require(review['occurrences_sha256'] == digest(occurrences),
             'Duplicate review occurrences do not match current text')
    _require(isinstance(review['request_sha256'], str) and len(review['request_sha256']) == 64,
             'Invalid duplicate review digest')
    # The engine receives the proof only after request validation; recheck its shape and binding here.
    expected_chars = {item['char'] for item in occurrences}
    decisions = review['decisions']
    _require(isinstance(decisions, list) and len(decisions) == len(expected_chars),
             'Invalid duplicate review decisions')
    for decision in decisions:
        _require(isinstance(decision, dict) and set(decision) == {
            'char', 'device', 'reason', 'position_evidence'}, 'Invalid duplicate decision fields')
        _require(decision['char'] in expected_chars and decision['device'] in DEVICES and
                 isinstance(decision['reason'], str) and decision['reason'].strip(),
                 'Invalid duplicate decision')
        evidence = decision['position_evidence']
        positions = next(x['positions'] for x in occurrences if x['char'] == decision['char'])
        _require(isinstance(evidence, list) and
                 {(x.get('line'), x.get('pos')) for x in evidence if isinstance(x, dict)} ==
                 {(x['line'], x['pos']) for x in positions},
                 'Duplicate position evidence does not match current text')
    _require({item['char'] for item in decisions} == expected_chars,
             'Duplicate or missing repeated character decision')
    _require(review['decision_sha256'] == digest(decisions),
             'Duplicate review decisions were changed after validation')
    _require(type(review['complete']) is bool and
             review['complete'] == all(item['device'] != 'unknown' for item in decisions),
             'Duplicate review completeness mismatch')
    return deepcopy(decisions)


def recheck_candidate(engine, candidate, state, request, payload, *, readings=None):
    review = apply_review(request, payload)
    result = engine.check_candidate(candidate, state, readings=readings, duplicate_review=review)
    return {'review': review, 'result': result}


def recheck_poem(engine, state, request, payload, *, readings=None):
    review = apply_review(request, payload)
    result = engine.check_poem(state, readings=readings, duplicate_review=review)
    return {'review': review, 'result': result}
