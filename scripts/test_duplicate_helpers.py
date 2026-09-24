def duplicate_decisions(request, device='internal_parallelism', reason='夹具声明句内对称复现以形成回环照应。'):
    lines = dict(zip(request['context'].get('line_indices', range(1, len(request['context']['lines']) + 1)),
                     request['context']['lines']))
    decisions = []
    for occurrence in request['occurrences']:
        evidence = []
        for position in occurrence['positions']:
            line = lines[position['line']]
            evidence.append({'line': position['line'], 'pos': position['pos'],
                             'quote': line, 'meaning': f"该处{occurrence['char']}参与具体验证语义。"})
        decisions.append({'char': occurrence['char'], 'device': device, 'reason': reason,
                          'position_evidence': evidence})
    return decisions
