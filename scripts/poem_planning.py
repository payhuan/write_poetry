"""Versioned semantic poem plans bound to an immutable planning basis."""

from copy import deepcopy
import json

from assessment_binding import digest


PLAN_VERSION = 'poem-plan-1'
RELATIONS = {'parallel', 'contrast', 'far_near', 'motion_stillness', 'past_present',
             'reality_imagination', 'cause_effect', 'two_perspectives'}


class PlanError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def need(condition, code, message):
    if not condition:
        raise PlanError(code, message)


def text(value, *, empty=False):
    return isinstance(value, str) and (empty or bool(value.strip()))


def strings(value, *, allow_empty=False):
    return isinstance(value, list) and (allow_empty or bool(value)) and all(text(x) for x in value)


def planning_basis(state):
    d = state.to_dict()
    stable = {key: deepcopy(d[key]) for key in
              ('request', 'poem_type', 'form', 'template_id', 'meter_template', 'rhyme',
               'constraints', 'theme', 'emotion')}
    stable['base_lines'] = deepcopy(d['lines'])
    return {'binding_version': 1, 'base_revision': d['revision'],
            'context_sha256': digest(stable), 'base_lines_sha256': digest(d['lines'])}


def expected_units(form):
    if form['line_count'] == 4:
        return [{'unit_id': f'line-{i}', 'unit': f'第{i}句', 'line_indices': [i]}
                for i in range(1, 5)]
    names = ('首联', '颔联', '颈联', '尾联')
    return [{'unit_id': f'couplet-{i}', 'unit': name,
             'line_indices': [i * 2 - 1, i * 2]}
            for i, name in enumerate(names, 1)]


def export_plan_request(state):
    d = state.to_dict()
    need('meter_template' in d and d['rhyme']['group'] is not None,
         'PLAN_PREREQUISITES', 'Bind the meter template and rhyme group before semantic planning')
    return {'plan_version': PLAN_VERSION, 'binding': planning_basis(state),
            'request': deepcopy(d['request']), 'theme': d['theme'], 'emotion': deepcopy(d['emotion']),
            'poem_type': d['poem_type'], 'form': deepcopy(d['form']),
            'rhyme': deepcopy(d['rhyme']), 'base_lines': deepcopy(d['lines']),
            'expected_units': expected_units(d['form']),
            'instruction': ('只规划全诗语义蓝图，不写完整诗句。区分用户事实与合理推断；'
                            '每个单位说明新增信息、保护语义和暂不应提前表达的内容。'
                            '律诗颔联、颈联须规划语义关系、语义轴、句法策略与避免合掌。')}


def _validate_checklist(value, names, code):
    need(isinstance(value, dict) and set(value) == set(names), code, 'Invalid planning checklist')
    for name, item in value.items():
        need(isinstance(item, dict) and set(item) == {'status', 'reason'} and
             item['status'] in ('pass', 'fail') and text(item['reason']), code,
             'Every planning check needs pass/fail and a reason')
    need(all(x['status'] == 'pass' for x in value.values()), code,
         'Planning self-check must pass before generation')


