"""Versioned semantic poem plans bound to an immutable planning basis."""

from copy import deepcopy
import json

from assessment_binding import digest


PLAN_VERSION = 'poem-plan-2'
RELATIONS = {'parallel', 'contrast', 'far_near', 'motion_stillness', 'past_present',
             'reality_imagination', 'cause_effect', 'two_perspectives'}
TURN_AXES = {'action_state', 'emotion_stance', 'cognition', 'meaning',
             'spatiotemporal', 'imagery_relation', 'other'}


class PlanError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or []

    def to_dict(self):
        return {'status': 'error', 'code': self.code, 'message': str(self),
                'details': self.details}


def field_error(code, location, message, **details):
    raise PlanError(code, f'{location}: {message}', [{'location': location, **details}])


def object_fields(value, fields, location, code):
    if not isinstance(value, dict):
        field_error(code, location, 'Expected an object', expected_type='object')
    missing, unexpected = sorted(fields - value.keys()), sorted(value.keys() - fields)
    if missing or unexpected:
        field_error(code, location, 'Unexpected or missing fields',
                    missing_fields=missing, unexpected_fields=unexpected)


def allowed_value(value, allowed, location, code):
    if value not in tuple(allowed):
        choices = sorted(allowed, key=str)
        field_error(code, location, f'received {value!r}; allowed: {", ".join(map(str, choices))}',
                    received=value, allowed_values=choices)


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
    missing = (['meter_template'] if 'meter_template' not in d else []) + (
        ['rhyme.group'] if d['rhyme']['group'] is None else [])
    if missing:
        field_error('PLAN_PREREQUISITES', 'state', 'Bind meter and rhyme before semantic planning',
                    missing_fields=missing)
    return {'plan_version': PLAN_VERSION, 'binding': planning_basis(state),
            'request': deepcopy(d['request']), 'theme': d['theme'], 'emotion': deepcopy(d['emotion']),
            'poem_type': d['poem_type'], 'form': deepcopy(d['form']),
            'rhyme': deepcopy(d['rhyme']), 'base_lines': deepcopy(d['lines']),
            'expected_units': expected_units(d['form']),
            'instruction': ('只规划全诗语义蓝图，不写完整诗句。区分用户事实与合理推断；'
                            '每个单位说明新增信息、保护语义和暂不应提前表达的内容。'
                            '律诗四联依次承担起、承、转、合，并说明各自如何服务核心命题；颔联、颈联须规划语义关系、语义轴、句法策略与避免合掌。'
                            '颈联须规划真实方向变化及其铺垫和收束作用；变化可以是行动、立场、情绪、认知或意义重释，不要求换景换意象。')}


def _validate_checklist(value, names, code):
    object_fields(value, set(names), 'self_check', code)
    for name, item in value.items():
        location = f'self_check.{name}'
        object_fields(item, {'status', 'reason'}, location, code)
        allowed_value(item['status'], ('pass', 'fail'), location + '.status', code)
        if not text(item['reason']):
            field_error(code, location + '.reason', 'Expected a nonempty explanation')
    failed = [name for name, item in value.items() if item['status'] == 'fail']
    if failed:
        raise PlanError(code, 'Planning self-check failed: ' + ', '.join(failed),
                        [{'location': f'self_check.{name}.status', 'received': 'fail',
                          'reason': value[name]['reason']} for name in failed])


