"""Deterministic failure -> checkpoint recovery -> draft demonstration, no model calls."""
import argparse
import json
from poem_state import PoemState
from poem_templates import TemplateLibrary
from constraint_engine import ConstraintEngine
from rhyme_lexicon import RhymeLexicon
from poetry_round import ENVELOPE, WEIGHTS, write_new_json
from poetry_search import Search
from round_content import CONTENT_VERSION
from poem_planning import PLAN_VERSION, expected_units, make_plan_artifact, planning_basis
from poetry_unit import WEIGHTS as UNIT_WEIGHTS
from runtime_workspace import configure_workspace, output_path

LEXICON = {'上平声部': {'一东': list('天地人风')}, '上声部': {'一董': list('水火土风')}}


def fixture_state(form='五言绝句', **budget):
    s = PoemState.create({'raw_request': '山中测试', 'theme': '山中', 'poem_type': form,
                          'meter': {'start': '仄起', 'first_line_rhymes': False}})
    TemplateLibrary().bind(s)
    s.set_plan(rhyme={'system': '平水韵', 'group': '一东', 'first_line_rhymes': False},
               structure=[{'unit': '颔联', 'intent': '由远及近'}])
    s.set_budget(**{'beam_width': 1, **budget})
    return s


def fixture_engine():
    return ConstraintEngine(RhymeLexicon(LEXICON), duplicate_policy='report')


def fixture_plan(state):
    d = state.to_dict()
    units = []
    for item in expected_units(d['form']):
        middle = d['form']['line_count'] == 8 and item['line_indices'] in ([3, 4], [5, 6])
        units.append({**item, 'primary_task': '完成当前结构任务', 'content_scope': '当前可感知内容',
                      'semantic_increment': '相对前文增加一层具体认识',
                      'relation_to_previous': '承接前一单位', 'relation_to_next': '为后一单位留出空间',
                      'protected_meaning': ['不改变用户事实'], 'must_not_say_yet': [],
                      'antithesis': ({'relation': 'contrast', 'semantic_axes': ['远与近'],
                                      'syntactic_strategy': '上下句采用对应句法骨架',
                                      'avoid_hangzhang': '两句提供不同信息，不作同义复述'} if middle else None)})
    allusions = d['request'].get('allusions', [])
    plan = {'plan_version': PLAN_VERSION, 'binding': planning_basis(state),
            'provenance': [{'field': 'theme', 'value': d['theme'], 'source': 'user',
                            'evidence': d['request']['raw_request']}],
            'poem_core': {'topic': d['theme'], 'core_proposition': '在具体山中场景中形成认识变化',
                          'speaker': '身处山中的观察者', 'situation': '观察山中景物', 'trigger': '景物变化',
                          'primary_emotion': '由平静转为有所触动', 'inner_tension': '',
                          'ending_state': '收于克制余味', 'register': '清淡自然'},
            'spatiotemporal_structure': {'time_path': ['眼前'], 'space_path': ['近景', '远景'],
                                         'viewpoint': '固定观察点', 'reality_imagination_relation': '以实景为主'},
            'emotion_path': [{'stage': 1, 'state': '平静观察', 'trigger': '进入山中场景'}],
            'core_images': [{'image': '山', 'source_in_scene': '用户主题中的山中场景',
                             'first_unit': units[0]['unit_id'], 'surface_function': '建立空间',
                             'deeper_function': '承载情绪变化', 'development': '由背景转为情绪触发'}],
            'structure_plan': units,
            'allusion_plan': {'requirement': 'required' if any(x['required'] for x in allusions) else
                              'optional' if allusions else 'none',
                              'function': '按用户要求承担必要表达' if allusions else '当前规划不主动用典',
                              'required_semantics': ['保留用户指定典义'] if allusions else [],
                              'items': [{'requirement_id': x['id'], 'unit_id': units[-1]['unit_id'],
                                         'function': '承接用户指定典故', 'required_semantics': ['典义准确'],
                                         'candidate_direction': ['自然融入当前诗境']} for x in allusions]},
            'implicit_content': [], 'risk_list': [{'risk': '意象堆积', 'prevention': '只发展核心意象'}],
            'prosody_boundary': {'form': d['poem_type'], 'rhyme_requirement': '遵守已选平水韵',
                                  'antithesis_positions': d['form']['antithesis_pairs']},
            'self_check': {name: {'status': 'pass', 'reason': '固定规划夹具通过'} for name in
                           ('independent_units', 'semantic_progression', 'grounded_images',
                            'viewpoint_continuity', 'ending_prepared', 'allusion_necessary')}}
    return make_plan_artifact(state, plan)


