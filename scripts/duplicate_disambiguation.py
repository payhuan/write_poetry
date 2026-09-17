"""Bound AI review for repeated-character usage in a checked poem context."""

from copy import deepcopy

from assessment_binding import BINDING_FIELDS, digest

VERSION = 'duplicate-review-1'
ALLOWED_RELATIONS = {'reduplication', 'parallel', 'descending'}
RELATIONS = ALLOWED_RELATIONS | {'error', 'unknown'}


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
            '逐个判断重复字在当前全句及全诗语境中的用法。只有叠字，或各重复位置所属意群构成'
            '明确并列或递减关系时才允许；普通复现、递进、无法说明意群关系或牵强解释均判 error。'
            '证据不足返回 unknown。不得根据格律是否通过倒推结论。'),
    }
    request['request_sha256'] = digest(request)
    return request


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
    _require(isinstance(payload['decisions'], list), 'decisions must be an array')
    expected_chars = {item['char'] for item in request['occurrences']}
    _require(len(expected_chars) == len(request['occurrences']),
             'Duplicate request contains repeated character groups')
    _require(len(payload['decisions']) == len(expected_chars),
             'Every repeated character requires one decision')
    decisions = []
    for decision in payload['decisions']:
        _require(isinstance(decision, dict) and set(decision) ==
                 {'char', 'relation', 'reason'}, 'Invalid duplicate decision fields')
        _require(decision['char'] in expected_chars, 'Unknown repeated character decision')
        _require(decision['relation'] in RELATIONS, 'Invalid duplicate relation')
        _require(isinstance(decision['reason'], str) and decision['reason'].strip(),
                 'Duplicate decision requires a reason')
        decisions.append(deepcopy(decision))
    _require({item['char'] for item in decisions} == expected_chars,
             'Duplicate or missing repeated character decision')
    return {
        'review_version': VERSION,
        'constraint_binding': deepcopy(request['constraint_binding']),
        'request_sha256': request['request_sha256'],
        'occurrences_sha256': digest(request['occurrences']),
        'decisions': decisions,
        'complete': all(item['relation'] != 'unknown' for item in decisions),
    }


def validate_for_engine(review, binding, occurrences):
    """Validate a review artifact against the exact object and duplicate positions."""
    _require(isinstance(review, dict) and set(review) == {
        'review_version', 'constraint_binding', 'request_sha256', 'occurrences_sha256',
        'decisions', 'complete'}, 'Invalid duplicate review artifact')
    _require(review['review_version'] == VERSION, 'Unsupported duplicate review artifact')
    _require(review['constraint_binding'] == binding, 'Duplicate review belongs to another context')
    _require(review['occurrences_sha256'] == digest(occurrences),
             'Duplicate review occurrences do not match current text')
    _require(isinstance(review['request_sha256'], str) and len(review['request_sha256']) == 64,
             'Invalid duplicate review digest')
    expected_chars = {item['char'] for item in occurrences}
    decisions = review['decisions']
    _require(isinstance(decisions, list) and len(decisions) == len(expected_chars),
             'Invalid duplicate review decisions')
    for decision in decisions:
        _require(isinstance(decision, dict) and set(decision) == {'char', 'relation', 'reason'},
                 'Invalid duplicate decision fields')
        _require(decision['char'] in expected_chars and decision['relation'] in RELATIONS,
                 'Invalid duplicate decision')
        _require(isinstance(decision['reason'], str) and decision['reason'].strip(),
                 'Duplicate decision requires a reason')
    _require({item['char'] for item in decisions} == expected_chars,
             'Duplicate or missing repeated character decision')
    _require(type(review['complete']) is bool and
             review['complete'] == all(item['relation'] != 'unknown' for item in decisions),
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
