"""Content planning and fulfillment, separate from input parsing and meter rules.

Semantic verdicts are agent reports, never deterministic allusion recognition.
"""

from copy import deepcopy
import json

from assessment_binding import digest, make_binding, require_binding
from content_requirements import check_fragment_structure
from text_normalizer import normalize_line

CONTENT_VERSION = 'round-content-2'
ALLUSION_FACTS = ('persons', 'relationships', 'event', 'source', 'core_meaning')
ALLUSION_FITS = ('semantic', 'situation', 'emotion', 'identity', 'function')
ALLUSION_DIMENSIONS = {'semantic_fit': 4, 'context_fit': 4, 'coherence': 3,
                       'naturalness': 3, 'semantic_gain': 3, 'transformation': 3}
ALLUSION_PENALTIES = {'obtrusiveness': 3, 'redundancy': 3}
ALLUSION_ERRORS = {f'E{i:02d}' for i in range(1, 13)}


class ContentError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    def to_dict(self):
        return {'status': 'error', 'code': self.code, 'message': str(self)}


def need(condition, code, message):
    if not condition:
        raise ContentError(code, message)


def requirements(request):
    # IDs are unique within each array, not necessarily across the two arrays.
    return [('fragment', item) for item in request.get('required_fragments', [])] + [
        ('allusion', item) for item in request.get('allusions', [])]


def export_content_plan(state):
    """Export an editable, context-bound arrangement; never modify request/state."""
    d = state.to_dict()
    return {'content_version': CONTENT_VERSION, 'revision': d['revision'],
            'context_sha256': make_binding(d)['context_sha256'],
            'assignments': [{'kind': kind, 'requirement_id': item['id'],
                             'line_index': item.get('line_index'),
                             'reason': '用户指定句位' if item.get('line_index') is not None else ''}
                            for kind, item in requirements(d['request'])]}


def validate_content_plan(state, plan=None):
    """Validate only model arrangements; reuse input checks for placed conflicts."""
    default = export_content_plan(state)
    plan = deepcopy(default if plan is None else plan)
    need(isinstance(plan, dict) and set(plan) == set(default), 'INVALID_CONTENT_PLAN', 'Invalid content plan envelope')
    need(all(plan[k] == default[k] for k in ('content_version', 'revision', 'context_sha256')),
         'STALE_CONTENT_PLAN', 'Content plan belongs to another revision or context')
    need(type(plan['revision']) is int and isinstance(plan['assignments'], list),
         'INVALID_CONTENT_PLAN', 'Invalid revision or assignments')
    d = state.to_dict()
    known = {(kind, item['id']): item for kind, item in requirements(d['request'])}
    seen, unresolved, placed = set(), [], []
    for assignment in plan['assignments']:
        need(isinstance(assignment, dict) and set(assignment) == {'kind', 'requirement_id', 'line_index', 'reason'},
             'INVALID_ASSIGNMENT', 'Assignment requires kind, requirement_id, line_index and reason')
        kind, rid = assignment['kind'], assignment['requirement_id']
        need(isinstance(kind, str) and isinstance(rid, str), 'INVALID_ASSIGNMENT', 'Requirement key must be strings')
        key = (kind, rid)
        need(key in known and key not in seen, 'INVALID_ASSIGNMENT_ID', 'Unknown or duplicate requirement assignment')
        seen.add(key)
        item, index = known[key], assignment['line_index']
        need(index is None or type(index) is int and 1 <= index <= d['form']['line_count'],
             'INVALID_ASSIGNMENT_POSITION', 'Assigned line must be in the poem form')
        need(isinstance(assignment['reason'], str), 'INVALID_ASSIGNMENT', 'Arrangement reason must be a string')
        if index is not None:
            need(bool(assignment['reason'].strip()), 'INVALID_ASSIGNMENT', 'Explain the model arrangement separately from user evidence')
        if kind == 'fragment':
            need(item['line_index'] is None or index == item['line_index'],
                 'LOCKED_REQUIREMENT_POSITION', 'Cannot move a user-locked fragment')
            need(item['mode'] != 'inspiration' or index is None,
                 'INSPIRATION_POSITION', 'Inspiration must not lock a line')
            if item['mode'] != 'inspiration' and index is None:
                unresolved.append({'kind': kind, 'requirement_id': rid})
            placed.append({**item, 'line_index': index})
        elif item['required'] and index is None:
            unresolved.append({'kind': kind, 'requirement_id': rid})
    need(seen == set(known), 'MISSING_ASSIGNMENT', 'Every requirement needs an assignment entry (inspiration stays null)')
    # This is validation of newly chosen positions, not a second input parser.
    try:
        check_fragment_structure(placed, d['form'])
    except ValueError as exc:
        raise ContentError('CONTENT_PLAN_CONFLICT', str(exc)) from exc
    return plan, unresolved