def validate_plan(state, payload):
    """Validate plan structure and binding, not the truth of literary judgments."""
    required = {'plan_version', 'binding', 'provenance', 'poem_core',
                'spatiotemporal_structure', 'emotion_path', 'core_images',
                'structure_plan', 'allusion_plan', 'implicit_content', 'risk_list',
                'prosody_boundary', 'self_check'}
    object_fields(payload, required, 'plan', 'INVALID_POEM_PLAN')
    allowed_value(payload['plan_version'], (PLAN_VERSION,), 'plan_version', 'WRONG_PLAN_VERSION')
    basis = planning_basis(state)
    if payload['binding'] != basis:
        saved = payload['binding']
        differences = sorted(k for k in basis.keys() | saved.keys()
                             if k not in basis or k not in saved or basis[k] != saved[k]) if isinstance(saved, dict) else []
        field_error('STALE_POEM_PLAN', 'binding', 'Regenerate the plan from the current planning basis',
                    differing_fields=differences)
    d = state.to_dict()

    provenance = payload['provenance']
    if not isinstance(provenance, list):
        field_error('INVALID_PLAN_PROVENANCE', 'provenance', 'Expected an array')
    seen = set()
    for i, item in enumerate(provenance):
        loc = f'provenance[{i}]'
        object_fields(item, {'field', 'value', 'source', 'evidence'}, loc, 'INVALID_PLAN_PROVENANCE')
        allowed_value(item['source'], ('user', 'inferred'), loc + '.source', 'INVALID_PLAN_PROVENANCE')
        for key in ('field', 'value', 'evidence'):
            if not text(item[key], empty=key == 'evidence'):
                field_error('INVALID_PLAN_PROVENANCE', loc + '.' + key, 'Expected text')
        if item['field'] in seen:
            field_error('INVALID_PLAN_PROVENANCE', loc + '.field', 'Duplicate provenance field')
        seen.add(item['field'])
        if item['source'] == 'user':
            if not item['evidence'].strip() or item['evidence'] not in d['request']['raw_request']:
                field_error('INVALID_PLAN_PROVENANCE', loc + '.evidence',
                            'User facts need verbatim request evidence')

    core = payload['poem_core']
    core_fields = {'topic', 'core_proposition', 'speaker', 'situation', 'trigger',
                   'primary_emotion', 'inner_tension', 'ending_state', 'register'}
    object_fields(core, core_fields, 'poem_core', 'INVALID_POEM_CORE')
    for k in sorted(core_fields):
        if not text(core[k], empty=k == 'inner_tension'):
            field_error('INVALID_POEM_CORE', 'poem_core.' + k, 'Expected text')
    space = payload['spatiotemporal_structure']
    code = 'INVALID_SPATIOTEMPORAL_PLAN'
    object_fields(space, {'time_path', 'space_path', 'viewpoint', 'reality_imagination_relation'},
                  'spatiotemporal_structure', code)
    for key in ('time_path', 'space_path'):
        if not strings(space[key]):
            field_error(code, 'spatiotemporal_structure.' + key, 'Expected a nonempty array of strings')
    for key in ('viewpoint', 'reality_imagination_relation'):
        if not text(space[key]):
            field_error(code, 'spatiotemporal_structure.' + key, 'Expected nonempty text')
    emotions = payload['emotion_path']
    if not isinstance(emotions, list) or not emotions:
        field_error('INVALID_EMOTION_PATH', 'emotion_path', 'Expected a nonempty array')
    for index, item in enumerate(emotions, 1):
        loc = f'emotion_path[{index - 1}]'
        object_fields(item, {'stage', 'state', 'trigger'}, loc, 'INVALID_EMOTION_PATH')
        if item['stage'] != index:
            field_error('INVALID_EMOTION_PATH', loc + '.stage', 'Emotion stages must be contiguous',
                        received=item['stage'], expected=index)
        for key in ('state', 'trigger'):
            if not text(item[key]):
                field_error('INVALID_EMOTION_PATH', loc + '.' + key, 'Expected nonempty text')

    expected = expected_units(d['form'])
    units = payload['structure_plan']
    if not isinstance(units, list) or len(units) != len(expected):
        field_error('INVALID_STRUCTURE_PLAN', 'structure_plan', 'Expected one entry per structural unit',
                    expected_count=len(expected), received_count=len(units) if isinstance(units, list) else None)
    unit_ids = set()
    for index, (item, identity) in enumerate(zip(units, expected)):
        fields = {'unit_id', 'unit', 'line_indices', 'arc_role', 'primary_task', 'content_scope',
                  'semantic_increment', 'relation_to_previous', 'relation_to_next',
                  'protected_meaning', 'must_not_say_yet', 'antithesis', 'turn_plan'}
        lushi = d['form']['line_count'] == 8
        expected_role = ('起', '承', '转', '合')[index] if lushi else None
        loc = f'structure_plan[{index}]'
        object_fields(item, fields, loc, 'INVALID_STRUCTURE_PLAN')
        for k in ('unit_id', 'unit', 'line_indices'):
            if item[k] != identity[k]:
                field_error('INVALID_STRUCTURE_PLAN', loc + '.' + k, 'Value must match this structural unit',
                            received=item[k], expected=identity[k])
        allowed_value(item['arc_role'], (expected_role,), loc + '.arc_role', 'INVALID_STRUCTURE_PLAN')
        for key in ('primary_task', 'content_scope', 'semantic_increment',
                    'relation_to_previous', 'relation_to_next'):
            if not text(item[key]):
                field_error('INVALID_STRUCTURE_PLAN', loc + '.' + key, 'Expected nonempty text')
        for key in ('protected_meaning', 'must_not_say_yet'):
            if not strings(item[key], allow_empty=key == 'must_not_say_yet'):
                field_error('INVALID_STRUCTURE_PLAN', loc + '.' + key, 'Invalid array of strings')
        unit_ids.add(item['unit_id'])
        middle = d['form']['line_count'] == 8 and item['line_indices'] in ([3, 4], [5, 6])
        antithesis = item['antithesis']
        turn_plan = item['turn_plan']
        if lushi and expected_role == '转':
            turn_fields = {'axis', 'before_state', 'change', 'after_state', 'grounding',
                           'relation_to_previous', 'relation_to_next', 'task_connection',
                           'increment_connection', 'protected_meaning_relation',
                           'new_meaning_or_tension'}
            turn_loc = loc + '.turn_plan'
            code = 'TURN_PLAN_REQUIRED'
            object_fields(turn_plan, turn_fields, turn_loc, code)
            allowed_value(turn_plan['axis'], TURN_AXES, turn_loc + '.axis', code)
            for k in sorted(turn_fields - {'axis'}):
                if not text(turn_plan[k]):
                    field_error(code, turn_loc + '.' + k, 'Expected a nonempty explanation')
            if turn_plan['before_state'].strip() == turn_plan['after_state'].strip():
                field_error(code, turn_loc + '.after_state', 'Must differ from before_state',
                            compared_with=turn_loc + '.before_state')
            for k in ('relation_to_previous', 'relation_to_next'):
                if turn_plan[k] != item[k]:
                    field_error(code, turn_loc + '.' + k, 'Must match the enclosing unit',
                                expected=item[k], received=turn_plan[k])
        elif turn_plan is not None:
            field_error('INVALID_TURN_PLAN', loc + '.turn_plan',
                        'Only the regulated-verse neck couplet has a turn plan', expected=None)
        if middle:
            anti_loc = loc + '.antithesis'
            object_fields(antithesis, {'relation', 'semantic_axes', 'syntactic_strategy', 'avoid_hangzhang'},
                          anti_loc, 'ANTITHESIS_PLAN_REQUIRED')
            allowed_value(antithesis['relation'], RELATIONS, anti_loc + '.relation',
                          'ANTITHESIS_PLAN_REQUIRED')
            if not strings(antithesis['semantic_axes']):
                field_error('ANTITHESIS_PLAN_REQUIRED', anti_loc + '.semantic_axes',
                            'Expected a nonempty array of strings')
            for key in ('syntactic_strategy', 'avoid_hangzhang'):
                if not text(antithesis[key]):
                    field_error('ANTITHESIS_PLAN_REQUIRED', anti_loc + '.' + key, 'Expected nonempty text')
        elif antithesis is not None:
            field_error('INVALID_ANTITHESIS_PLAN', loc + '.antithesis',
                        'Only middle couplets use antithesis planning', expected=None)

    images = payload['core_images']
    if not isinstance(images, list):
        field_error('INVALID_IMAGE_PLAN', 'core_images', 'Expected an array')
    names = set()
    for i, item in enumerate(images):
        loc = f'core_images[{i}]'
        object_fields(item, {'image', 'source_in_scene', 'first_unit', 'surface_function',
                             'deeper_function', 'development'}, loc, 'INVALID_IMAGE_PLAN')
        allowed_value(item['first_unit'], unit_ids, loc + '.first_unit', 'INVALID_IMAGE_PLAN')
        for key in ('image', 'source_in_scene', 'surface_function', 'deeper_function', 'development'):
            if not text(item[key]):
                field_error('INVALID_IMAGE_PLAN', loc + '.' + key, 'Expected nonempty text')
        if item['image'] in names:
            field_error('INVALID_IMAGE_PLAN', loc + '.image', 'Duplicate core image')
        names.add(item['image'])

    allusion = payload['allusion_plan']
    object_fields(allusion, {'requirement', 'function', 'required_semantics', 'items'},
                  'allusion_plan', 'INVALID_ALLUSION_PLAN')
    allowed_value(allusion['requirement'], ('none', 'optional', 'required'),
                  'allusion_plan.requirement', 'INVALID_ALLUSION_PLAN')
    for key, valid in (('function', text(allusion['function'])),
                       ('required_semantics', strings(allusion['required_semantics'], allow_empty=True)),
                       ('items', isinstance(allusion['items'], list))):
        if not valid:
            field_error('INVALID_ALLUSION_PLAN', 'allusion_plan.' + key, 'Invalid value for field')
    expected_allusions = {x['id']: x for x in d['request'].get('allusions', [])}
    covered = set()
    for i, item in enumerate(allusion['items']):
        loc = f'allusion_plan.items[{i}]'
        object_fields(item, {'requirement_id', 'unit_id', 'function', 'required_semantics',
                             'candidate_direction'}, loc, 'INVALID_ALLUSION_PLAN')
        allowed_value(item['requirement_id'], expected_allusions, loc + '.requirement_id', 'INVALID_ALLUSION_PLAN')
        allowed_value(item['unit_id'], unit_ids, loc + '.unit_id', 'INVALID_ALLUSION_PLAN')
        if item['requirement_id'] in covered:
            field_error('INVALID_ALLUSION_PLAN', loc + '.requirement_id', 'Duplicate requirement ID')
        for key, valid in (('function', text(item['function'])),
                           ('required_semantics', strings(item['required_semantics'])),
                           ('candidate_direction', strings(item['candidate_direction']))):
            if not valid:
                field_error('INVALID_ALLUSION_PLAN', loc + '.' + key, 'Invalid value for field')
        covered.add(item['requirement_id'])
    if covered != set(expected_allusions):
        field_error('INVALID_ALLUSION_PLAN', 'allusion_plan.items', 'Every requested allusion needs a plan item',
                    missing_requirement_ids=sorted(expected_allusions.keys() - covered))
    if any(x['required'] for x in expected_allusions.values()):
        allowed_value(allusion['requirement'], ('required',),
                      'allusion_plan.requirement', 'INVALID_ALLUSION_PLAN')

    if not strings(payload['implicit_content'], allow_empty=True):
        field_error('INVALID_POEM_PLAN', 'implicit_content', 'Expected an array of strings')
    if not isinstance(payload['risk_list'], list):
        field_error('INVALID_POEM_PLAN', 'risk_list', 'Expected an array')
    for i, item in enumerate(payload['risk_list']):
        loc = f'risk_list[{i}]'
        object_fields(item, {'risk', 'prevention'}, loc, 'INVALID_RISK_PLAN')
        for key in ('risk', 'prevention'):
            if not text(item[key]):
                field_error('INVALID_RISK_PLAN', loc + '.' + key, 'Expected nonempty text')
    prosody = payload['prosody_boundary']
    code = 'INVALID_PROSODY_BOUNDARY'
    object_fields(prosody, {'form', 'rhyme_requirement', 'antithesis_positions'}, 'prosody_boundary', code)
    for key, expected_value in (('form', d['poem_type']), ('antithesis_positions', d['form']['antithesis_pairs'])):
        if prosody[key] != expected_value:
            field_error(code, 'prosody_boundary.' + key, 'Value conflicts with selected form',
                        expected=expected_value, received=prosody[key])
    if not text(prosody['rhyme_requirement']):
        field_error(code, 'prosody_boundary.rhyme_requirement', 'Expected nonempty text')
    checklist = ('independent_units', 'semantic_progression', 'grounded_images',
                 'viewpoint_continuity', 'ending_prepared', 'allusion_necessary')
    if d['form']['line_count'] == 8:
        checklist += ('arc_roles_assigned', 'neck_couplet_changes_direction',
                      'turn_is_more_than_next_event', 'ending_resolves_or_reframes')
    _validate_checklist(payload['self_check'], checklist,
                        'PLAN_SELF_CHECK_FAILED')
    return deepcopy(payload)


def make_plan_artifact(state, payload):
    plan = validate_plan(state, payload)
    return {'plan': plan, 'sha256': digest(plan)}


def load_plan_artifact(state, artifact):
    object_fields(artifact, {'plan', 'sha256'}, 'poem_plan_artifact', 'INVALID_PLAN_ARTIFACT')
    if artifact['sha256'] != digest(artifact['plan']):
        field_error('INVALID_PLAN_ARTIFACT', 'poem_plan_artifact.sha256',
                    'Digest mismatch; export the plan artifact again')
    return validate_plan(state, artifact['plan'])


def unit_for_line(plan, line_index):
    return next((deepcopy(x) for x in plan['structure_plan'] if line_index in x['line_indices']), None)
