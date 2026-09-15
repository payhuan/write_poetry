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

from assessment_binding import digest, make_binding
from constraint_engine import ConstraintEngine
from poem_state import PoemState
from text_normalizer import IGNORED
from round_content import (CONTENT_VERSION, ContentError, export_content_plan,
                           validate_content_plan, locked_candidate, check_content,
                           content_eligible, semantic_request, apply_semantic_reviews,
                           read_content_fulfillment)

PROJECT = Path(__file__).resolve().parent.parent
CONTRACT_VERSION = 'poetry-round-1'
RUBRIC_VERSION = 'literary-1'
WEIGHTS = {'theme': 0.25, 'naturalness': 0.25, 'imagery': 0.20,
           'continuity': 0.15, 'expression': 0.15}
MAX_FORMAT_RETRIES = 2
ENVELOPE = {'contract_version', 'batch_id', 'context_sha256', 'generation_sha256'}


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


def _context(state, line_index, batch_id, content_plan=None):
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
        'allusions': allusions, 'required_fragments': fragments, 'content_plan': plan,
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
        'generation_instruction': '生成语义完整的候选句，保留句意与自然措辞；不得逐字填空、自动改句或猜选有利读音。',
    }
    result['locked_candidate'] = locked_candidate(result)
    if result['locked_candidate'] is not None:
        result['candidate_count'] = {'min': 1, 'max': 1, 'target': 1}
        result['generation_instruction'] = '本句锁定 exact 完整句，跳过模型生成，直接调用 filter 送检原文，不改写。'
    result['generation_sha256'] = digest(result)
    return result


def export_context(state, line_index, *, content_plan=None):
    """Export a stable batch token and all model context, without changing state."""
    return _context(state, line_index, uuid4().hex, content_plan)


def _validate_context(state, context):
    require(isinstance(context, dict), 'INVALID_CONTEXT', 'Context must be an object')
    batch_id = context.get('batch_id')
    require(isinstance(batch_id, str) and len(batch_id) == 32 and
            all(c in '0123456789abcdef' for c in batch_id), 'INVALID_BATCH', 'Invalid exported batch ID')
    expected = _context(state, context.get('line_index'), batch_id, context.get('content_plan'))
    require(context == expected, 'STALE_CONTEXT', 'Context, revision or budget changed; export a new batch')
    require(context['status'] == 'ready', 'PLANNING_REQUIRED', 'Complete planning before generating this line')


def _envelope(context):
    return {k: context[k] for k in sorted(ENVELOPE)}


def _validate_envelope(payload, context, extra):
    finite_json(payload)
    require(isinstance(payload, dict) and set(payload) == ENVELOPE | extra | {'attempt'},
            'INVALID_ENVELOPE', 'Unexpected or missing model response fields')
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
    for index, candidate in enumerate(items):
        code = None
        if not isinstance(candidate, dict) or set(candidate) != {'id', 'text', 'line_index'}:
            code = 'INVALID_CANDIDATE_FIELDS'
        elif not _text(candidate['id']):
            code = 'INVALID_CANDIDATE_ID'
        elif candidate['id'] in ids:
            code = 'DUPLICATE_CANDIDATE_ID'
        elif type(candidate['line_index']) is not int or candidate['line_index'] != context['line_index']:
            code = 'WRONG_LINE_INDEX'
        elif not _text(candidate['text']) or not perimeter_text(candidate['text']):
            code = 'EMPTY_CANDIDATE_TEXT'
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


def filter_candidates(state, context, payload=None, *, engine=None):
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
            checks = engine.check_candidate({**candidate, 'id': record['history_id']}, snapshot)
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
    result = {'context': deepcopy(context), 'candidate_input': deepcopy(payload), 'records': records}
    if context['content_plan'] is not None:
        result['content_input'] = None
    _seal_filter(result)
    if context['content_plan'] is not None:
        result['content_request'] = semantic_request(result)
    return result


def _seal_filter(result):
    result['filter_sha256'] = digest({k: v for k, v in result.items()
                                      if k not in ('filter_sha256', 'score_request', 'content_request')})
    result['score_request'] = _score_request(result)


def _apply_content(filtered, payload):
    require('content_request' in filtered, 'NO_CONTENT_REQUIREMENTS', 'This batch has no content requirements')
    _validate_envelope(payload, filtered['context'], {'filter_sha256', 'content_version', 'reviews'})
    require(payload['filter_sha256'] == filtered['filter_sha256'], 'WRONG_FILTER', 'Content review belongs to another filtered batch')
    require(payload['content_version'] == CONTENT_VERSION, 'WRONG_CONTENT_VERSION', 'Unsupported content review version')
    result = deepcopy(filtered)
    result['records'] = apply_semantic_reviews(filtered, payload)
    result['content_input'] = deepcopy(payload)
    _seal_filter(result)
    return result


