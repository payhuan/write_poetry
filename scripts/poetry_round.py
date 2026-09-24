"""One round of agent-generated lines: context, pure filtering, scoring and commit.

No model API, line adoption, search, or automatic pronunciation selection.
All returned JSON is detached from state. CLI paths are relative to this project.
"""

import argparse
from copy import deepcopy
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

from allusion_lexicon import AllusionLexicon
from assessment_binding import digest, make_binding
from constraint_engine import ConstraintEngine
from poem_planning import unit_for_line
from poem_state import PoemState
from text_normalizer import IGNORED
from round_content import (CONTENT_VERSION, ContentError, export_content_plan,
                           validate_content_plan, locked_candidate, check_content,
                           content_eligible, semantic_request, apply_semantic_reviews,
                           read_content_fulfillment)
from runtime_workspace import configure_workspace, input_path, output_path
from reading_disambiguation import make_request as make_reading_request, apply_review as apply_reading_review
from duplicate_disambiguation import make_request as make_duplicate_request, apply_review as apply_duplicate_review
from duplicate_scoring import adjust_score

PROJECT = Path(__file__).resolve().parent.parent
CONTRACT_VERSION = 'poetry-round-3'
RUBRIC_VERSION = 'literary-2'
WEIGHTS = {'theme': 0.25, 'naturalness': 0.25, 'imagery': 0.20,
           'continuity': 0.15, 'expression': 0.15}
MAX_FORMAT_RETRIES = 2
ENVELOPE = {'contract_version', 'batch_id', 'context_sha256', 'generation_sha256'}
_ALLUSION_LEXICON = None


def _allusion_lexicon():
    global _ALLUSION_LEXICON
    if _ALLUSION_LEXICON is None:
        _ALLUSION_LEXICON = AllusionLexicon()
    return _ALLUSION_LEXICON


class RoundError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details or []

    def to_dict(self):
        return {'status': 'error', 'code': self.code, 'message': str(self), 'details': self.details}


def require(condition, code, message):
    if not condition:
        raise RoundError(code, message)