def locked_candidate(context):
    plan = context.get('content_plan')
    if not plan:
        return None
    indices = {(x['kind'], x['requirement_id']): x['line_index'] for x in plan['assignments']}
    for item in context['required_fragments']:
        if (item['mode'] == 'exact' and item['kind'] == 'line' and
                indices['fragment', item['id']] == context['line_index']):
            return {'id': 'locked:' + item['id'], 'text': item['text'], 'line_index': context['line_index']}
    return None


def _exact(item, text, index):
    try:
        actual, expected = normalize_line(text, index), normalize_line(item['text'], index)
    except ValueError:
        return False
    if actual['segments'] != 1:
        return False
    return actual['text'] == expected['text'] if item['kind'] == 'line' else expected['text'] in actual['text']


def summarize(result):
    blocking = [x['status'] for x in result['requirements'] if x['blocking']]
    result['valid'] = False if 'violated' in blocking else None if 'unknown' in blocking else True
    return result


def check_content(state, context, candidate):
    """Compute exact matches fresh; semantic requirements start unknown/pending."""
    d = state.to_dict()
    assignments = {(x['kind'], x['requirement_id']): x for x in context['content_plan']['assignments']}
    current = candidate['line_index']
    records = []
    for kind, item in requirements(d['request']):
        assignment = assignments[kind, item['id']]
        index = assignment['line_index']
        semantic = kind == 'allusion' or item['mode'] != 'exact'
        required = item['required'] if kind == 'allusion' else item['mode'] != 'inspiration'
        due = index == current or index is None and not required
        text = candidate['text'] if index == current else (d['lines'][index - 1] if index is not None and index <= len(d['lines']) else None)
        evidence = []
        if not semantic and text is not None:
            status = 'satisfied' if _exact(item, text, index) else 'violated'
            reason = 'EXACT_MATCH' if status == 'satisfied' else 'EXACT_FRAGMENT_MISSING'
            evidence = [{'line_index': index, 'text_sha256': digest(text), 'text': text}]
        elif index is not None and index > current and text is None:
            status, reason = 'pending', 'SCHEDULED_LATER'
        else:
            status, reason = 'unknown', 'SEMANTIC_REVIEW_REQUIRED' if semantic else 'ASSIGNED_LINE_ABSENT'
        records.append({'kind': kind, 'requirement_id': item['id'], 'line_index': index,
                        'required': required, 'blocking': required and due,
                        'method': 'llm' if semantic else 'exact-normalized-1',
                        'status': status, 'reason': reason, 'evidence': evidence,
                        'semantic_review_due': semantic and due,
                        'arrangement': deepcopy(assignment),
                        'arrangement_source': 'user_position' if kind == 'fragment' and item['line_index'] is not None else 'agent_plan',
                        'interpretation': None})
    return summarize({'content_version': CONTENT_VERSION, **make_binding(d, candidate),
                      'revision': d['revision'], 'batch_id': context['batch_id'],
                      'generation_sha256': context['generation_sha256'],
                      'plan_sha256': digest(context['content_plan']), 'requirements': records})


