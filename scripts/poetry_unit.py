"""Unit-aware generation for atomic regulated-verse middle couplets.

This module validates model-shaped JSON and delegates deterministic checks for
each line to the existing Day 3 contract.  It never decides literary truth.
"""

from copy import deepcopy
from fractions import Fraction
import math
from uuid import uuid4

from assessment_binding import digest, make_binding
from constraint_engine import ConstraintEngine
from poem_planning import unit_for_line
from poem_state import PoemState
from poetry_round import (RoundError, export_context, filter_candidates,
                          review_content, review_readings, content_eligible, require)
from round_content import export_content_plan
from character_alignment import validate_character_alignment

CONTRACT_VERSION = 'poetry-unit-1'
RUBRIC_VERSION = 'literary-3'
WEIGHTS = {'task_completion': .16, 'semantic_progression': .13,
           'image_relation': .10, 'context_consistency': .11,
           'implicitness': .08, 'naturalness': .11, 'freshness': .05,
           'future_space': .08, 'parallelism': .18}
LOCK_FIELDS = {'protected_meaning', 'facts', 'images', 'relations', 'emotion_direction',
               'structural_function', 'confirmed_allusions'}
PARALLEL_FIELDS = {'semantic_relation', 'image_relation', 'hangzhang',
                   'character_alignment', 'score', 'reason'}
ENVELOPE = {'contract_version', 'batch_id', 'context_sha256', 'generation_sha256'}


def is_atomic_couplet(state):
    data = state.to_dict()
    return data['form']['line_count'] == 8 and len(data['lines']) + 1 in (3, 5)


def _text(value, limit=1000):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _unit_context(state, poem_plan, content_plan, batch_id):
    data = state.to_dict()
    start = len(data['lines']) + 1
    require(data['form']['line_count'] == 8 and start in (3, 5),
            'NOT_ATOMIC_COUPLET', 'Only lines 3-4 and 5-6 use atomic couplet generation')
    unit = unit_for_line(poem_plan, start)
    require(unit is not None and unit.get('line_indices') == [start, start + 1],
            'PLAN_UNIT_MISMATCH', 'The poem plan does not bind this middle couplet')
    template = data['meter_template']['data']
    result = {
        'contract_version': CONTRACT_VERSION, 'batch_id': batch_id,
        'context_sha256': make_binding(data)['context_sha256'], 'revision': data['revision'],
        'line_indices': [start, start + 1], 'unit_name': unit['unit'],
        'request': data['request'], 'theme': data['theme'], 'emotion': data['emotion'],
        'lines': data['lines'], 'poem_type': data['poem_type'], 'form': data['form'],
        'rhyme': data['rhyme'], 'constraints': data['constraints'],
        'planned_unit': deepcopy(unit), 'poem_plan': deepcopy(poem_plan),
        'content_plan': deepcopy(content_plan), 'template': data['meter_template'],
        'line_requirements': [{
            'line_index': index,
            'tone_requirements': sorted([x for x in template['tunes'] if x['line'] == index],
                                        key=lambda x: x['pos']),
            'is_rhyme_line': index in template['rhyme']['positions'],
        } for index in (start, start + 1)],
        'candidate_count': {'min': 1, 'max': data['budget']['candidates_per_round'],
                            'target': min(5, data['budget']['candidates_per_round'])},
        'generation_instruction': ('一次生成完整一联。先选择表达路径，再写两句；两句共同完成规划增量，'
                                   '提交风险与 semantic_lock。对仗须逐字位核对上下句字符、句法功能与对应关系，'
                                   '同时评价整联语义和意象关系，避免合掌；'
                                   '不得为对仗或押韵改变事实、典故、情感方向或提前使用后文内容。'),
    }
    result['generation_sha256'] = digest(result)
    return result


def export_unit_context(state, poem_plan, content_plan):
    return _unit_context(state, poem_plan, content_plan, uuid4().hex)


