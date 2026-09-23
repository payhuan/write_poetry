"""Validate LLM-extracted intent and initialize a poetry state (no model API required)."""

import argparse
import json
from pathlib import Path

from init_state import initialize
from poem_state import PoemState
from request_options import normalize_meter, normalize_input
from poem_templates import TemplateLibrary
from content_requirements import normalize_requirements, ContentConflict
from runtime_workspace import configure_workspace, input_path, output_path


ALIASES = {
    "五绝": "五言绝句", "五言绝句": "五言绝句",
    "七绝": "七言绝句", "七言绝句": "七言绝句",
    "五律": "五言律诗", "五言律诗": "五言律诗",
    "七律": "七言律诗", "七言律诗": "七言律诗",
}
FIELDS = {"theme", "scene", "emotion", "characters", "time", "poem_type", "constraints", "meter", "allusions", "required_fragments"}


class ClarificationRequired(ValueError):
    """The extraction identifies unresolved intent; do not silently choose an answer."""


def string(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value.strip()


def strings(value, field):
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array of strings")
    result = []
    for item in value:
        item = string(item, field)
        if item and item not in result:
            result.append(item)
    return result


def parse_request(raw_request, extraction, *, input_envelope=None):
    """Normalize an extraction. Semantic correctness remains the LLM's responsibility.

    extraction = {fields..., evidence: {field: [verbatim excerpts]}, questions: []}
    Evidence verifies source membership, not semantic entailment.
    """
    if not isinstance(raw_request, str) or not raw_request.strip():
        raise ValueError("raw_request must be a nonempty string")
    if not isinstance(extraction, dict):
        raise ValueError("extraction must be a JSON object")
    input_envelope = normalize_input(input_envelope, raw_request)
    unknown = set(extraction) - FIELDS - {"evidence", "questions"}
    if unknown:
        raise ValueError("Unknown extraction fields: " + ", ".join(sorted(unknown)))
    questions = strings(extraction.get("questions", []), "questions")
    if questions:
        raise ClarificationRequired("；".join(questions))
    request = {"raw_request": raw_request}
    for field in ("allusions", "required_fragments"):
        if field in extraction:
            try:
                request[field] = normalize_requirements(extraction[field], field, raw_request)
            except ContentConflict as exc:
                raise ClarificationRequired(str(exc)) from exc
    try:
        meter = normalize_meter(extraction.get("meter"))
    except ValueError as exc:
        raise ClarificationRequired(str(exc)) from exc
    request["meter"] = meter
    request["input"] = input_envelope
    for field in ("theme", "scene", "time"):
        value = extraction.get(field)
        request[field] = "" if value is None else string(value, field)
    if not request["theme"]:
        raise ClarificationRequired("希望这首诗写什么主题或场景？")
    for field in ("emotion", "characters", "constraints"):
        value = extraction.get(field)
        request[field] = [] if value is None else strings(value, field)
    poem_type = extraction.get("poem_type")
    if poem_type is not None:
        poem_type = string(poem_type, "poem_type")
        if poem_type:
            if poem_type not in ALIASES:
                raise ClarificationRequired(f"诗体“{poem_type}”不明确或不在首期范围内，请确定四种近体诗之一。")
            request["poem_type"] = ALIASES[poem_type]
    library = TemplateLibrary()
    inferred_from_template = False
    # A concrete template identifies its poem type; do not first apply the default.
    if meter["template_id"] is not None and "poem_type" not in request:
        candidates = library.candidates(meter=meter)
        if len(candidates) != 1:
            raise ClarificationRequired("指定模板不存在或与起式、首句押韵要求冲突，请核对。")
        request["poem_type"] = candidates[0]["type"]
        inferred_from_template = True
    candidates = library.candidates(request.get("poem_type", "七言律诗"), meter=meter)
    if not candidates:
        raise ClarificationRequired("诗体、模板、起式或首句押韵要求互相冲突，请确定采用哪一种。")
    evidence = extraction.get("evidence", {})
    if not isinstance(evidence, dict) or set(evidence) - FIELDS:
        raise ValueError("evidence must map supported fields to source excerpts")
    verified = {}
    for field, excerpts in evidence.items():
        excerpts = strings(excerpts, f"evidence.{field}")
        if any(excerpt not in raw_request for excerpt in excerpts):
            raise ValueError(f"evidence.{field} contains text absent from raw_request")
        if excerpts and (not request.get(field) or (field == "meter" and all(v is None for v in meter.values()))):
            raise ValueError(f"evidence.{field} has no corresponding extracted value")
        verified[field] = excerpts
    try:
        state = initialize(request)
    except ContentConflict as exc:
        raise ClarificationRequired(str(exc)) from exc
    if inferred_from_template:
        state["assumptions"].append("根据指定模板确定诗体")
    state["input_parse"] = {
        "method": "llm_extraction_with_validation",
        "evidence": verified,
        "unattributed_fields": [field for field in FIELDS if request.get(field) and not verified.get(field)
                                and (field != "meter" or any(v is not None for v in meter.values()))],
    }
    state["input_parse"]["unattributed_fields"].sort()
    model = PoemState.from_dict(state)
    if len(candidates) == 1:
        library.bind(model)
    return model.to_dict()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="Original user request")
    source.add_argument("--text-file", type=Path, help="UTF-8 original request file")
    parser.add_argument("--extraction", required=True, type=Path, help="LLM extraction JSON")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    try:
        configure_workspace(args.work_dir)
        target = output_path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = args.text if args.text is not None else input_path(args.text_file).read_text(encoding="utf-8-sig")
        extraction = json.loads(input_path(args.extraction).read_text(encoding="utf-8-sig"))
        state = parse_request(raw, extraction)
        with target.open("x", encoding="utf-8") as output:
            json.dump(state, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except ClarificationRequired as exc:
        parser.exit(3, f"需要澄清: {exc}\n")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"解析失败: {exc}\n")


if __name__ == "__main__":
    main()