def finite_json(value):
    try:
        digest(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise RoundError('INVALID_JSON', 'Expected finite JSON data') from exc


def read_json(text):
    """Reject duplicate object keys and nonstandard NaN/Infinity, not just syntax."""
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_JSON_KEY', 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise RoundError('INVALID_JSON', 'Non-finite JSON constant: ' + value)
    try:
        result = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except json.JSONDecodeError as exc:
        raise RoundError('INVALID_JSON', str(exc)) from exc
    finite_json(result)
    return result


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def perimeter_text(text):
    """Only trim agreed peripheral punctuation/whitespace; preserve the interior."""
    start, end = 0, len(text)
    while start < end and (text[start].isspace() or text[start] in IGNORED):
        start += 1
    while end > start and (text[end - 1].isspace() or text[end - 1] in IGNORED):
        end -= 1
    return text[start:end]


def _context(state, line_index, batch_id, content_plan=None, poem_plan=None):
    d = state.to_dict()
    require(type(line_index) is int and 1 <= line_index <= d['form']['line_count'],
            'INVALID_LINE_INDEX', 'line_index must be a one-based position in the form')
    require('meter_template' in d, 'TEMPLATE_UNBOUND', 'Bind a template snapshot before generation')
    t = d['meter_template']['data']
    is_rhyme = line_index in t['rhyme']['positions']
    reasons = ([{'code': 'RHYME_GROUP_UNSELECTED', 'message': '当前句需要押韵，请先完成韵部规划。'}]
               if is_rhyme and d['rhyme']['group'] is None else [])
    allusions = d['request'].get('allusions', [])
    fragments = d['request'].get('required_fragments', [])
    planned_unit = unit_for_line(poem_plan, line_index) if poem_plan is not None else None
    allusion_references = _allusion_lexicon().resolve_requirements(allusions) if allusions else []
    for reference, requirement in zip(allusion_references, allusions):
        if requirement['required'] and reference['status'] == 'ambiguous':
            reasons.append({'code': 'ALLUSION_DISAMBIGUATION_REQUIRED',
                            'requirement_id': requirement['id'],
                            'choices': [{'allusion_id': choice['allusion_id'],
                                         'canonical_name': choice['canonical_name'],
                                         'raw_nav_name': choice['raw_nav_name']}
                                        for choice in reference['choices']],
                            'message': '必用典故名称在本地索引中对应多条记录，请先选定主典。'})
    plan = None
    if allusions or fragments or content_plan is not None:
        plan, unresolved = validate_content_plan(state, content_plan)
        if unresolved:
            reasons.append({'code': 'CONTENT_ASSIGNMENT_REQUIRED', 'requirements': unresolved,
                            'message': '先安排未指定位置的必含片段和必用典故；不要要求每句包含全部内容。'})
    couplet = None
    if d['form']['line_count'] == 8:
        number = (line_index - 1) // 2
        unit = ('首联', '颔联', '颈联', '尾联')[number]
        partner = line_index + 1 if line_index % 2 else line_index - 1
        couplet = {'unit': unit, 'line_indices': [number * 2 + 1, number * 2 + 2],
                   'intents': [x['intent'] for x in d['structure'] if x['unit'] == unit],
                   'paired_line_index': partner,
                   'paired_text': d['lines'][partner - 1] if partner <= len(d['lines']) else None,
                   'semantic_parallelism': '仅由文学评价观察，程序未判断语义对仗'}
    result = {
        'contract_version': CONTRACT_VERSION, 'batch_id': batch_id,
        'context_sha256': make_binding(d)['context_sha256'], 'revision': d['revision'],
        'status': 'planning_required' if reasons else 'ready', 'reasons': reasons,
        'request': d['request'], 'theme': d['theme'], 'emotion': d['emotion'],
        'allusions': allusions, 'allusion_references': allusion_references,
        'required_fragments': fragments, 'content_plan': plan,
        'poem_plan': deepcopy(poem_plan), 'planned_unit': planned_unit,
        'imagery': d['imagery'], 'structure': d['structure'], 'lines': d['lines'],
        'poem_type': d['poem_type'], 'form': d['form'], 'line_index': line_index,
        'template': d['meter_template'], 'user_meter': d['request']['meter'],
        'tone_requirements': sorted([x for x in t['tunes'] if x['line'] == line_index], key=lambda x: x['pos']),
        'is_rhyme_line': is_rhyme, 'rhyme': d['rhyme'], 'constraints': d['constraints'],
        'couplet': couplet, 'budget': d['budget'],
        'candidate_count': {'min': 1, 'max': d['budget']['candidates_per_round'],
                            'target': min(5, d['budget']['candidates_per_round'])},
        'retry_policy': {'max_attempts_per_stage': MAX_FORMAT_RETRIES + 1,
                         'automatic_retries': False},
        'generation_instruction': ('生成语义完整的候选句，完成 planned_unit 的主要任务与语义增量，'
                                   '保留保护语义且不得提前表达 must_not_say_yet。不得逐字填空、自动改句或猜选有利读音。'
                                   '用典时以 allusion_references 的规范主典、简释和出处原文为依据；'
                                   'not_found 只表示局部语料未收录，须另行可靠核实，不能据此编造。'),
    }
    result['locked_candidate'] = locked_candidate(result)
    if result['locked_candidate'] is not None:
        result['candidate_count'] = {'min': 1, 'max': 1, 'target': 1}
        result['generation_instruction'] = '本句锁定 exact 完整句，跳过模型生成，直接调用 filter 送检原文，不改写。'
    result['generation_sha256'] = digest(result)
    return result


def export_context(state, line_index, *, content_plan=None, poem_plan=None):
    """Export a stable batch token and all model context, without changing state."""
    return _context(state, line_index, uuid4().hex, content_plan, poem_plan)


def _validate_context(state, context):
    require(isinstance(context, dict), 'INVALID_CONTEXT', 'Context must be an object')
    batch_id = context.get('batch_id')
    require(isinstance(batch_id, str) and len(batch_id) == 32 and
            all(c in '0123456789abcdef' for c in batch_id), 'INVALID_BATCH', 'Invalid exported batch ID')
    expected = _context(state, context.get('line_index'), batch_id, context.get('content_plan'),
                        context.get('poem_plan'))
    require(context == expected, 'STALE_CONTEXT', 'Context, revision or budget changed; export a new batch')
    require(context['status'] == 'ready', 'PLANNING_REQUIRED', 'Complete planning before generating this line')


def _envelope(context):
    return {k: context[k] for k in sorted(ENVELOPE)}


def _validate_envelope(payload, context, extra):
    finite_json(payload)
    expected = ENVELOPE | extra | {'attempt'}
    if not isinstance(payload, dict) or set(payload) != expected:
        actual = set(payload) if isinstance(payload, dict) else set()
        missing, unexpected = sorted(expected - actual), sorted(actual - expected)
        raise RoundError('INVALID_ENVELOPE',
                         f"Invalid response envelope: missing={missing}; unexpected={unexpected}",
                         [{'location': 'response', 'missing_fields': missing,
                           'unexpected_fields': unexpected}])
    require(all(payload[k] == context[k] for k in ENVELOPE), 'WRONG_BATCH', 'Model response batch/context does not match')
    require(type(payload['attempt']) is int and 1 <= payload['attempt'] <= MAX_FORMAT_RETRIES + 1,
            'RETRY_EXHAUSTED', 'At most three attempts per model stage; no automatic retry')


def _receive(context, payload):
    _validate_envelope(payload, context, {'candidates'})
    items = payload['candidates']
    require(isinstance(items, list), 'INVALID_CANDIDATES', 'candidates must be an array')
    require(1 <= len(items) <= context['candidate_count']['max'], 'CANDIDATE_BUDGET',
            'Candidate count must be 1..candidates_per_round (including duplicates)')
    ids, problems = set(), []
    planned = context.get('planned_unit')
    for index, candidate in enumerate(items):
        code = None
        base_fields = {'id', 'text', 'line_index'}
        enhanced_fields = base_fields | {'expression_path', 'semantic_increment', 'risks', 'semantic_lock'}
        expected = base_fields if planned is None else enhanced_fields
        if not isinstance(candidate, dict) or set(candidate) not in ((base_fields, enhanced_fields)
                                                                      if planned is not None else (base_fields,)):
            code = 'INVALID_CANDIDATE_FIELDS'
            actual = set(candidate) if isinstance(candidate, dict) else set()
            allowed = (base_fields, enhanced_fields) if planned is not None else (base_fields,)
            best = min(allowed, key=lambda fields: len(fields - actual) + len(actual - fields))
            problems.append({'input_index': index, 'candidate_index': index,
                             'code': code, 'raw': deepcopy(candidate),
                             'expected_fields': sorted(best),
                             'missing_fields': sorted(best - actual),
                             'unexpected_fields': sorted(actual - best)})
            continue
        elif not _text(candidate['id']):
            code = 'INVALID_CANDIDATE_ID'
        elif candidate['id'] in ids:
            code = 'DUPLICATE_CANDIDATE_ID'
        elif type(candidate['line_index']) is not int or candidate['line_index'] != context['line_index']:
            code = 'WRONG_LINE_INDEX'
        elif not _text(candidate['text']) or not perimeter_text(candidate['text']):
            code = 'EMPTY_CANDIDATE_TEXT'
        elif planned is not None and set(candidate) == enhanced_fields:
            lock = candidate['semantic_lock']
            lock_fields = {'protected_meaning', 'facts', 'images', 'relations', 'emotion_direction',
                           'structural_function', 'confirmed_allusions'}
            if (not _text(candidate['expression_path']) or not _text(candidate['semantic_increment']) or
                    not isinstance(candidate['risks'], list) or
                    any(not _text(x) for x in candidate['risks']) or
                    not isinstance(lock, dict) or set(lock) != lock_fields or
                    any(not isinstance(lock[k], list) or any(not _text(x) for x in lock[k])
                        for k in ('facts', 'images', 'relations', 'confirmed_allusions')) or
                    any(not _text(lock[k]) for k in ('protected_meaning', 'emotion_direction',
                                                     'structural_function')) or
                    lock['structural_function'] != planned['primary_task'] or
                    any(x not in lock['protected_meaning'] for x in planned['protected_meaning'])):
                code = 'INVALID_SEMANTIC_LOCK'
        if isinstance(candidate, dict) and _text(candidate.get('id')):
            ids.add(candidate['id'])
        if code:
            problems.append({'input_index': index, 'code': code, 'raw': deepcopy(candidate)})
    if problems:
        raise RoundError('CANDIDATE_FORMAT_ERROR', 'Batch rejected before rule checking', problems)
    return deepcopy(items)


def _rule_reasons(checks):
    return [{'check': name, **issue} for name, check in checks['checks'].items()
            for issue in check.get('issues', [])] + [
                {'code': 'DUPLICATE_CHARACTER', 'message': error}
                for error in checks['errors'] if error.startswith('duplicate:')]


def filter_candidates(state, context, payload=None, *, engine=None, readings=None, duplicate_reviews=None):
    """Validate the entire input first, then check all unique lines on one snapshot.

    Day 3 deliberately does not accept model-supplied pronunciation selectors.
    """
    _validate_context(state, context)
    if context['locked_candidate'] is not None:
        locked_payload = {**_envelope(context), 'attempt': 1, 'candidates': [context['locked_candidate']]}
        require(payload is None or payload == locked_payload, 'LOCKED_EXACT_LINE',
                'This line is locked: omit candidate input and check the original fragment directly')
        payload = locked_payload
    items = _receive(context, payload)
    snapshot = PoemState.from_dict(state.to_dict())
    engine = engine if engine is not None else ConstraintEngine()
    records, seen = [], {}
    for index, candidate in enumerate(items):
        normalized = perimeter_text(candidate['text'])
        record = {**candidate, 'input_index': index, 'normalized_text': normalized,
                  'history_id': context['batch_id'] + ':' + candidate['id'],
                  'checks': None, 'literary_evaluation': None, 'quality_score': None,
                  'duplicate_of': seen.get(normalized), 'reasons': []}
        if normalized in seen:
            record.update(category='duplicate', reasons=[{'code': 'DUPLICATE_TEXT',
                          'message': '仅忽略外围空白和约定标点后与较早候选相同。'}])
        else:
            seen[normalized] = candidate['id']
            checks = engine.check_candidate({'id': record['history_id'], 'text': candidate['text'],
                                              'line_index': candidate['line_index']}, snapshot,
                                             readings=readings,
                                             duplicate_review=(duplicate_reviews or {}).get(record['id']))
            record.update(checks=checks, category=('passed' if checks['valid'] is True else
                          'failed' if checks['valid'] is False else 'unresolved'),
                          reasons=_rule_reasons(checks))
            if context['locked_candidate'] is not None and checks['valid'] is not True:
                record['reasons'].append({'code': 'LOCKED_EXACT_RULE_CONFLICT' if checks['valid'] is False else 'LOCKED_EXACT_RULE_UNKNOWN',
                    'message': '原样完整句与规则明确冲突，请用户调整要求。' if checks['valid'] is False else
                               '原样完整句的规则结果尚不明确，不能视为通过或自动选音。'})
        if context['content_plan'] is not None:
            record['content_result'] = check_content(snapshot, context, {**candidate, 'id': record['history_id']})
        records.append(record)
    result = {'context': deepcopy(context), 'candidate_input': deepcopy(payload),
              'reading_seed': deepcopy(readings), 'records': records}
    if context['content_plan'] is not None:
        result['content_input'] = None
    _seal_filter(result)
    request = _reading_request(result)
    if request['reviews']:
        result['reading_input'] = None
        result['reading_request'] = request
    result['duplicate_request'] = _duplicate_request(result)
    if context['content_plan'] is not None:
        result['content_request'] = semantic_request(result)
    return result


def _seal_filter(result):
    result['filter_sha256'] = digest({k: v for k, v in result.items()
                                      if k not in ('filter_sha256', 'score_request', 'content_request',
                                                   'reading_request', 'duplicate_request')})
    result['score_request'] = _score_request(result)


def _reading_request(filtered):
    context = filtered['context']
    reviews = []
    for record in filtered['records']:
        checks = record.get('checks')
        if (record['category'] == 'unresolved' and checks and
                checks.get('checks', {}).get('reading', {}).get('status') == 'unknown'):
            reviews.append({'id': record['id'], 'line_index': record['line_index'],
                            'text_sha256': digest(record['text']),
                            'request': make_reading_request(checks)})
    return {**_envelope(context), 'filter_sha256': filtered['filter_sha256'],
            'reviews': reviews,
            'instruction': ('逐候选按 reading-review-1 结合句义选择韵表已提供的归属。'
                            '不得依据哪个选项更合格律倒推读音；证据不足返回 unknown。')}


def _duplicate_request(filtered):
    reviews = []
    for record in filtered['records']:
        checks = record.get('checks')
        duplicate = (checks or {}).get('checks', {}).get('duplicate') or {}
        if duplicate.get('status') == 'unknown':
            reviews.append({'id': record['id'], 'request': make_duplicate_request(checks)})
    return {**_envelope(filtered['context']), 'filter_sha256': filtered['filter_sha256'],
            'reviews': reviews}


def _apply_readings(state, filtered, payload, engine):
    require('reading_request' in filtered, 'NO_READING_REVIEW', 'This batch has no unresolved readings')
    _validate_envelope(payload, filtered['context'], {'filter_sha256', 'reviews'})
    require(payload['filter_sha256'] == filtered['filter_sha256'], 'WRONG_FILTER',
            'Reading review belongs to another filtered batch')
    expected = {item['id']: item for item in filtered['reading_request']['reviews']}
    require(isinstance(payload['reviews'], list) and len(payload['reviews']) == len(expected),
            'INVALID_READING_REVIEWS', 'Every requested candidate needs one reading review')
    supplied = {}
    for item in payload['reviews']:
        require(isinstance(item, dict) and set(item) == {'id', 'review'} and
                item['id'] in expected and item['id'] not in supplied,
                'INVALID_READING_REVIEWS', 'Unknown, duplicate or malformed reading review')
        supplied[item['id']] = item['review']
    result = deepcopy(filtered)
    snapshot = PoemState.from_dict(state.to_dict())
    for record in result['records']:
        if record['id'] not in expected:
            continue
        review = apply_reading_review(expected[record['id']]['request'], supplied[record['id']])
        checks = engine.check_candidate({'id': record['history_id'], 'text': record['text'],
                                          'line_index': record['line_index']}, snapshot,
                                         readings=review['readings'])
        record.update(checks=checks, reading_review=review,
                      category=('passed' if checks['valid'] is True else
                                'failed' if checks['valid'] is False else 'unresolved'),
                      reasons=_rule_reasons(checks))
    result['reading_input'] = deepcopy(payload)
    _seal_filter(result)
    result['reading_request'] = _reading_request(result)
    if result['context']['content_plan'] is not None:
        result['content_request'] = semantic_request(result)
    result['duplicate_request'] = _duplicate_request(result)
    return result


def _apply_duplicates(state, filtered, payload, engine):
    require('duplicate_request' in filtered, 'NO_DUPLICATE_REVIEW', 'This batch has no unresolved repeated characters')
    _validate_envelope(payload, filtered['context'], {'filter_sha256', 'reviews'})
    require(payload['filter_sha256'] == filtered['filter_sha256'], 'WRONG_FILTER',
            'Duplicate review belongs to another filtered batch')
    expected = {item['id']: item['request'] for item in filtered['duplicate_request']['reviews']}
    require(isinstance(payload['reviews'], list) and len(payload['reviews']) == len(expected),
            'INVALID_DUPLICATE_REVIEWS', 'Every unresolved candidate needs one duplicate review')
    supplied = {}
    for item in payload['reviews']:
        require(isinstance(item, dict) and set(item) == {'id', 'review'} and
                item['id'] in expected and item['id'] not in supplied,
                'INVALID_DUPLICATE_REVIEWS', 'Unknown, duplicate or malformed duplicate review')
        supplied[item['id']] = item['review']
    result = deepcopy(filtered)
    snapshot = PoemState.from_dict(state.to_dict())
    for record in result['records']:
        if record['id'] not in expected:
            continue
        request = expected[record['id']]
        review = apply_duplicate_review(request, supplied[record['id']])
        reading_review = record.get('reading_review') or {}
        checks = engine.check_candidate({'id': record['history_id'], 'text': record['text'],
                                          'line_index': record['line_index']}, snapshot,
                                         readings={**deepcopy(filtered['reading_seed'] or {}),
                                                   **deepcopy(reading_review.get('readings', {}))},
                                         duplicate_review=review)
        record.update(checks=checks, duplicate_review=review,
                      category=('passed' if checks['valid'] is True else
                                'failed' if checks['valid'] is False else 'unresolved'),
                      reasons=_rule_reasons(checks))
    result['duplicate_input'] = deepcopy(payload)
    _seal_filter(result)
    result['duplicate_request'] = _duplicate_request(result)
    if result['context']['content_plan'] is not None:
        result['content_request'] = semantic_request(result)
    return result


def _apply_content(filtered, payload):
    require('content_request' in filtered, 'NO_CONTENT_REQUIREMENTS', 'This batch has no content requirements')
    _validate_envelope(payload, filtered['context'], {'filter_sha256', 'content_version', 'reviews'})
    require(payload['filter_sha256'] == filtered['filter_sha256'], 'WRONG_FILTER', 'Content review belongs to another filtered batch')
    require(payload['content_version'] == CONTENT_VERSION, 'WRONG_CONTENT_VERSION', 'Unsupported content review version')
    result = deepcopy(filtered)
    result['records'] = apply_semantic_reviews(filtered, payload)
    result['content_input'] = deepcopy(payload)
    _seal_filter(result)
    result['duplicate_request'] = _duplicate_request(result)
    return result


def _verify_filter(state, filtered, engine):
    finite_json(filtered)
    base = {'context', 'candidate_input', 'reading_seed', 'records', 'filter_sha256', 'score_request'}
    optional = {'content_input', 'content_request', 'reading_input', 'reading_request',
                'duplicate_input', 'duplicate_request'}
    require(isinstance(filtered, dict) and base <= set(filtered) and not set(filtered) - base - optional,
            'INVALID_FILTER', 'Expected a complete program-generated filter artifact')
    verified = filter_candidates(state, filtered['context'], filtered['candidate_input'], engine=engine,
                                 readings=filtered['reading_seed'])
    if filtered.get('reading_input') is not None:
        verified = _apply_readings(state, verified, filtered['reading_input'], engine)
    if filtered.get('duplicate_input') is not None:
        verified = _apply_duplicates(state, verified, filtered['duplicate_input'], engine)
    if filtered.get('content_input') is not None:
        verified = _apply_content(verified, filtered['content_input'])
    require(_filter_comparison_view(verified) == _filter_comparison_view(filtered),
            'FILTER_MISMATCH', 'Saved filter changed or rule data differs; filter again')
    return verified


def _filter_comparison_view(filtered):
    """Ignore legacy context copies inside derived requests; the bound sibling is authoritative."""
    result = deepcopy(filtered)
    for key in ('score_request', 'content_request'):
        request = result.get(key)
        if isinstance(request, dict):
            request.pop('context', None)
    return result


def review_content(state, filtered, payload, *, engine=None):
    """Import semantic fulfillment before literary scoring, without writing state."""
    verified = _verify_filter(state, filtered, engine)
    require(verified.get('content_input') is None, 'CONTENT_ALREADY_REVIEWED', 'Review the original filter artifact when correcting a review')
    return _apply_content(verified, payload)


def review_readings(state, filtered, payload, *, engine=None):
    """Import bound reading choices as a separate stage, then rerun the engine."""
    engine = engine if engine is not None else ConstraintEngine()
    verified = _verify_filter(state, filtered, engine)
    require(verified.get('reading_input') is None, 'READING_ALREADY_REVIEWED',
            'Review the original filter artifact when correcting reading choices')
    return _apply_readings(state, verified, payload, engine)


def review_duplicates(state, filtered, payload, *, engine=None):
    engine = engine if engine is not None else ConstraintEngine()
    verified = _verify_filter(state, filtered, engine)
    require(verified.get('duplicate_input') is None, 'DUPLICATE_ALREADY_REVIEWED',
            'Review the original filter artifact when correcting a duplicate review')
    return _apply_duplicates(state, verified, payload, engine)


def _score_request(filtered):
    context = filtered['context']
    candidates = []
    for record in filtered['records']:
        if not content_eligible(record):
            continue
        after = list(context['lines'])
        index = record['line_index'] - 1
        if index < len(after):
            after[index] = record['text']
        elif index == len(after):
            after.append(record['text'])
        else:
            after.extend([''] * (index - len(after)))
            after.append(record['text'])
        _, adjustment = adjust_score(0.0, context['lines'], after)
        candidates.append({**{k: record[k] for k in ('id', 'text', 'line_index')},
                           **({k: deepcopy(record[k]) for k in
                               ('expression_path', 'semantic_increment', 'risks', 'semantic_lock')}
                              if 'semantic_lock' in record else {}),
                           **({'content_fulfillment': deepcopy(record['content_result'])}
                              if 'content_result' in record else {}),
                           'duplicate_score_adjustment': adjustment})
    return {**_envelope(context), 'filter_sha256': filtered['filter_sha256'],
            'rubric_version': RUBRIC_VERSION, 'weights': deepcopy(WEIGHTS),
            'duplicate_score_policy': {'first_group_free': True,
                                       'penalty_per_additional_group': 0.20,
                                       'applied_by_python_after_model_score': True},
            'scale': {'0': '不符合该维度', '0.25': '明显薄弱', '0.5': '基本成立但有明显不足',
                      '0.75': '表现良好，只有局部不足', '1': '在当前上下文中充分且准确'},
            'candidates': candidates,
            'instruction': '只评价此处列出的规则和本句必需内容均通过的候选；各维度给有限 0–1 数值和简短理由。未评价填 null。'
                           '自然、准确、含蓄优于词藻堆砌，不要求机械添加诗眼。不得改变规则结果。'
                           '律诗可附加对仗观察，但不得声称程序已完成语义对仗判断。'}


def validate_scores(filtered, payload):
    """Return complete scores or explicit nulls; a non-null item must be complete."""
    eligible = [r['id'] for r in filtered['records'] if content_eligible(r)]
    if payload is None:
        return {key: None for key in eligible}
    _validate_envelope(payload, filtered['context'], {'filter_sha256', 'rubric_version', 'scores'})
    require(payload['filter_sha256'] == filtered['filter_sha256'], 'WRONG_FILTER', 'Scores belong to another filtered batch')
    require(payload['rubric_version'] == RUBRIC_VERSION, 'WRONG_RUBRIC', 'Unsupported scoring scale version')
    require(isinstance(payload['scores'], list), 'INVALID_SCORES', 'scores must be an array')
    evaluations = {}
    for item in payload['scores']:
        require(isinstance(item, dict) and set(item) == {'id', 'evaluation'} and _text(item['id']),
                'INVALID_SCORE_ITEM', 'Each score requires id and evaluation')
        candidate_id = item['id']
        require(candidate_id in eligible, 'INELIGIBLE_SCORE_ID', 'Only passed candidates can be evaluated: ' + candidate_id)
        require(candidate_id not in evaluations, 'DUPLICATE_SCORE_ID', 'Duplicate score ID: ' + candidate_id)
        evaluation = item['evaluation']
        if evaluation is None:
            evaluations[candidate_id] = None
            continue
        require(isinstance(evaluation, dict) and set(evaluation) in
                ({'dimensions', 'reason'}, {'dimensions', 'reason', 'parallelism_observation'}),
                'INVALID_EVALUATION', 'Evaluation requires dimensions, reason and optional parallelism_observation')
        dims = evaluation['dimensions']
        require(isinstance(dims, dict) and set(dims) == set(WEIGHTS), 'MISSING_SCORE_DIMENSION', 'All five dimensions are required')
        for name, value in dims.items():
            require(type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value),
                    'SCORE_OUT_OF_RANGE', name + ' must be a finite number in [0, 1]')
        require(_text(evaluation['reason']) and len(evaluation['reason']) <= 500,
                'INVALID_SCORE_REASON', 'A nonempty reason of at most 500 characters is required')
        if 'parallelism_observation' in evaluation:
            require(filtered['context']['couplet'] is not None and _text(evaluation['parallelism_observation'])
                    and len(evaluation['parallelism_observation']) <= 500,
                    'INVALID_PARALLELISM_OBSERVATION', 'Only regulated eight-line poems accept a brief parallelism observation')
        total = sum(Fraction(str(dims[k])) * Fraction(str(w)) for k, w in WEIGHTS.items())
        record = next(r for r in filtered['records'] if r['id'] == candidate_id)
        after = list(filtered['context']['lines'])
        index = record['line_index'] - 1
        if index < len(after):
            after[index] = record['text']
        elif index == len(after):
            after.append(record['text'])
        else:
            after.extend([''] * (index - len(after)))
            after.append(record['text'])
        adjusted, adjustment = adjust_score(float(total), filtered['context']['lines'], after)
        evaluations[candidate_id] = {**deepcopy(evaluation), 'rubric_version': RUBRIC_VERSION,
                                     'base_total': float(total), **adjustment, 'total': adjusted}
    require(set(evaluations) == set(eligible), 'MISSING_SCORE_ITEM', 'Each passed ID needs an evaluation or explicit null')
    return evaluations


