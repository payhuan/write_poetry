"""Input contracts for allusions and user-authored fragments, not semantic checks."""

from text_normalizer import normalize_line


class ContentConflict(ValueError):
    """Explicit content requirements cannot fit the selected poem structure."""


def normalize_requirements(value, field, raw_request):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result, ids = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{field} entries must be objects")
        if field == "allusions":
            defaults = {"description": "", "required": True}
            required = {"id", "name", "source_excerpt"}
        else:
            defaults = {"mode": "exact", "line_index": None}
            required = {"id", "text", "kind", "source_excerpt"}
        if not required <= set(item) or set(item) - required - set(defaults):
            raise ValueError(f"Invalid {field} entry fields")
        entry = {**defaults, **item}
        for key in required:
            if not isinstance(entry[key], str) or not entry[key].strip():
                raise ValueError(f"{field}.{key} must be a nonempty string")
        if entry["id"] in ids:
            raise ValueError(f"Duplicate {field} id")
        ids.add(entry["id"])
        if entry["source_excerpt"] not in raw_request:
            raise ValueError(f"{field} evidence absent from user text")
        if field == "allusions":
            if type(entry["required"]) is not bool or not isinstance(entry["description"], str):
                raise ValueError("Invalid allusion required/description")
        else:
            if entry["kind"] not in ("line", "phrase") or entry["mode"] not in ("exact", "adapt", "inspiration"):
                raise ValueError("Invalid fragment kind/mode")
            if entry["text"] not in entry["source_excerpt"]:
                raise ValueError("Fragment text must be preserved verbatim in source_excerpt")
            if entry["line_index"] is not None and (type(entry["line_index"]) is not int or entry["line_index"] < 1):
                raise ValueError("Fragment line_index must be positive integer or null")
            if entry["mode"] == "inspiration" and entry["line_index"] is not None:
                raise ContentConflict("仅作灵感的片段不应同时锁定句位，请明确保留方式。")
        result.append(entry)
    return result


def check_fragment_structure(fragments, form):
    fixed = {}
    distinct_lines = set()
    for entry in fragments:
        index = entry["line_index"]
        if index is not None and index > form["line_count"]:
            raise ContentConflict("指定片段句位超出当前诗体句数。")
        if entry["mode"] != "exact":
            continue
        line = normalize_line(entry["text"], index or 1)
        if line["segments"] != 1 or not line["cells"] or any(not c["is_han"] for c in line["cells"]):
            raise ContentConflict("原样片段应为单句或短语；多句请拆分，非汉字内容请澄清保留方式。")
        text = line["text"]
        width = form["line_length"]
        if len(text) > width or (entry["kind"] == "line" and len(text) != width):
            raise ContentConflict("原样保留片段的字数与当前诗体不兼容，请调整诗体或允许润色。")
        if entry["kind"] == "line":
            distinct_lines.add(text)
            if index is not None:
                if index in fixed and fixed[index] != text:
                    raise ContentConflict("同一句位指定了不同的原样诗句。")
                fixed[index] = text
    if len(distinct_lines) > form["line_count"]:
        raise ContentConflict("要求保留的不同完整诗句超过当前诗体句数。")
    for entry in fragments:
        if entry["mode"] == "exact" and entry["kind"] == "phrase" and entry["line_index"] in fixed:
            if normalize_line(entry["text"], 1)["text"] not in fixed[entry["line_index"]]:
                raise ContentConflict("指定短语不在该句位已锁定的原样诗句中。")