def line_for(context, variant=0):
    tones = context['tone_requirements']
    out = []
    for i, item in enumerate(tones):
        tone = item['tune']
        if tone == '中':
            tone = tones[i+1]['tune']
        out.append(('天' if variant == 0 else '地') if tone == '平' else ('水' if variant == 0 else '火'))
    return ''.join(out)


def unit_line_for(requirement, variant=0):
    return line_for({'tone_requirements': requirement['tone_requirements']}, variant)


def reading_payload(filtered, attempt=1):
    reviews = []
    for item in filtered['reading_request']['reviews']:
        decisions = []
        for occurrence in item['request']['occurrences']:
            option = next((item for item in occurrence['options'] if item['section'] == '上平声部'),
                          occurrence['options'][0])
            decisions.append({'coordinate': occurrence['coordinate'], 'char': occurrence['char'],
                              'status': 'selected', 'section': option['section'], 'group': option['group'],
                              'reason': '固定测试夹具按给定语境选择已收录读音。'})
        reviews.append({'id': item['id'], 'review': {
            'review_version': item['request']['review_version'],
            'request_sha256': item['request']['request_sha256'], 'decisions': decisions}})
    return {**{k: filtered['context'][k] for k in ENVELOPE}, 'attempt': attempt,
            'filter_sha256': filtered['filter_sha256'], 'reviews': reviews}


