"""Lossless code-point coordinates; only explicit punctuation is ignored."""

IGNORED = frozenset('，。！？；：、,.!?;:「」『』“”‘’（）()《》〈〉【】')
BOUNDARIES = frozenset('，。！？；,.!?;\n\r')


def is_han(char):
    n = ord(char)
    return (0x3400 <= n <= 0x4DBF or 0x4E00 <= n <= 0x9FFF
            or 0xF900 <= n <= 0xFAFF or 0x20000 <= n <= 0x2FA1F
            or 0x30000 <= n <= 0x3347F or char == '〇')


def normalize_line(text, line):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Line text must be a nonempty string')
    cells, segments, in_segment = [], 0, False
    for offset, char in enumerate(text):
        if char in BOUNDARIES:
            in_segment = False
        if char.isspace() or char in IGNORED:
            continue
        if not in_segment:
            segments += 1
            in_segment = True
        cells.append({'line': line, 'pos': len(cells) + 1, 'char': char,
                      'offset': offset, 'is_han': is_han(char)})
    return {'line': line, 'original': text, 'text': ''.join(c['char'] for c in cells),
            'cells': cells, 'segments': segments}