def finish_round(state, filtered, scores=None, *, engine=None):
    """Recheck the saved filter and all scores before one atomic history write.

    Returns the full audit report. Persist it alongside the state (CLI bundles both).
    Filter rechecking prevents edited intermediate artifacts being trusted as rules.
    """
    verified = _verify_filter(state, filtered, engine)
    evaluations = validate_scores(verified, scores)
    records = deepcopy(verified['records'])
    for r in records:
        r['literary_evaluation'] = evaluations.get(r['id'])
        r['quality_score'] = (r['literary_evaluation']['total'] if r['literary_evaluation'] is not None else None)
    ranked = sorted((r for r in records if r['quality_score'] is not None),
                    key=lambda r: (-r['quality_score'], r['input_index']))
    counts = {key: sum(r['category'] == key for r in records)
              for key in ('passed', 'failed', 'unresolved', 'duplicate')}
    counts['scored'] = len(ranked)
    missing = [r['id'] for r in records if content_eligible(r) and r['literary_evaluation'] is None]
    blocked = [r for r in records if r['category'] == 'passed' and not content_eligible(r)]
    if ranked:
        status, code = ('partial', 'SCORES_INCOMPLETE') if missing else ('completed', 'ROUND_EVALUATED')
    elif missing:
        status, code = 'incomplete', 'SCORING_MISSING'
    elif blocked:
        status, code = 'unavailable', ('CONTENT_REQUIREMENTS_VIOLATED' if any(
            r['content_result']['valid'] is False for r in blocked) else 'CONTENT_REQUIREMENTS_UNKNOWN')
    elif counts['failed'] and not counts['unresolved']:
        status, code = 'unavailable', 'ALL_CANDIDATES_FAILED'
    elif counts['unresolved'] and not counts['failed']:
        status, code = 'unavailable', 'ALL_CANDIDATES_UNRESOLVED'
    else:
        status, code = 'unavailable', 'NO_PASSED_CANDIDATES'
    if verified['context']['locked_candidate'] is not None and counts['failed']:
        status, code = 'requires_user_adjustment', 'LOCKED_EXACT_RULE_CONFLICT'
    context = verified['context']
    parent = 'round:' + context['batch_id'] + ':' + context['generation_sha256']
    existing = state.to_dict()['candidate_history']
    require(not any(c['parent_path'] == parent for c in existing), 'BATCH_ALREADY_RECORDED', 'This round has already been recorded')
    history = []
    for r in records:
        # IDs in a model response are local to a batch; state IDs are globally namespaced.
        history_id = r['history_id']
        decision = 'retained' if r['quality_score'] is not None else (
                   'rejected' if r['category'] in ('failed', 'duplicate') or
                   r.get('content_result', {}).get('valid') is False else 'pending')
        r['decision'] = decision
        reason = {'category': r['category'], 'reasons': r['reasons'],
                  'duplicate_of': r['duplicate_of'], 'literary_evaluation': r['literary_evaluation'],
                  'missing_score': r['id'] in missing, 'adopted': False}
        if 'content_result' in r:
            reason['content_result'] = r['content_result']
        checks = deepcopy(r['checks']) if r['checks'] is not None else {}
        history.append({'id': history_id, 'text': r['text'], 'line_index': r['line_index'],
                        'parent_path': parent, 'checks': checks, 'quality_score': r['quality_score'],
                        'decision': decision, 'reason': json.dumps(reason, ensure_ascii=False, allow_nan=False)})
    report = {**_envelope(context), 'status': status, 'code': code, 'counts': counts,
              'missing_scores': missing, 'records': records, 'ranking': [r['id'] for r in ranked],
              'recommended_id': ranked[0]['id'] if ranked else None,
              'adopted_id': None, 'filter_artifact': verified, 'score_input': deepcopy(scores)}
    if verified['context']['content_plan'] is not None:
        report['content_blocked_ids'] = [r['id'] for r in blocked]
        report['content_plan'] = deepcopy(verified['context']['content_plan'])
        counts.update(content_eligible=sum(content_eligible(r) for r in records),
                      content_violated=sum(r['category'] != 'duplicate' and r['content_result']['valid'] is False for r in records),
                      content_unknown=sum(r['category'] != 'duplicate' and r['content_result']['valid'] is None for r in records))
    state.record_candidate_batch(history)
    return report


