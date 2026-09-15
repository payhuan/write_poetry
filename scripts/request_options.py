"""Normalize explicit meter preferences and the text-only input envelope."""

from copy import deepcopy


def normalize_meter(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"template_id", "start", "first_line_rhymes", "pattern"}:
        raise ValueError("Invalid meter fields")
    result = {"template_id": None, "start": None, "first_line_rhymes": None}
    result.update({k: v for k, v in value.items() if k != "pattern"})
    if result["template_id"] is not None and (not isinstance(result["template_id"], str) or not result["template_id"].strip()):
        raise ValueError("meter.template_id must be a nonempty string or null")
    if result["start"] not in (None, "平起", "仄起"):
        raise ValueError("meter.start must be 平起, 仄起 or null")
    if result["first_line_rhymes"] is not None and type(result["first_line_rhymes"]) is not bool:
        raise ValueError("meter.first_line_rhymes must be a boolean or null")
    pattern = value.get("pattern")
    if pattern is not None:
        patterns = {s + r: (s, flag) for s in ("平起", "仄起")
                    for r, flag in (("首句入韵", True), ("首句不入韵", False))}
        if not isinstance(pattern, str) or pattern not in patterns:
            raise ValueError("Unsupported meter.pattern")
        start, first = patterns[pattern]
        if result["start"] not in (None, start) or (result["first_line_rhymes"] is not None and result["first_line_rhymes"] != first):
            raise ValueError("Conflicting meter preferences")
        result.update(start=start, first_line_rhymes=first)
    return result


def matches_meter(template, meter):
    return ((meter["template_id"] is None or meter["template_id"] == template["id"])
            and (meter["start"] is None or template["pattern"].startswith(meter["start"]))
            and (meter["first_line_rhymes"] is None or meter["first_line_rhymes"] == template["rhyme"]["first_line_rhyme"]))


def normalize_input(value, raw_request):
    """Reserve attachments without pretending that image input is supported."""
    if value is None:
        return {"version": 1, "text": raw_request, "attachments": []}
    if not isinstance(value, dict) or set(value) != {"version", "text", "attachments"}:
        raise ValueError("Invalid input envelope")
    if type(value["version"]) is not int or value["version"] != 1 or value["text"] != raw_request:
        raise ValueError("Input envelope version/text conflict")
    if not isinstance(value["attachments"], list):
        raise ValueError("input.attachments must be an array")
    if value["attachments"]:
        raise ValueError("Image/attachment input is reserved for a future version; provide a text description now")
    return deepcopy(value)
