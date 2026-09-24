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
                          review_content, review_readings, review_duplicates, content_eligible, require)
from round_content import export_content_plan
from character_alignment import (validate_character_alignment, validate_structure_alignment,
                                 validate_soft_rating)

CONTRACT_VERSION = 'poetry-unit-3'
RUBRIC_VERSION = 'literary-5'
WEIGHTS = {'task_completion': .14, 'semantic_progression': .11,
           'image_relation': .09, 'context_consistency': .10,
           'implicitness': .07, 'naturalness': .10, 'freshness': .045,
           'future_space': .095, 'parallelism': .25}
LOCK_FIELDS = {'protected_meaning', 'facts', 'images', 'relations', 'emotion_direction',
               'structural_function', 'confirmed_allusions'}
PARALLEL_FIELDS = {'semantic_relation', 'image_relation', 'hangzhang',
                   'character_alignment', 'structure_alignment',
                   'character_score', 'structure_score', 'score', 'reason'}
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
                                   + ('本联承担承接职责，须接住首联并扩展而非复述；' if unit.get('arc_role') == '承' else
                                      '本联承担转向职责，须依据 turn_plan 改变前文状态或解释、说明铺垫并为尾联留出收束空间；不可只续写下一事件。' if unit.get('arc_role') == '转' else '')
                                    + ('若转向会破坏用户事实、典故或保护语义，返回 replan_required，不得硬补跳景。'
                                       if unit.get('arc_role') == '转' else '')
                                   + '提交风险与 semantic_lock。对仗须双层核对：逐字位说明每个对应字的句法功能；逐结构位将上下句切分为连续、完整、不重叠的词组／句法成分，并说明对应关系。'
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
    expected = ENVELOPE | {'attempt', 'status', 'candidates'}
    if not isinstance(payload, dict) or set(payload) != expected:
        actual = set(payload) if isinstance(payload, dict) else set()
        missing, unexpected = sorted(expected - actual), sorted(actual - expected)
        from poetry_round import RoundError
        raise RoundError('INVALID_UNIT_RESPONSE',
                         f'Invalid unit response: missing={missing}; unexpected={unexpected}',
                         [{'location': 'response', 'missing_fields': missing,
                           'unexpected_fields': unexpected}])
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
    expected_candidate = {'id', 'lines', 'expression_path', 'semantic_increment', 'risks', 'semantic_lock'}
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != expected_candidate:
            actual = set(item) if isinstance(item, dict) else set()
            missing, unexpected = sorted(expected_candidate - actual), sorted(actual - expected_candidate)
            from poetry_round import RoundError
            raise RoundError('INVALID_UNIT_CANDIDATE',
                             f'Unit candidate {index} fields invalid: missing={missing}; unexpected={unexpected}',
                             [{'candidate_index': index, 'missing_fields': missing,
                               'unexpected_fields': unexpected,
                               'expected_fields': sorted(expected_candidate)}])
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
            duplicate_pending = bool(filtered.get('duplicate_request', {}).get('reviews'))
            if line_record['category'] != 'passed' and not reading_pending and not duplicate_pending:
                category = 'failed' if line_record['category'] == 'failed' else 'unresolved'
                break
            staged.append_line(line['text'])
        records.append({**deepcopy(candidate), 'line_filters': line_filters, 'category': category,
                        'literary_evaluation': None, 'quality_score': None})
    result = {'status': 'filtered', 'context': deepcopy(context), 'candidate_input': deepcopy(payload),
              'records': records}
    result['filter_sha256'] = digest(result)
    result['reading_request'] = unit_reading_request(result)
    result['duplicate_request'] = unit_duplicate_request(result)
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


def unit_duplicate_request(filtered):
    requests = []
    for record in filtered['records']:
        for line_filter in record['line_filters']:
            request = line_filter.get('duplicate_request')
            if request and request.get('reviews') and line_filter.get('duplicate_input') is None:
                requests.append({'candidate_id': record['id'],
                                 'line_index': line_filter['context']['line_index'],
                                 'request': deepcopy(request)})
    return {**_envelope(filtered['context']), 'filter_sha256': filtered['filter_sha256'],
            'requests': requests}