def response_for(job, *, fail_high=False, two=False, value=0.8):
    c = job['context']
    payload = {**{k: c[k] for k in ENVELOPE}, 'attempt': job['attempt']}
    if job['stage'] == 'unit_generate':
        candidates = []
        for variant in range(2 if two else 1):
            lines = [{'line_index': requirement['line_index'],
                      'text': unit_line_for(requirement, variant)}
                     for requirement in c['line_requirements']]
            candidates.append({'id': str(variant), 'lines': lines,
                               'expression_path': '以景物关系推进当前联意',
                               'semantic_increment': '完成当前联的新增认识', 'risks': [],
                               'semantic_lock': {
                                   'protected_meaning': '不改变用户事实', 'facts': [],
                                   'images': ['山中景物'], 'relations': ['上下句相互映照'],
                                   'emotion_direction': '由平静转为触动',
                                   'structural_function': c['planned_unit']['primary_task'],
                                   'confirmed_allusions': []}})
        payload.update(status='candidates', candidates=candidates)
    elif job['stage'] == 'unit_score':
        f = job['filtered']
        parallel = {name: {'status': 'pass', 'reason': '固定对仗评价夹具'} for name in
                    ('semantic_relation', 'syntax', 'parts_of_speech', 'image_relation', 'hangzhang')}
        parallel.update(score=value, reason='对仗结构完整且两句不合掌。')
        payload.update(filter_sha256=f['filter_sha256'], rubric_version='literary-2',
                       scores=[{'id': item['id'], 'dimensions': {k: value for k in UNIT_WEIGHTS},
                                'parallelism': parallel, 'reason': '固定整联评分夹具。'}
                               for item in f['score_request']['candidates']])
    elif job['stage'] == 'unit_content':
        f = job['filtered']
        reviews = []
        for requested in f['content_request']['requests']:
            record = next(x for x in f['records'] if x['id'] == requested['candidate_id'])
            line_filter = next(x for x in record['line_filters']
                               if x['context']['line_index'] == requested['line_index'])
            fake = {'stage': 'content', 'context': line_filter['context'], 'filtered': line_filter,
                    'attempt': job['attempt'], 'run_id': '', 'job_id': '', 'path_id': '',
                    'batch_id': '', 'context_sha256': ''}
            reviews.append({'candidate_id': requested['candidate_id'],
                            'line_index': requested['line_index'],
                            'response': response_for(fake)['payload']})
        payload.update(filter_sha256=f['filter_sha256'], reviews=reviews)
    elif job['stage'] == 'unit_reading':
        f = job['filtered']
        reviews = []
        for requested in f['reading_request']['requests']:
            record = next(x for x in f['records'] if x['id'] == requested['candidate_id'])
            line_filter = next(x for x in record['line_filters']
                               if x['context']['line_index'] == requested['line_index'])
            reviews.append({'candidate_id': requested['candidate_id'],
                            'line_index': requested['line_index'],
                            'response': reading_payload(line_filter, job['attempt'])})
        payload.update(filter_sha256=f['filter_sha256'], reviews=reviews)
    elif job['stage'] == 'generate':
        texts = [line_for(c)]
        if two or not c['lines']:
            texts.append(line_for(c, 1))
        if fail_high and c['lines'] and '水' in c['lines'][0]:
            texts = ['错']
        payload['candidates'] = [{
            'id': str(i), 'text': t, 'line_index': c['line_index'],
            **({'expression_path': '以当前景物推进诗意', 'semantic_increment': '增加当前句的具体认识',
                'risks': [], 'semantic_lock': {'protected_meaning': '不改变用户事实', 'facts': [],
                    'images': ['山中景物'], 'relations': [], 'emotion_direction': '由平静转为触动',
                    'structural_function': c['planned_unit']['primary_task'], 'confirmed_allusions': []}}
               if c.get('planned_unit') else {})}
            for i, t in enumerate(texts)]
    elif job['stage'] == 'score':
        f = job['filtered']
        payload.update(filter_sha256=f['filter_sha256'], rubric_version='literary-1',
                       scores=[{'id': item['id'], 'evaluation': {'dimensions': {k: value - i * .1 for k in WEIGHTS},
                               'reason': '确定性控制流程夹具评分。'}} for i, item in enumerate(f['score_request']['candidates'])])
    elif job['stage'] == 'poem_reading':
        request = job['reading_request']
        decisions = []
        for occurrence in request['occurrences']:
            option = next((x for x in occurrence['options'] if x['tone'] == '平'),
                          occurrence['options'][0])
            decisions.append({'coordinate': occurrence['coordinate'], 'char': occurrence['char'],
                              'status': 'selected', 'section': option['section'],
                              'group': option['group'], 'reason': '固定夹具按句义选择已收录义项'})
        payload = {'attempt': job['attempt'], 'review': {
            'review_version': request['review_version'],
            'request_sha256': request['request_sha256'], 'decisions': decisions}}
    elif job['stage'] == 'reading':
        payload = reading_payload(job['filtered'], job['attempt'])
    else:
        f = job['filtered']
        def verdict(item, requirement):
            result = {'kind': requirement['kind'], 'requirement_id': requirement['requirement_id'],
                      'status': 'satisfied', 'reason': '固定语义夹具', 'interpretation': '仅验证契约',
                      'evidence': [{'line_index': item['line_index'], 'quote': item['text']}]}
            if requirement['kind'] == 'allusion':
                reference = requirement['allusion_reference']
                basis = ({'kind': 'bundled', 'references': [reference['record']['record_sha256']]}
                         if reference['status'] == 'resolved' else
                         {'kind': 'external', 'references': ['固定测试外部出处']})
                result['allusion_analysis'] = {
                    'source_basis': basis,
                    'classification': {'source_type': 'event', 'visibility': 'implicit',
                                       'semantic_relation': 'direct', 'transformation': 'allusive_transform'},
                    'fact_checks': {name: {'status': 'pass', 'reason': '固定事实门夹具'} for name in
                                    ('persons', 'relationships', 'event', 'source', 'core_meaning')},
                    'mapping': [{'source_element': '原典结构', 'current_element': '当前诗境',
                                 'quote': item['text']}],
                    'fit_checks': {name: {'status': 'pass', 'reason': '固定映射门夹具'} for name in
                                   ('semantic', 'situation', 'emotion', 'identity', 'function')},
                    'function': '压缩表达并增加原典背景。', 'reversal_signals': [], 'error_codes': [],
                    'quality': {'dimensions': {'semantic_fit': 3, 'context_fit': 3, 'coherence': 2,
                                               'naturalness': 2, 'semantic_gain': 2, 'transformation': 2},
                                'penalties': {'obtrusiveness': 0, 'redundancy': 0}, 'total': 14}}
            return result
        payload.update(filter_sha256=f['filter_sha256'], content_version=CONTENT_VERSION, reviews=[
            {'id': item['id'], 'line_index': item['line_index'], 'text_sha256': item['text_sha256'],
             'requirements': [verdict(item, r) for r in item['requirements']]}
            for item in f['content_request']['candidates']])
    return {**{k: job[k] for k in ('run_id', 'job_id', 'path_id', 'batch_id', 'context_sha256')}, 'payload': payload}


def drive(search, **kwargs):
    for _ in range(200):
        if search.data['status'] != 'waiting_for_llm':
            return search
        search.submit(response_for(search.next_job(), **kwargs))
    raise AssertionError('Controller failed to terminate')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='examples/day4-result.json')
    parser.add_argument('--work-dir')
    args = parser.parse_args()
    configure_workspace(args.work_dir)
    state = fixture_state()
    search = drive(Search.create(state, poem_plan=fixture_plan(state), engine=fixture_engine()), fail_high=True)
    assert search.data['status'] == 'ready_for_review', search.data['events']
    assert search.data['used']['backtracks'] == 1
    write_new_json(output_path(args.output), search.to_dict())
    print(json.dumps({'status': search.data['status'], 'used': search.data['used'],
                      'events': search.data['events']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