def content_eligible(record):
    return record['category'] == 'passed' and record.get('content_result', {}).get('valid', True) is True


def _allusion_check_map(value, keys, code):
    need(isinstance(value, dict) and set(value) == set(keys), code, 'Missing allusion checks')
    result = {}
    for name, item in value.items():
        need(isinstance(item, dict) and set(item) == {'status', 'reason'} and
             item['status'] in ('pass', 'fail', 'unknown') and
             isinstance(item['reason'], str) and bool(item['reason'].strip()) and len(item['reason']) <= 1000,
             code, 'Every allusion check needs pass/fail/unknown and a reason')
        result[name] = deepcopy(item)
    return result


def _allusion_analysis(value, expected, candidate, verdict):
    """Validate two gates and quality evidence; never infer literary truth."""
    fields = {'source_basis', 'classification', 'fact_checks', 'mapping', 'fit_checks',
              'function', 'reversal_signals', 'error_codes', 'quality'}
    need(isinstance(value, dict) and set(value) == fields,
         'INVALID_ALLUSION_ANALYSIS', 'Allusion verdict requires the complete analysis object')
    reference = expected['allusion_reference']
    basis = value['source_basis']
    need(isinstance(basis, dict) and set(basis) == {'kind', 'references'} and
         basis['kind'] in ('bundled', 'external', 'unverified') and
         isinstance(basis['references'], list) and
         all(isinstance(x, str) and bool(x.strip()) and len(x) <= 1000 for x in basis['references']),
         'INVALID_ALLUSION_SOURCE', 'Declare bundled, external or unverified source basis')
    if reference['status'] == 'resolved' and verdict == 'satisfied':
        need(basis == {'kind': 'bundled', 'references': [reference['record']['record_sha256']]},
             'ALLUSION_SOURCE_UNBOUND', 'Resolved allusion must cite its bundled record digest')
    if reference['status'] == 'not_found' and verdict == 'satisfied':
        need(basis['kind'] == 'external' and bool(basis['references']),
             'ALLUSION_SOURCE_REQUIRED', 'A local miss needs explicit reliable external references')
    need(reference['status'] != 'ambiguous' or verdict != 'satisfied',
         'ALLUSION_AMBIGUOUS', 'Ambiguous allusion must be resolved before it can be satisfied')

    classification = value['classification']
    need(isinstance(classification, dict) and set(classification) ==
         {'source_type', 'visibility', 'semantic_relation', 'transformation'} and
         classification['source_type'] in ('event', 'phrase', 'unknown') and
         classification['visibility'] in ('explicit', 'implicit', 'unknown') and
         classification['semantic_relation'] in ('direct', 'inverse', 'borrowed', 'adaptive', 'unknown') and
         classification['transformation'] in ('quotation', 'rewrite', 'allusive_transform', 'unknown'),
         'INVALID_ALLUSION_CLASSIFICATION', 'Invalid independent allusion classification dimensions')
    facts = _allusion_check_map(value['fact_checks'], ALLUSION_FACTS, 'INVALID_ALLUSION_FACTS')
    fits = _allusion_check_map(value['fit_checks'], ALLUSION_FITS, 'INVALID_ALLUSION_FIT')

    need(isinstance(value['function'], str) and bool(value['function'].strip()) and len(value['function']) <= 1000,
         'INVALID_ALLUSION_FUNCTION', 'Explain what the allusion adds to this poem')
    mapping = value['mapping']
    need(isinstance(mapping, list), 'INVALID_ALLUSION_MAPPING', 'mapping must be an array')
    for item in mapping:
        need(isinstance(item, dict) and set(item) == {'source_element', 'current_element', 'quote'} and
             all(isinstance(item[k], str) and bool(item[k].strip()) and len(item[k]) <= 1000
                 for k in ('source_element', 'current_element', 'quote')) and
             item['quote'] in candidate['text'], 'INVALID_ALLUSION_MAPPING',
             'Mapping must connect a source element to the current scene and quote this candidate')
    signals = value['reversal_signals']
    need(isinstance(signals, list), 'INVALID_ALLUSION_REVERSAL', 'reversal_signals must be an array')
    for item in signals:
        need(isinstance(item, dict) and set(item) == {'quote', 'explanation'} and
             isinstance(item['quote'], str) and bool(item['quote'].strip()) and item['quote'] in candidate['text'] and
             isinstance(item['explanation'], str) and bool(item['explanation'].strip()) and len(item['explanation']) <= 1000,
             'INVALID_ALLUSION_REVERSAL', 'Inverse use needs a quoted reversal or contrast signal')
    need(classification['semantic_relation'] != 'inverse' or bool(signals),
         'ALLUSION_REVERSAL_UNPROVEN', 'Inverse use needs explicit textual reversal evidence')
    need(classification['semantic_relation'] == 'inverse' or not signals,
         'INVALID_ALLUSION_REVERSAL', 'Reversal signals only belong to inverse use')

    errors = value['error_codes']
    need(isinstance(errors, list) and len(errors) == len(set(errors)) and
         all(isinstance(x, str) and x in ALLUSION_ERRORS for x in errors),
         'INVALID_ALLUSION_ERRORS', 'Use unique allusion error codes E01-E12')
    failed_facts = {k for k, v in facts.items() if v['status'] == 'fail'}
    failed_fits = {k for k, v in fits.items() if v['status'] == 'fail'}
    need(not (failed_facts & {'persons', 'relationships', 'event', 'source'}) or 'E01' in errors,
         'ALLUSION_ERROR_CODE_MISSING', 'Fact failures require E01')
    need(facts['core_meaning']['status'] != 'fail' or 'E02' in errors,
         'ALLUSION_ERROR_CODE_MISSING', 'Core-meaning failure requires E02')
    need(not (failed_fits & {'semantic', 'situation'}) or 'E03' in errors,
         'ALLUSION_ERROR_CODE_MISSING', 'Semantic or situation mismatch requires E03')

    quality = value['quality']
    need(isinstance(quality, dict) and set(quality) == {'dimensions', 'penalties', 'total'},
         'INVALID_ALLUSION_QUALITY', 'Invalid allusion quality object')
    dimensions, penalties = quality['dimensions'], quality['penalties']
    need(isinstance(dimensions, dict) and set(dimensions) == set(ALLUSION_DIMENSIONS) and
         all(type(v) is int and 0 <= v <= ALLUSION_DIMENSIONS[k] for k, v in dimensions.items()) and
         isinstance(penalties, dict) and set(penalties) == set(ALLUSION_PENALTIES) and
         all(type(v) is int and 0 <= v <= ALLUSION_PENALTIES[k] for k, v in penalties.items()),
         'INVALID_ALLUSION_QUALITY', 'Allusion scores must be integers within their declared ranges')
    total = sum(dimensions.values()) - sum(penalties.values())
    need(type(quality['total']) is int and quality['total'] == total,
         'INVALID_ALLUSION_QUALITY', 'Allusion quality total must equal dimensions minus penalties')

    if verdict == 'satisfied':
        need(all(value != 'unknown' for value in classification.values()),
             'ALLUSION_CLASSIFICATION_UNKNOWN', 'Satisfied allusion needs complete independent classifications')
        need(bool(mapping), 'ALLUSION_MAPPING_REQUIRED', 'Satisfied allusion needs a source-to-scene mapping')
        need(all(x['status'] == 'pass' for x in facts.values()),
             'ALLUSION_FACTS_FAILED_OR_UNKNOWN', 'Satisfied allusion requires all fact checks to pass')
        need(all(x['status'] == 'pass' for x in fits.values()),
             'ALLUSION_FIT_FAILED_OR_UNKNOWN', 'Satisfied allusion requires all fit checks to pass')
        need(not errors, 'ALLUSION_QUALITY_ERROR', 'A detected allusion error cannot be satisfied')
        need(all(dimensions[name] > 0 for name in ('semantic_fit', 'context_fit', 'semantic_gain')),
             'ALLUSION_GAIN_REQUIRED', 'Satisfied allusion needs semantic, contextual and gain value')
    return deepcopy(value)