def _load(value):
    return read_json(input_path(value).read_text(encoding='utf-8-sig'))


def write_new_json(path, value):
    """Publish one complete JSON artifact atomically, with no existing-file overwrite."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.round-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        # Same-directory hard link atomically publishes complete data and fails if
        # the destination already exists on either Windows or POSIX.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'export', 'filter', 'review-reading', 'review-duplicate', 'review-content', 'finish'):
        command = commands.add_parser(name)
        command.add_argument('--work-dir')
        command.add_argument('--state', required=True)
        command.add_argument('--output', required=True)
        if name == 'export':
            command.add_argument('--line', type=int, required=True)
            command.add_argument('--content-plan')
        elif name == 'filter':
            command.add_argument('--context', required=True)
            command.add_argument('--candidates')
        elif name in ('review-reading', 'review-duplicate', 'review-content'):
            command.add_argument('--filtered', required=True)
            command.add_argument('--reviews', required=True)
        elif name == 'finish':
            command.add_argument('--filtered', required=True)
            command.add_argument('--scores')
    args = parser.parse_args()
    try:
        configure_workspace(args.work_dir)
        output = output_path(args.output)
        require(not output.exists(), 'OUTPUT_EXISTS', 'Use a new output file; snapshots are never overwritten')
        data = _load(args.state)
        state = PoemState.from_dict(data['state'] if isinstance(data, dict) and set(data) == {'state', 'report'} else data)
        if args.command == 'plan':
            result = export_content_plan(state)
        elif args.command == 'export':
            result = export_context(state, args.line, content_plan=_load(args.content_plan) if args.content_plan else None)
        elif args.command == 'filter':
            result = filter_candidates(state, _load(args.context), _load(args.candidates) if args.candidates else None)
        elif args.command == 'review-reading':
            result = review_readings(state, _load(args.filtered), _load(args.reviews))
        elif args.command == 'review-duplicate':
            result = review_duplicates(state, _load(args.filtered), _load(args.reviews))
        elif args.command == 'review-content':
            result = review_content(state, _load(args.filtered), _load(args.reviews))
        else:
            report = finish_round(state, _load(args.filtered), _load(args.scores) if args.scores else None)
            result = {'state': state.to_dict(), 'report': report}
        write_new_json(output, result)
        summary = {'output': str(output)}
        if args.command == 'finish':
            summary.update({k: report[k] for k in ('status', 'code', 'counts', 'recommended_id')})
        elif args.command == 'export':
            summary.update({k: result[k] for k in ('status', 'reasons')})
        elif args.command == 'plan':
            summary.update(status='plan_exported')
        else:
            summary.update(status=('reading_reviewed' if args.command == 'review-reading' else
                                   'duplicate_reviewed' if args.command == 'review-duplicate' else
                                   'content_reviewed' if args.command == 'review-content' else 'filtered'),
                           score_candidates=len(result['score_request']['candidates']))
        print(json.dumps(summary, ensure_ascii=False))
    except (ValueError, OSError) as exc:
        error = exc.to_dict() if isinstance(exc, (RoundError, ContentError)) else {'status': 'error', 'code': 'INVALID_INPUT', 'message': str(exc)}
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == '__main__':
    main()