def _verify_filter(state, filtered, engine):
    finite_json(filtered)
    base = {'context', 'candidate_input', 'records', 'filter_sha256', 'score_request'}
    require(isinstance(filtered, dict) and set(filtered) in (base, base | {'content_input', 'content_request'}),
            'INVALID_FILTER', 'Expected a complete program-generated filter artifact')
    verified = filter_candidates(state, filtered['context'], filtered['candidate_input'], engine=engine)
    if filtered.get('content_input') is not None:
        verified = _apply_content(verified, filtered['content_input'])
    require(verified == filtered, 'FILTER_MISMATCH', 'Saved filter changed or rule data differs; filter again')
    return verified


def review_content(state, filtered, payload, *, engine=None):
    """Import semantic fulfillment before literary scoring, without writing state."""
    verified = _verify_filter(state, filtered, engine)
    require(verified.get('content_input') is None, 'CONTENT_ALREADY_REVIEWED', 'Review the original filter artifact when correcting a review')
    return _apply_content(verified, payload)


def _score_request(filtered):
    context = filtered['context']
    return {**_envelope(context), 'filter_sha256': filtered['filter_sha256'],
            'rubric_version': RUBRIC_VERSION, 'weights': deepcopy(WEIGHTS),
            'scale': {'0': '不符合该维度', '0.25': '明显薄弱', '0.5': '基本成立但有明显不足',
                      '0.75': '表现良好，只有局部不足', '1': '在当前上下文中充分且准确'},
            'context': deepcopy(context),
            'candidates': [{**{k: record[k] for k in ('id', 'text', 'line_index')},
                            **({'content_fulfillment': deepcopy(record['content_result'])}
                               if 'content_result' in record else {})}
                           for record in filtered['records'] if content_eligible(record)],
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
        evaluations[candidate_id] = {**deepcopy(evaluation), 'rubric_version': RUBRIC_VERSION,
                                     'total': float(total)}
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


def project_path(value):
    """Require CLI inputs/outputs to live in the relocatable project directory."""
    path = Path(value)
    path = (path if path.is_absolute() else PROJECT / path).resolve()
    if not path.is_relative_to(PROJECT):
        raise RoundError('PATH_OUTSIDE_PROJECT', 'Keep round input/output files inside this project')
    return path


def _load(value):
    return read_json(project_path(value).read_text(encoding='utf-8-sig'))


def write_new_json(path, value):
    """Publish one complete JSON artifact atomically, with no existing-file overwrite."""
    path = project_path(path)
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
    for name in ('plan', 'export', 'filter', 'review-content', 'finish'):
        command = commands.add_parser(name)
        command.add_argument('--state', required=True)
        command.add_argument('--output', required=True)
        if name == 'export':
            command.add_argument('--line', type=int, required=True)
            command.add_argument('--content-plan')
        elif name == 'filter':
            command.add_argument('--context', required=True)
            command.add_argument('--candidates')
        elif name == 'review-content':
            command.add_argument('--filtered', required=True)
            command.add_argument('--reviews', required=True)
        elif name == 'finish':
            command.add_argument('--filtered', required=True)
            command.add_argument('--scores')
    args = parser.parse_args()
    try:
        output = project_path(args.output)
        require(not output.exists(), 'OUTPUT_EXISTS', 'Use a new output file; snapshots are never overwritten')
        data = _load(args.state)
        state = PoemState.from_dict(data['state'] if isinstance(data, dict) and set(data) == {'state', 'report'} else data)
        if args.command == 'plan':
            result = export_content_plan(state)
        elif args.command == 'export':
            result = export_context(state, args.line, content_plan=_load(args.content_plan) if args.content_plan else None)
        elif args.command == 'filter':
            result = filter_candidates(state, _load(args.context), _load(args.candidates) if args.candidates else None)
        elif args.command == 'review-content':
            result = review_content(state, _load(args.filtered), _load(args.reviews))
        else:
            report = finish_round(state, _load(args.filtered), _load(args.scores) if args.scores else None)
            result = {'state': state.to_dict(), 'report': report}
        write_new_json(output, result)
        summary = {'output': output.relative_to(PROJECT).as_posix()}
        if args.command == 'finish':
            summary.update({k: report[k] for k in ('status', 'code', 'counts', 'recommended_id')})
        elif args.command == 'export':
            summary.update({k: result[k] for k in ('status', 'reasons')})
        elif args.command == 'plan':
            summary.update(status='plan_exported')
        else:
            summary.update(status='content_reviewed' if args.command == 'review-content' else 'filtered',
                           score_candidates=len(result['score_request']['candidates']))
        print(json.dumps(summary, ensure_ascii=False))
    except (ValueError, OSError) as exc:
        error = exc.to_dict() if isinstance(exc, (RoundError, ContentError)) else {'status': 'error', 'code': 'INVALID_INPUT', 'message': str(exc)}
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == '__main__':
    main()