def validate_unit_context(state, context):
    require(isinstance(context, dict), 'INVALID_UNIT_CONTEXT', 'Unit context must be an object')
    batch = context.get('batch_id')
    require(isinstance(batch, str) and len(batch) == 32 and all(c in '0123456789abcdef' for c in batch),
            'INVALID_BATCH', 'Invalid unit batch ID')
    expected = _unit_context(state, context.get('poem_plan'), context.get('content_plan'), batch)
    require(context == expected, 'STALE_CONTEXT', 'Unit context or bound state changed')


def _envelope(context):
    return {key: context[key] for key in ENVELOPE}


def _validate_lock(value, planned):
    require(isinstance(value, dict) and set(value) == LOCK_FIELDS,
            'INVALID_SEMANTIC_LOCK', 'semantic_lock must contain all protected dimensions')
    for key in ('facts', 'images', 'relations', 'confirmed_allusions'):
        require(isinstance(value[key], list) and all(_text(x) for x in value[key]),
                'INVALID_SEMANTIC_LOCK', key + ' must be an array of nonempty strings')
    for key in ('protected_meaning', 'emotion_direction', 'structural_function'):
        require(_text(value[key]), 'INVALID_SEMANTIC_LOCK', key + ' must be nonempty')
    require(value['structural_function'] == planned['primary_task'],
            'SEMANTIC_LOCK_PLAN_MISMATCH', 'structural_function must preserve the planned task')
    protected = planned.get('protected_meaning', [])
    require(all(item in value['protected_meaning'] for item in protected),
            'SEMANTIC_LOCK_INCOMPLETE', 'All planned protected meanings must remain explicit in the lock')


def receive_unit_candidates(context, payload):
    require(isinstance(payload, dict) and set(payload) == ENVELOPE | {'attempt', 'status', 'candidates'},
            'INVALID_UNIT_RESPONSE', 'Unexpected unit generation fields')
    require(all(payload[k] == context[k] for k in ENVELOPE), 'WRONG_BATCH', 'Unit response belongs elsewhere')
    require(type(payload['attempt']) is int and 1 <= payload['attempt'] <= 3,
            'RETRY_EXHAUSTED', 'At most three attempts are supported')
    require(payload['status'] in ('candidates', 'replan_required'),
            'INVALID_UNIT_STATUS', 'Use candidates or replan_required')
    if payload['status'] == 'replan_required':
        require(isinstance(payload['candidates'], dict) and set(payload['candidates']) ==
                {'reason', 'conflict', 'suggested_replan_scope'} and
                all(_text(payload['candidates'][k]) for k in payload['candidates']),
                'INVALID_REPLAN_REQUEST', 'Explain the conflict and bounded replan scope')
        return {'status': 'replan_required', 'replan': deepcopy(payload['candidates'])}
    items = payload['candidates']
    require(isinstance(items, list) and 1 <= len(items) <= context['candidate_count']['max'],
            'CANDIDATE_BUDGET', 'Candidate count is outside the configured budget')
    seen_ids, seen_texts, result = set(), set(), []
    for item in items:
        require(isinstance(item, dict) and set(item) ==
                {'id', 'lines', 'expression_path', 'semantic_increment', 'risks', 'semantic_lock'},
                'INVALID_UNIT_CANDIDATE', 'Unit candidate fields are incomplete')
        require(_text(item['id']) and item['id'] not in seen_ids, 'DUPLICATE_CANDIDATE_ID', 'Invalid candidate id')
        lines = item['lines']
        require(isinstance(lines, list) and len(lines) == 2 and
                [x.get('line_index') for x in lines if isinstance(x, dict)] == context['line_indices'] and
                all(set(x) == {'line_index', 'text'} and _text(x['text']) for x in lines),
                'INVALID_UNIT_LINES', 'A middle-couplet candidate must contain both complete ordered lines')
        text_key = tuple(x['text'].strip() for x in lines)
        require(text_key not in seen_texts, 'DUPLICATE_TEXT', 'Duplicate complete couplet')
        require(_text(item['expression_path']) and _text(item['semantic_increment']),
                'INVALID_UNIT_EXPLANATION', 'Expression path and semantic increment are required')
        require(isinstance(item['risks'], list) and all(_text(x) for x in item['risks']),
                'INVALID_UNIT_RISKS', 'risks must be an array of strings')
        _validate_lock(item['semantic_lock'], context['planned_unit'])
        seen_ids.add(item['id']); seen_texts.add(text_key); result.append(deepcopy(item))
    return {'status': 'candidates', 'candidates': result}