def validate_plan(state, payload):
    """Validate plan structure and binding, not the truth of literary judgments."""
    required = {'plan_version', 'binding', 'provenance', 'poem_core',
                'spatiotemporal_structure', 'emotion_path', 'core_images',
                'structure_plan', 'allusion_plan', 'implicit_content', 'risk_list',
                'prosody_boundary', 'self_check'}
    need(isinstance(payload, dict) and set(payload) == required,
         'INVALID_POEM_PLAN', 'Unexpected or missing poem-plan fields')
    need(payload['plan_version'] == PLAN_VERSION, 'WRONG_PLAN_VERSION', 'Unsupported poem plan version')
    need(payload['binding'] == planning_basis(state), 'STALE_POEM_PLAN', 'Plan belongs to another planning basis')
    d = state.to_dict()

    provenance = payload['provenance']
    need(isinstance(provenance, list), 'INVALID_PLAN_PROVENANCE', 'provenance must be an array')
    seen = set()
    for item in provenance:
        need(isinstance(item, dict) and set(item) == {'field', 'value', 'source', 'evidence'} and
             text(item['field']) and item['field'] not in seen and text(item['value']) and
             item['source'] in ('user', 'inferred') and isinstance(item['evidence'], str),
             'INVALID_PLAN_PROVENANCE', 'Invalid or duplicate provenance record')
        seen.add(item['field'])
        if item['source'] == 'user':
            need(bool(item['evidence'].strip()) and item['evidence'] in d['request']['raw_request'],
                 'INVALID_PLAN_PROVENANCE', 'User facts need verbatim request evidence')

    core = payload['poem_core']
    core_fields = {'topic', 'core_proposition', 'speaker', 'situation', 'trigger',
                   'primary_emotion', 'inner_tension', 'ending_state', 'register'}
    need(isinstance(core, dict) and set(core) == core_fields and
         all(text(core[k]) for k in core_fields - {'inner_tension'}) and
         text(core['inner_tension'], empty=True), 'INVALID_POEM_CORE', 'Invalid poem core')
    space = payload['spatiotemporal_structure']
    need(isinstance(space, dict) and set(space) ==
         {'time_path', 'space_path', 'viewpoint', 'reality_imagination_relation'} and
         strings(space['time_path']) and strings(space['space_path']) and
         text(space['viewpoint']) and text(space['reality_imagination_relation']),
         'INVALID_SPATIOTEMPORAL_PLAN', 'Invalid time, space or viewpoint plan')
    emotions = payload['emotion_path']
    need(isinstance(emotions, list) and bool(emotions), 'INVALID_EMOTION_PATH', 'emotion_path must be nonempty')
    for index, item in enumerate(emotions, 1):
        need(isinstance(item, dict) and set(item) == {'stage', 'state', 'trigger'} and
             item['stage'] == index and text(item['state']) and text(item['trigger']),
             'INVALID_EMOTION_PATH', 'Emotion stages must be contiguous and explained')

    expected = expected_units(d['form'])
    units = payload['structure_plan']
    need(isinstance(units, list) and len(units) == len(expected),
         'INVALID_STRUCTURE_PLAN', 'Plan must cover every structural unit exactly once')
    unit_ids = set()
    for item, identity in zip(units, expected):
        fields = {'unit_id', 'unit', 'line_indices', 'primary_task', 'content_scope',
                  'semantic_increment', 'relation_to_previous', 'relation_to_next',
                  'protected_meaning', 'must_not_say_yet', 'antithesis'}
        need(isinstance(item, dict) and set(item) == fields and
             all(item[k] == identity[k] for k in ('unit_id', 'unit', 'line_indices')) and
             item['unit_id'] not in unit_ids and all(text(item[k]) for k in
             ('primary_task', 'content_scope', 'semantic_increment',
              'relation_to_previous', 'relation_to_next')) and
             strings(item['protected_meaning']) and strings(item['must_not_say_yet'], allow_empty=True),
             'INVALID_STRUCTURE_PLAN', 'Invalid structural unit plan')
        unit_ids.add(item['unit_id'])
        middle = d['form']['line_count'] == 8 and item['line_indices'] in ([3, 4], [5, 6])
        antithesis = item['antithesis']
        if middle:
            need(isinstance(antithesis, dict) and set(antithesis) ==
                 {'relation', 'semantic_axes', 'syntactic_strategy', 'avoid_hangzhang'} and
                 antithesis['relation'] in RELATIONS and strings(antithesis['semantic_axes']) and
                 text(antithesis['syntactic_strategy']) and text(antithesis['avoid_hangzhang']),
                 'ANTITHESIS_PLAN_REQUIRED', 'Middle couplets need an antithesis plan')
        else:
            need(antithesis is None, 'INVALID_ANTITHESIS_PLAN', 'Only middle couplets use antithesis planning')

    images = payload['core_images']
    need(isinstance(images, list), 'INVALID_IMAGE_PLAN', 'core_images must be an array')
    names = set()
    for item in images:
        need(isinstance(item, dict) and set(item) ==
             {'image', 'source_in_scene', 'first_unit', 'surface_function', 'deeper_function', 'development'} and
             text(item['image']) and item['image'] not in names and text(item['source_in_scene']) and
             item['first_unit'] in unit_ids and text(item['surface_function']) and
             text(item['deeper_function']) and text(item['development']),
             'INVALID_IMAGE_PLAN', 'Invalid or duplicate core image')
        names.add(item['image'])

    allusion = payload['allusion_plan']
    need(isinstance(allusion, dict) and set(allusion) == {'requirement', 'function', 'required_semantics', 'items'} and
         allusion['requirement'] in ('none', 'optional', 'required') and text(allusion['function']) and
         strings(allusion['required_semantics'], allow_empty=True) and isinstance(allusion['items'], list),
         'INVALID_ALLUSION_PLAN', 'Invalid allusion plan')
    expected_allusions = {x['id']: x for x in d['request'].get('allusions', [])}
    covered = set()
    for item in allusion['items']:
        need(isinstance(item, dict) and set(item) ==
             {'requirement_id', 'unit_id', 'function', 'required_semantics', 'candidate_direction'} and
             item['requirement_id'] in expected_allusions and item['requirement_id'] not in covered and
             item['unit_id'] in unit_ids and text(item['function']) and
             strings(item['required_semantics']) and strings(item['candidate_direction']),
             'INVALID_ALLUSION_PLAN', 'Invalid allusion assignment')
        covered.add(item['requirement_id'])
    need(covered == set(expected_allusions), 'INVALID_ALLUSION_PLAN', 'Every requested allusion needs a plan item')
    if any(x['required'] for x in expected_allusions.values()):
        need(allusion['requirement'] == 'required', 'INVALID_ALLUSION_PLAN', 'Required user allusions stay required')

    need(strings(payload['implicit_content'], allow_empty=True) and
         isinstance(payload['risk_list'], list), 'INVALID_POEM_PLAN', 'Invalid implicit content or risk list')
    for item in payload['risk_list']:
        need(isinstance(item, dict) and set(item) == {'risk', 'prevention'} and
             text(item['risk']) and text(item['prevention']), 'INVALID_RISK_PLAN', 'Invalid planning risk')
    prosody = payload['prosody_boundary']
    need(isinstance(prosody, dict) and set(prosody) ==
         {'form', 'rhyme_requirement', 'antithesis_positions'} and
         prosody['form'] == d['poem_type'] and text(prosody['rhyme_requirement']) and
         prosody['antithesis_positions'] == d['form']['antithesis_pairs'],
         'INVALID_PROSODY_BOUNDARY', 'Prosody boundary conflicts with selected form')
    _validate_checklist(payload['self_check'],
                        ('independent_units', 'semantic_progression', 'grounded_images',
                         'viewpoint_continuity', 'ending_prepared', 'allusion_necessary'),
                        'PLAN_SELF_CHECK_FAILED')
    return deepcopy(payload)


def make_plan_artifact(state, payload):
    plan = validate_plan(state, payload)
    return {'plan': plan, 'sha256': digest(plan)}


def load_plan_artifact(state, artifact):
    need(isinstance(artifact, dict) and set(artifact) == {'plan', 'sha256'} and
         artifact['sha256'] == digest(artifact['plan']), 'INVALID_PLAN_ARTIFACT', 'Corrupt poem plan artifact')
    return validate_plan(state, artifact['plan'])


def unit_for_line(plan, line_index):
    return next((deepcopy(x) for x in plan['structure_plan'] if line_index in x['line_indices']), None)