def semantic_request(filtered):
    candidates = []
    context = filtered['context']
    known = {(kind, item['id']): item for kind, item in requirements(context['request'])}
    references = {item['requirement_id']: item for item in context.get('allusion_references', [])}
    for record in filtered['records']:
        result = record.get('content_result')
        if record['category'] != 'passed' or result is None or result['valid'] is False:
            continue
        active = [x for x in result['requirements'] if x['semantic_review_due']]
        if active:
            candidates.append({'id': record['id'], 'line_index': record['line_index'],
                               'text': record['text'], 'text_sha256': digest(record['text']),
                               'requirements': [{'kind': x['kind'], 'requirement_id': x['requirement_id'],
                                                 'requirement': deepcopy(known[x['kind'], x['requirement_id']]),
                                                 **({'allusion_reference': deepcopy(references[x['requirement_id']])}
                                                    if x['kind'] == 'allusion' else {})}
                                                for x in active]})
    return {'content_version': CONTENT_VERSION, 'filter_sha256': filtered['filter_sha256'],
            **{k: context[k] for k in ('contract_version', 'batch_id', 'context_sha256', 'generation_sha256')},
            'context': deepcopy(context), 'candidates': candidates,
            'instruction': '逐项判断 satisfied/violated/unknown，说明候选具体表达如何体现典故或保留主要意思。'
                           '出现典故名称不等于成功用典；resolved 典故须按规范主典、简释与出处判断；'
                           'ambiguous 先消歧，不能判 satisfied；not_found 仅表示局部语料未收录，'
                           '须另行可靠核实，否则保留 unknown，不编造出处。'
                           'adapt 不要求原字相同；inspiration 不要求出现原文；可选典故不阻断。'
                           '用典必须先完成事实门与诗境映射门，再记录分类、功能、E01-E12、质量维度和惩罚项；'
                           '反用必须引用明确反转信号，总分不能覆盖硬错误。'
                           'satisfied 须引用候选原文并给出具体解释。照抄候选文本摘要、句位和要求 ID。'}