def filter_units(state, context, payload, *, engine=None):
    validate_unit_context(state, context)
    received = receive_unit_candidates(context, payload)
    if received['status'] == 'replan_required':
        return {**received, 'context': deepcopy(context)}
    engine = engine if engine is not None else ConstraintEngine()
    records = []
    for candidate in received['candidates']:
        staged, line_filters, category = state.fork(), [], 'passed'
        for line in candidate['lines']:
            plan = export_content_plan(staged)
            plan['assignments'] = deepcopy(context['content_plan']['assignments'])
            line_context = export_context(staged, line['line_index'], content_plan=plan,
                                          poem_plan=context['poem_plan'])
            line_payload = {k: line_context[k] for k in ('contract_version', 'batch_id', 'context_sha256',
                                                          'generation_sha256')}
            line_payload.update(attempt=payload['attempt'], candidates=[{
                'id': candidate['id'] + ':' + str(line['line_index']),
                'text': line['text'], 'line_index': line['line_index'],
                'expression_path': candidate['expression_path'],
                'semantic_increment': candidate['semantic_increment'],
                'risks': deepcopy(candidate['risks']),
                'semantic_lock': deepcopy(candidate['semantic_lock'])}])
            filtered = filter_candidates(staged, line_context, line_payload, engine=engine)
            line_filters.append(filtered)
            line_record = filtered['records'][0]
            reading_pending = bool(filtered.get('reading_request', {}).get('reviews'))
            if line_record['category'] != 'passed' and not reading_pending:
                category = 'failed' if line_record['category'] == 'failed' else 'unresolved'
                break
            staged.append_line(line['text'])
        records.append({**deepcopy(candidate), 'line_filters': line_filters, 'category': category,
                        'literary_evaluation': None, 'quality_score': None})
    result = {'status': 'filtered', 'context': deepcopy(context), 'candidate_input': deepcopy(payload),
              'records': records}
    result['filter_sha256'] = digest(result)
    result['reading_request'] = unit_reading_request(result)
    result['content_request'] = unit_content_request(result)
    result['score_request'] = unit_score_request(result)
    return result


def _eligible(record):
    return record['category'] == 'passed' and all(content_eligible(f['records'][0]) for f in record['line_filters'])


def unit_reading_request(filtered):
    requests = []
    for record in filtered['records']:
        for line_filter in record['line_filters']:
            request = line_filter.get('reading_request')
            if request and request.get('reviews') and line_filter.get('reading_input') is None:
                requests.append({'candidate_id': record['id'],
                                 'line_index': line_filter['context']['line_index'],
                                 'request': deepcopy(request)})
    return {**_envelope(filtered['context']), 'filter_sha256': filtered['filter_sha256'],
            'requests': requests}


def review_unit_readings(state, filtered, payload, *, engine=None):
    expected = filtered['reading_request']['requests']
    require(isinstance(payload, dict) and set(payload) == ENVELOPE |
            {'attempt', 'filter_sha256', 'reviews'} and
            all(payload[k] == filtered['context'][k] for k in ENVELOPE) and
            payload['filter_sha256'] == filtered['filter_sha256'] and
            type(payload['attempt']) is int and 1 <= payload['attempt'] <= 3,
            'INVALID_UNIT_READING', 'Unit reading review is not bound to this batch')
    require(isinstance(payload['reviews'], list) and len(payload['reviews']) == len(expected),
            'INVALID_UNIT_READING', 'Every requested line needs one reading review')
    keyed = {(x['candidate_id'], x['line_index']): x for x in payload['reviews']
             if isinstance(x, dict) and set(x) == {'candidate_id', 'line_index', 'response'}}
    require(len(keyed) == len(expected), 'INVALID_UNIT_READING', 'Duplicate or malformed line reading review')
    result = deepcopy(filtered)
    for wanted in expected:
        key = (wanted['candidate_id'], wanted['line_index'])
        require(key in keyed, 'INVALID_UNIT_READING', 'Missing line reading review')
        record = next(x for x in result['records'] if x['id'] == wanted['candidate_id'])
        index = next(i for i, f in enumerate(record['line_filters'])
                     if f['context']['line_index'] == wanted['line_index'])
        staged = state.fork()
        for line in record['lines']:
            if line['line_index'] == wanted['line_index']:
                break
            staged.append_line(line['text'])
        record['line_filters'][index] = review_readings(
            staged, record['line_filters'][index], keyed[key]['response'], engine=engine)
        categories = [line_filter['records'][0]['category'] for line_filter in record['line_filters']]
        record['category'] = ('failed' if 'failed' in categories else
                              'unresolved' if 'unresolved' in categories else 'passed')
    result['reading_input'] = deepcopy(payload)
    result['filter_sha256'] = digest({k: v for k, v in result.items()
                                      if k not in ('filter_sha256', 'reading_request',
                                                   'content_request', 'score_request')})
    result['reading_request'] = unit_reading_request(result)
    result['content_request'] = unit_content_request(result)
    result['score_request'] = unit_score_request(result)
    return result