def review_unit_duplicates(state, filtered, payload, *, engine=None):
    expected = filtered['duplicate_request']['requests']
    require(isinstance(payload, dict) and set(payload) == ENVELOPE |
            {'attempt', 'filter_sha256', 'reviews'} and
            all(payload[k] == filtered['context'][k] for k in ENVELOPE) and
            payload['filter_sha256'] == filtered['filter_sha256'] and
            type(payload['attempt']) is int and 1 <= payload['attempt'] <= 3,
            'INVALID_UNIT_DUPLICATE', 'Unit duplicate review is not bound to this batch')
    require(isinstance(payload['reviews'], list) and len(payload['reviews']) == len(expected),
            'INVALID_UNIT_DUPLICATE', 'Every requested line needs one duplicate review')
    keyed = {(x['candidate_id'], x['line_index']): x for x in payload['reviews']
             if isinstance(x, dict) and set(x) == {'candidate_id', 'line_index', 'response'}}
    require(len(keyed) == len(expected), 'INVALID_UNIT_DUPLICATE', 'Duplicate or malformed line review')
    result = deepcopy(filtered)
    for wanted in expected:
        key = (wanted['candidate_id'], wanted['line_index'])
        require(key in keyed, 'INVALID_UNIT_DUPLICATE', 'Missing line duplicate review')
        record = next(x for x in result['records'] if x['id'] == wanted['candidate_id'])
        index = next(i for i, f in enumerate(record['line_filters'])
                     if f['context']['line_index'] == wanted['line_index'])
        staged = state.fork()
        for line in record['lines']:
            if line['line_index'] == wanted['line_index']:
                break
            staged.append_line(line['text'])
        record['line_filters'][index] = review_duplicates(
            staged, record['line_filters'][index], keyed[key]['response'], engine=engine)
        categories = [line_filter['records'][0]['category'] for line_filter in record['line_filters']]
        record['category'] = ('failed' if 'failed' in categories else
                              'unresolved' if 'unresolved' in categories else 'passed')
    result['duplicate_input'] = deepcopy(payload)
    result['filter_sha256'] = digest({k: v for k, v in result.items()
                                      if k not in ('filter_sha256', 'reading_request', 'duplicate_request',
                                                   'content_request', 'score_request')})
    result['duplicate_request'] = unit_duplicate_request(result)
    result['content_request'] = unit_content_request(result)
    result['score_request'] = unit_score_request(result)
    return result


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
                                      if k not in ('filter_sha256', 'reading_request', 'duplicate_request',
                                                   'content_request', 'score_request')})
    result['reading_request'] = unit_reading_request(result)
    result['duplicate_request'] = unit_duplicate_request(result)
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
                                 'request': {k: deepcopy(v) for k, v in request.items()
                                             if k != 'context'}})
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
                                      if k not in ('filter_sha256', 'reading_request', 'duplicate_request',
                                                   'content_request', 'score_request')})
    result['reading_request'] = unit_reading_request(result)
    result['duplicate_request'] = unit_duplicate_request(result)
    result['content_request'] = unit_content_request(result)
    result['score_request'] = unit_score_request(result)
    return result


def unit_score_request(filtered):
    return {**_envelope(filtered['context']), 'filter_sha256': filtered['filter_sha256'],
            'rubric_version': RUBRIC_VERSION, 'weights': deepcopy(WEIGHTS),
            'arc_role': filtered['context']['planned_unit'].get('arc_role'),
            'turn_plan': deepcopy(filtered['context']['planned_unit'].get('turn_plan')),
            'assessment_instruction': (
                '按本联规划职责评价。颈联须结合 turn_plan 和诗稿证据判断是否发生状态、立场、认知或意义变化；'
                '仅有下一事件不算转向。不得仅因未换场景或意象判失败。'
                if filtered['context']['planned_unit'].get('arc_role') == '转'
                else '评价本联如何完成规划职责及其语义增量。'),
            'candidates': [{k: deepcopy(record[k]) for k in
                            ('id', 'lines', 'expression_path', 'semantic_increment', 'risks', 'semantic_lock')}
                           for record in filtered['records'] if _eligible(record)],
            'instruction': ('评价任务完成、推进、物象、上下文、含蓄、自然、新鲜和后文空间。'
                            '对仗必须分别评价逐字位和逐结构位：结构分段应连续、完整覆盖上下句且不得重叠；两层分别打分，parallelism.score 必须为二者算术平均。'
                            '明显失配须降低对应层分数；任一层低分会通过较高对仗权重显著影响搜索排序，但仍是软约束，不直接判作格律失败。'
                            '同时评价整联语义关系、意象关系和合掌情况。')}


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
                'INVALID_UNIT_SCORE', 'All literary-5 dimensions require finite 0..1 values')
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
            validate_structure_alignment({x['line_index']: x['text'] for x in record['lines']},
                                         parallel['structure_alignment'],
                                         filtered['context']['line_indices'],
                                         filtered['context']['form']['line_length'])
        except (ValueError, StopIteration, KeyError) as exc:
            require(False, 'INVALID_PARALLEL_ALIGNMENT', str(exc))
        require(all(type(parallel[key]) in (int, float) and math.isfinite(parallel[key]) and
                    0 <= parallel[key] <= 1 for key in ('character_score', 'structure_score', 'score')) and
                _text(parallel['reason']) and _text(item['reason']),
                'INVALID_PARALLELISM', 'Character, structure and combined scores must be valid')
        require(abs(parallel['score'] - (parallel['character_score'] + parallel['structure_score']) / 2) < 1e-12,
                'PARALLELISM_SCORE_MISMATCH', 'Combined score must be the mean of the two layers')
        try:
            validate_soft_rating(parallel['character_alignment'], parallel['character_score'], 'Character')
            validate_soft_rating(parallel['structure_alignment'], parallel['structure_score'], 'Structure')
        except ValueError as exc:
            require(False, 'PARALLELISM_SCORE_CONFLICT', str(exc))
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