def apply_semantic_reviews(filtered, payload):
    """Validate report ownership/evidence, not the truth of semantic judgments."""
    request = filtered['content_request']
    known = {c['id']: c for c in request['candidates']}
    need(isinstance(payload['reviews'], list), 'INVALID_CONTENT_REVIEWS', 'reviews must be an array')
    records = deepcopy(filtered['records'])
    by_id = {r['id']: r for r in records}
    seen = set()
    for review in payload['reviews']:
        need(isinstance(review, dict) and set(review) == {'id', 'text_sha256', 'line_index', 'requirements'},
             'INVALID_CONTENT_REVIEW', 'Review requires candidate identity and requirements')
        cid = review['id']
        need(isinstance(cid, str) and cid in known and cid not in seen,
             'INVALID_CONTENT_CANDIDATE', 'Unknown, duplicate or ineligible content candidate')
        seen.add(cid)
        candidate = known[cid]
        need(type(review['line_index']) is int and all(review[k] == candidate[k] for k in ('line_index', 'text_sha256')),
             'CONTENT_SUBJECT_MISMATCH', 'Content review belongs to another candidate text or position')
        expected = {(x['kind'], x['requirement_id']) for x in candidate['requirements']}
        need(isinstance(review['requirements'], list), 'INVALID_CONTENT_REVIEW', 'requirements must be an array')
        covered = set()
        target = {(x['kind'], x['requirement_id']): x for x in by_id[cid]['content_result']['requirements']}
        for item in review['requirements']:
            base_fields = {'kind', 'requirement_id', 'status', 'reason', 'interpretation', 'evidence'}
            kind = item.get('kind') if isinstance(item, dict) else None
            expected_fields = base_fields | ({'allusion_analysis'} if kind == 'allusion' else set())
            need(isinstance(item, dict) and set(item) == expected_fields,
                 'INVALID_CONTENT_VERDICT', 'Invalid semantic verdict fields')
            need(isinstance(item['kind'], str) and isinstance(item['requirement_id'], str),
                 'INVALID_CONTENT_VERDICT', 'Requirement identity must be strings')
            key = item['kind'], item['requirement_id']
            need(key in expected and key not in covered, 'INVALID_CONTENT_REQUIREMENT', 'Unknown, duplicate or non-due requirement')
            covered.add(key)
            need(item['status'] in ('satisfied', 'violated', 'unknown'), 'INVALID_CONTENT_STATUS', 'Semantic status must be satisfied, violated or unknown')
            expected_item = next(x for x in candidate['requirements']
                                 if (x['kind'], x['requirement_id']) == key)
            if key[0] == 'allusion':
                reference = expected_item['allusion_reference']
                need(reference['status'] != 'ambiguous' or item['status'] != 'satisfied',
                     'ALLUSION_AMBIGUOUS', 'Ambiguous allusion must be resolved before it can be satisfied')
                analysis = _allusion_analysis(item['allusion_analysis'], expected_item, candidate, item['status'])
            for field in ('reason', 'interpretation'):
                need(isinstance(item[field], str) and len(item[field]) <= 1000,
                     'INVALID_CONTENT_EXPLANATION', 'Explanation must be a short string')
            need(bool(item['reason'].strip()), 'INVALID_CONTENT_EXPLANATION', 'Explain the semantic verdict')
            evidence = item['evidence']
            need(isinstance(evidence, list), 'INVALID_CONTENT_EVIDENCE', 'evidence must be an array')
            for entry in evidence:
                need(isinstance(entry, dict) and set(entry) == {'line_index', 'quote'} and
                     type(entry['line_index']) is int and entry['line_index'] == candidate['line_index'] and
                     isinstance(entry['quote'], str) and bool(entry['quote'].strip()) and entry['quote'] in candidate['text'],
                     'INVALID_CONTENT_EVIDENCE', 'Evidence must quote this candidate verbatim at its line')
            if item['status'] == 'satisfied':
                need(bool(evidence) and bool(item['interpretation'].strip()), 'CONTENT_EVIDENCE_REQUIRED',
                     'Satisfaction needs quoted expression and its semantic relationship to the requirement')
            target[key].update({k: deepcopy(item[k]) for k in ('status', 'reason', 'interpretation', 'evidence')})
            if key[0] == 'allusion':
                target[key]['allusion_analysis'] = analysis
        need(covered == expected, 'MISSING_CONTENT_REQUIREMENT', 'Every due semantic requirement needs a verdict, unknown is allowed')
        summarize(by_id[cid]['content_result'])
    need(seen == set(known), 'MISSING_CONTENT_REVIEW', 'Every requested candidate needs a semantic review')
    return records


def read_content_fulfillment(state, candidate_id):
    """Read only current-context history; historical satisfaction is not reusable."""
    d = state.to_dict()
    candidate = next((c for c in d['candidate_history'] if c['id'] == candidate_id), None)
    need(candidate is not None, 'CONTENT_CANDIDATE_ABSENT', 'Candidate history not found')
    try:
        result = json.loads(candidate['reason']).get('content_result')
    except (ValueError, AttributeError):
        result = None
    need(isinstance(result, dict), 'CONTENT_RESULT_ABSENT', 'No content fulfillment recorded')
    need(result.get('revision') == d['revision'], 'STALE_CONTENT_RESULT', 'Creative context changed; reevaluate content')
    try:
        require_binding(result, d, candidate)
    except ValueError as exc:
        raise ContentError('STALE_CONTENT_RESULT', str(exc)) from exc
    return deepcopy(result)