def unit_content_request(filtered):
    requests = []
    for record in filtered['records']:
        if record['category'] != 'passed':
            continue
        for line_filter in record['line_filters']:
            request = line_filter.get('content_request')
            if request and request.get('candidates'):
                requests.append({'candidate_id': record['id'],
                                 'line_index': line_filter['context']['line_index'],
                                 'request': deepcopy(request)})
    return {**_envelope(filtered['context']), 'filter_sha256': filtered['filter_sha256'],
            'requests': requests}


def review_unit_content(state, filtered, payload, *, engine=None):
    expected = filtered['content_request']['requests']
    require(isinstance(payload, dict) and set(payload) == ENVELOPE | {'attempt', 'filter_sha256', 'reviews'} and
            all(payload[k] == filtered['context'][k] for k in ENVELOPE) and
            payload['filter_sha256'] == filtered['filter_sha256'] and
            type(payload['attempt']) is int and 1 <= payload['attempt'] <= 3,
            'INVALID_UNIT_CONTENT', 'Unit content review is not bound to this batch')
    require(isinstance(payload['reviews'], list) and len(payload['reviews']) == len(expected),
            'INVALID_UNIT_CONTENT', 'Every requested line needs one content review')
    result = deepcopy(filtered)
    keyed = {(x['candidate_id'], x['line_index']): x for x in payload['reviews']
             if isinstance(x, dict) and set(x) == {'candidate_id', 'line_index', 'response'}}
    require(len(keyed) == len(expected), 'INVALID_UNIT_CONTENT', 'Duplicate or malformed line review')
    for wanted in expected:
        key = (wanted['candidate_id'], wanted['line_index'])
        require(key in keyed, 'INVALID_UNIT_CONTENT', 'Missing line content review')
        record = next(x for x in result['records'] if x['id'] == wanted['candidate_id'])
        index = next(i for i, f in enumerate(record['line_filters'])
                     if f['context']['line_index'] == wanted['line_index'])
        # Rebuild the state at the reviewed line so saved filters are revalidated.
        staged = state.fork()
        for line in record['lines']:
            if line['line_index'] == wanted['line_index']:
                break
            staged.append_line(line['text'])
        record['line_filters'][index] = review_content(
            staged, record['line_filters'][index], keyed[key]['response'], engine=engine)
    result['content_input'] = deepcopy(payload)
    result['filter_sha256'] = digest({k: v for k, v in result.items()
                                      if k not in ('filter_sha256', 'content_request', 'score_request')})
    result['content_request'] = unit_content_request(result)
    result['score_request'] = unit_score_request(result)
    return result


def unit_score_request(filtered):
    return {**_envelope(filtered['context']), 'filter_sha256': filtered['filter_sha256'],
            'rubric_version': RUBRIC_VERSION, 'weights': deepcopy(WEIGHTS),
            'candidates': [{k: deepcopy(record[k]) for k in
                            ('id', 'lines', 'expression_path', 'semantic_increment', 'risks', 'semantic_lock')}
                           for record in filtered['records'] if _eligible(record)],
            'instruction': ('评价任务完成、推进、物象、上下文、含蓄、自然、新鲜和后文空间。'
                            '对仗评价须逐字位列出上下句的对应字符、各自句法功能、对应状态与理由，'
                            '并评价整联语义关系、意象关系和是否合掌；低分只影响排序，不把文学判断伪装为格律硬失败。')}


