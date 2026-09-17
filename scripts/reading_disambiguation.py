"""Bound AI reading review for attested Pingshui memberships."""

from copy import deepcopy

from assessment_binding import BINDING_FIELDS, digest

VERSION = 'reading-review-1'


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def make_request(result):
    """Build a model request from one unresolved ConstraintResult."""
    _require(isinstance(result, dict), 'Constraint result must be an object')
    _require(BINDING_FIELDS <= set(result), 'Bound constraint result required')
    check = result.get('checks', {}).get('reading')
    _require(isinstance(check, dict) and check.get('status') == 'unknown',
             'No unresolved reading review in result')
    occurrences = []
    for issue in check.get('issues', []):
        _require(issue.get('code') == 'READING_REVIEW_REQUIRED',
                 'Unsupported reading issue')
        occurrences.append({k: deepcopy(issue[k]) for k in
                            ('coordinate', 'line', 'pos', 'offset', 'char', 'line_text', 'options')})
    _require(bool(occurrences), 'Reading review has no occurrences')
    request = {
        'review_version': VERSION,
        'constraint_binding': {k: deepcopy(result[k]) for k in
                               ('binding_version', 'scope', 'subject', 'context_sha256', 'revision')},
        'context': deepcopy(check.get('context')),
        'occurrences': occurrences,
        'instruction': ('结合整句语义，从每个字已收录的平水韵选项中选择此处义项。'
                        'senses 为空表示韵表未提供释义，不能据此虚构读音；无法可靠判断时返回 unknown。'),
    }
    request['request_sha256'] = digest(request)
    return request


def apply_review(request, payload):
    """Validate model choices and return selectors accepted by ConstraintEngine."""
    _require(isinstance(request, dict) and request.get('review_version') == VERSION,
             'Unsupported reading request')
    expected = digest({k: v for k, v in request.items() if k != 'request_sha256'})
    _require(request.get('request_sha256') == expected, 'Reading request digest mismatch')
    _require(isinstance(payload, dict) and set(payload) ==
             {'review_version', 'request_sha256', 'decisions'}, 'Invalid reading review fields')
    _require(payload['review_version'] == VERSION and
             payload['request_sha256'] == request['request_sha256'],
             'Reading review belongs to another request')
    _require(isinstance(payload['decisions'], list), 'decisions must be an array')
    expected_by_coordinate = {x['coordinate']: x for x in request['occurrences']}
    _require(len(payload['decisions']) == len(expected_by_coordinate),
             'Every ambiguous occurrence requires one decision')
    readings, reviews = {}, []
    for decision in payload['decisions']:
        _require(isinstance(decision, dict) and set(decision) ==
                 {'coordinate', 'char', 'status', 'section', 'group', 'reason'},
                 'Invalid reading decision fields')
        coordinate = decision['coordinate']
        occurrence = expected_by_coordinate.get(coordinate)
        _require(occurrence is not None and decision['char'] == occurrence['char'],
                 'Reading decision coordinate or character mismatch')
        _require(isinstance(decision['reason'], str) and decision['reason'].strip(),
                 'Reading decision requires a reason')
        _require(decision['status'] in ('selected', 'unknown'), 'Invalid reading decision status')
        if decision['status'] == 'unknown':
            _require(decision['section'] is None and decision['group'] is None,
                     'Unknown decision cannot select a membership')
        else:
            _require(isinstance(decision['section'], str) and
                     isinstance(decision['group'], str), 'Selected decision requires section and group')
            matches = [x for x in occurrence['options']
                       if x['section'] == decision['section'] and x['group'] == decision['group']]
            _require(len(matches) == 1, 'Selected reading is not an offered membership')
            readings[coordinate] = {'section': decision['section'], 'group': decision['group']}
        reviews.append(deepcopy(decision))
    _require({x['coordinate'] for x in reviews} == set(expected_by_coordinate),
             'Duplicate or missing reading decision')
    return {'review_version': VERSION, 'request_sha256': request['request_sha256'],
            'readings': readings, 'decisions': reviews,
            'complete': len(readings) == len(expected_by_coordinate)}


def recheck_candidate(engine, candidate, state, request, payload):
    review = apply_review(request, payload)
    result = engine.check_candidate(candidate, state, readings=review['readings'])
    return {'review': review, 'result': result}


def recheck_poem(engine, state, request, payload):
    review = apply_review(request, payload)
    result = engine.check_poem(state, readings=review['readings'])
    return {'review': review, 'result': result}
