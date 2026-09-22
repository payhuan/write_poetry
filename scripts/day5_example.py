"""Deterministic Day 5 workflow fixture, not a literary quality demonstration."""
import argparse
import json
from assessment_binding import make_binding
from day4_example import fixture_state, fixture_engine, fixture_plan, drive, response_for as search_response
from poetry_search import Search
from poetry_polish import (Polish, IDENTITY, WEIGHTS, REVIEW_VERSION,
                           REDLINE_VERSION, REDLINE_CHECKS)
from poetry_round import write_new_json
from runtime_workspace import configure_workspace, output_path
from word_refinement import VERSION as REFINEMENT_VERSION


def review_for(state, *, revise=False, positions=(1,), score=.8):
    dimensions = {}
    for name in WEIGHTS:
        na = name == 'parallelism' and state['form']['line_count'] == 4
        dimensions[name] = dict(status='not_applicable' if na else 'assessed',
                                score=None if na else score, reason='确定性流程夹具理由', couplets=[])
        if name == 'parallelism' and not na:
            dimensions[name]['couplets'] = [dict(line_indices=p, semantic='语义对应的夹具说明', syntax='句法对应的夹具说明')
                                              for p in ([3, 4], [5, 6])]
    return dict(review_version=REVIEW_VERSION, binding=make_binding(state), revision=state['revision'],
                dimensions=dimensions, issues=[dict(id='i1', dimension='expression',
                    locations=[dict(line_index=i, quote=state['lines'][i-1]) for i in positions],
                    severity='minor', suggestion='在这些句位尝试更贴合全诗的措辞', requires_user_change=False)] if revise else [],
                uncertainties=[], recommend_revision=revise, conclusion='revise' if revise else 'ready',
                user_constraints=[dict(constraint=c, status='satisfied', reason='固定要求复核夹具',
                                       evidence=[dict(line_index=1, quote=state['lines'][0])])
                                  for c in state['request']['constraints']])


def package_for(state, *, positions=(1,), modification_id='m1', invalid=False):
    return dict(id=modification_id, binding=make_binding(state), revision=state['revision'],
                edits=[dict(line_index=i, old=state['lines'][i-1],
                            new='错' if invalid else state['lines'][i-1].translate(str.maketrans('天水', '地火')))
                       for i in positions], issue_ids=['i1'], reason='针对已指出句位的固定候选')


def envelope(job, payload):
    return {**{k: job[k] for k in IDENTITY}, 'payload': payload}


def comparison_for(job, *, score=.9, decision='adopt'):
    new = review_for(job['context']['target'], score=score)
    old = job['context']['review']['review']
    dims = {}
    for name in WEIGHTS:
        a, b = old['dimensions'][name]['score'], new['dimensions'][name]['score']
        verdict = 'not_applicable' if a is None else 'better' if b > a else 'worse' if b < a else 'same'
        dims[name] = dict(verdict=verdict, reason='针对全诗而非孤立句的固定比较理由')
    return dict(candidate_review=new, dimensions=dims, resolved_issue_ids=['i1'],
                decision=decision, reason='固定比较结论：' + decision)


def content_for(job, status='satisfied'):
    f = job['context']['filtered']
    fake = dict(stage='final_content', context=f['context'], filtered=f, attempt=job['attempt'],
                run_id='', job_id='', path_id='', batch_id='', context_sha256='')
    payload = search_response(fake)['payload']
    for item in payload['reviews']:
        for verdict in item['requirements']:
            verdict['status'] = status
    return payload


def redline_for(state, *, answer='no', check=None):
    checks = {}
    for name in REDLINE_CHECKS:
        verdict = answer if check in (None, name) else 'no'
        checks[name] = dict(answer=verdict, reason='最终出口红线检查夹具',
                            evidence=[dict(line_index=1, quote=state['lines'][0])])
    return dict(redline_version=REDLINE_VERSION, binding=make_binding(state),
                revision=state['revision'], checks=checks,
                conclusion='pass' if all(x['answer'] == 'no' for x in checks.values()) else 'blocked')


def no_change_for(job):
    request = job['context']['refinement_request']
    return {'refinement_version': REFINEMENT_VERSION,
            'request_sha256': request['request_sha256'], 'status': 'NO_CHANGE',
            'reason': '逐字比较后没有确认到足以承担代价的净增益。',
            'result': {'lines': request['original_lines']}}


def run_example(*, adopt=False):
    initial = fixture_state()
    search = drive(Search.create(initial, poem_plan=fixture_plan(initial), engine=fixture_engine()))
    polish = Polish.create(search.to_dict(), search.data['drafts'][0]['path_id'], engine=fixture_engine())
    for _ in range(30):
        j = polish.next_job()
        if j is None:
            break
        if j['stage'] == 'review':
            p = review_for(polish.data['accepted'], revise=not bool(polish.data['history']))
        elif j['stage'] == 'generate':
            p = dict(packages=[package_for(polish.data['accepted'], invalid=not adopt)],
                     needs_user_change=False, reason='固定修改演示')
        elif j['stage'] == 'compare':
            p = comparison_for(j)
        elif j['stage'] == 'refinement':
            p = no_change_for(j)
        elif j['stage'].endswith('content'):
            p = content_for(j)
        elif j['stage'] == 'redline':
            p = redline_for(polish.data['accepted'])
        else:
            p = dict(title='流程测试', notes=['使用微型测试韵库与固定模型响应；不代表真实诗作质量。'])
        polish.submit(envelope(j, p))
    assert polish.data['status'] == 'completed', polish.data['reason']
    assert polish.data['history'][0]['decision'] == ('adopted' if adopt else 'rejected')
    return polish


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='examples/day5-result.json')
    parser.add_argument('--adopt', action='store_true')
    parser.add_argument('--work-dir')
    args = parser.parse_args()
    configure_workspace(args.work_dir)
    polish = run_example(adopt=args.adopt)
    write_new_json(output_path(args.output), polish.to_dict())
    print(json.dumps(polish.result(include_notes=True), ensure_ascii=True))


if __name__ == '__main__':
    main()