def validate_unit_scores(filtered, payload):
    eligible = {x['id'] for x in filtered['records'] if _eligible(x)}
    require(isinstance(payload, dict) and set(payload) == ENVELOPE |
            {'attempt', 'filter_sha256', 'rubric_version', 'scores'} and
            all(payload[k] == filtered['context'][k] for k in ENVELOPE) and
            payload['filter_sha256'] == filtered['filter_sha256'] and
            payload['rubric_version'] == RUBRIC_VERSION and isinstance(payload['scores'], list),
            'INVALID_UNIT_SCORES', 'Scores are incomplete or belong to another batch')
    result = {}
    for item in payload['scores']:
        require(isinstance(item, dict) and set(item) == {'id', 'dimensions', 'parallelism', 'reason'} and
                item['id'] in eligible and item['id'] not in result,
                'INVALID_UNIT_SCORE', 'Each eligible id may be scored once')
        dims = item['dimensions']
        require(isinstance(dims, dict) and set(dims) == set(WEIGHTS) and
                all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in dims.values()),
                'INVALID_UNIT_SCORE', 'All literary-3 dimensions require finite 0..1 values')
        parallel = item['parallelism']
        require(isinstance(parallel, dict) and set(parallel) == PARALLEL_FIELDS,
                'INVALID_PARALLELISM', 'Middle couplets require complete character-level parallelism analysis')
        for key in ('semantic_relation', 'image_relation', 'hangzhang'):
            require(isinstance(parallel[key], dict) and set(parallel[key]) == {'status', 'reason'} and
                    parallel[key]['status'] in ('pass', 'weak', 'fail', 'unknown') and _text(parallel[key]['reason']),
                    'INVALID_PARALLELISM', key + ' needs status and reason')
        try:
            record = next(x for x in filtered['records'] if x['id'] == item['id'])
            validate_character_alignment({x['line_index']: x['text'] for x in record['lines']},
                                         parallel['character_alignment'],
                                         filtered['context']['line_indices'],
                                         filtered['context']['form']['line_length'])
        except (ValueError, StopIteration, KeyError) as exc:
            require(False, 'INVALID_CHARACTER_ALIGNMENT', str(exc))
        require(type(parallel['score']) in (int, float) and 0 <= parallel['score'] <= 1 and
                _text(parallel['reason']) and _text(item['reason']),
                'INVALID_PARALLELISM', 'Parallelism score and reasons are required')
        require(abs(dims['parallelism'] - parallel['score']) < 1e-12,
                'PARALLELISM_SCORE_MISMATCH', 'The literary dimension must equal the structured score')
        total = sum(Fraction(str(dims[k])) * Fraction(str(v)) for k, v in WEIGHTS.items())
        result[item['id']] = {**deepcopy(item), 'total': float(total), 'rubric_version': RUBRIC_VERSION}
    require(set(result) == eligible, 'MISSING_UNIT_SCORE', 'Every eligible unit needs a score')
    return result


def finish_units(filtered, scores):
    evaluations = validate_unit_scores(filtered, scores)
    records = deepcopy(filtered['records'])
    for record in records:
        record['literary_evaluation'] = evaluations.get(record['id'])
        record['quality_score'] = (evaluations[record['id']]['total'] if record['id'] in evaluations else None)
    ranking = [x['id'] for x in sorted(records, key=lambda x: (x['quality_score'] is None,
                                                               -(x['quality_score'] or 0)))]
    ranking = [candidate_id for candidate_id in ranking
               if next(x for x in records if x['id'] == candidate_id)['quality_score'] is not None]
    return {'status': 'completed' if ranking else 'unavailable',
            'code': 'UNIT_EVALUATED' if ranking else 'NO_PASSED_UNIT',
            'records': records, 'ranking': ranking}
