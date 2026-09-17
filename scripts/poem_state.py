"""Validated, isolated poetry state with revision-aware editing and JSON persistence."""

from copy import deepcopy
import json
import math
from pathlib import Path
from request_options import normalize_meter, normalize_input, matches_meter
from assessment_binding import BINDING_FIELDS, validate_binding_shape, require_binding
from content_requirements import normalize_requirements, check_fragment_structure


def _initial_data(request, *, validate_library=True):
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    allowed = {"raw_request", "theme", "scene", "emotion", "characters", "time", "poem_type", "constraints", "meter", "input", "allusions", "required_fragments"}
    if set(request) - allowed:
        raise ValueError("Unknown fields: " + ", ".join(sorted(set(request) - allowed)))
    for key in ("raw_request", "theme"):
        if not isinstance(request.get(key), str) or not request[key].strip():
            raise ValueError(key + " must be a nonempty string")
    normalized = deepcopy(request)
    # Optional additive fields preserve existing 0.3.0 snapshots and bindings.
    for field in ("allusions", "required_fragments"):
        if field in request:
            normalized[field] = normalize_requirements(request[field], field, request["raw_request"])
    normalized["meter"] = normalize_meter(request.get("meter"))
    normalized["input"] = normalize_input(request.get("input"), request["raw_request"])
    for key in ("scene", "time"):
        normalized.setdefault(key, "")
        if not isinstance(normalized[key], str):
            raise ValueError(key + " must be a string")
    for key in ("emotion", "characters", "constraints"):
        normalized.setdefault(key, [])
        if not isinstance(normalized[key], list) or any(not isinstance(x, str) for x in normalized[key]):
            raise ValueError(key + " must be an array of strings")
    forms = json.loads(Path(__file__).with_name("poem-forms.json").read_text(encoding="utf-8"))
    inferred_from_template = False
    if normalized["meter"]["template_id"] is not None and "poem_type" not in normalized:
        from poem_templates import TemplateLibrary
        candidates = TemplateLibrary().candidates(meter=normalized["meter"])
        if len(candidates) != 1:
            raise ValueError("Unknown or conflicting requested template")
        normalized["poem_type"] = candidates[0]["type"]
        inferred_from_template = True
    poem_type = normalized.setdefault("poem_type", "七言绝句")
    if not isinstance(poem_type, str) or poem_type not in forms:
        raise ValueError("Unsupported poem_type")
    check_fragment_structure(normalized.get("required_fragments", []), forms[poem_type])
    if validate_library and any(v is not None for v in normalized["meter"].values()):
        from poem_templates import TemplateLibrary
        if not TemplateLibrary().candidates(poem_type, meter=normalized["meter"]):
            raise ValueError("Requested meter conflicts with poem type")
    return {
        "schema_version": "0.3.0", "request": normalized,
        "assumptions": (["根据指定模板确定诗体"] if inferred_from_template else
                        ["未指定诗体，默认七言绝句"] if "poem_type" not in request else []),
        "poem_type": poem_type, "form": forms[poem_type], "template_id": None,
        "theme": normalized["theme"], "emotion": list(normalized["emotion"]),
        "imagery": [], "structure": [],
        "rhyme": {"system": "平水韵", "group": None, "first_line_rhymes": normalized["meter"]["first_line_rhymes"]},
        "lines": [], "constraints": list(normalized["constraints"]),
        "candidate_history": [], "quality_score": None, "checks": {},
        "budget": {"candidates_per_round": 10, "beam_width": 3, "max_backtracks": 3, "max_rounds": 20},
        "status": "planning",
        "revision": 0,
    }


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _strings(value):
    return isinstance(value, list) and all(_text(x) for x in value)


def _score(value):
    return value is None or (type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1)


class PoemState:
    """All reads return copies; mutations validate before committing.

    Revision changes describe creative-context changes. Candidate and assessment
    records retain the revision they belong to, so historical checks cannot be
    mistaken for the current poem's checks.
    """

    def __init__(self, data):
        self._data = deepcopy(data)
        self._validate(self._data)

    @classmethod
    def create(cls, request):
        return cls(_initial_data(request))

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("State must be an object")
        data = deepcopy(data)
        if data.get("schema_version") == "0.1.0":
            # Existing scaffold states have no revision information. Never reuse
            # unversioned assessments after migration.
            data.update(schema_version="0.2.0", revision=0, checks={}, quality_score=None)
            if data.get("status") == "completed":
                data["status"] = "reviewing"
        if data.get("schema_version") == "0.2.0":
            data["request"] = _initial_data(data.get("request"))["request"]
            data["schema_version"] = "0.3.0"
        return cls(data)

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8-sig")))

    def save(self, path):
        """Write a new snapshot; never overwrite an existing state file."""
        payload = json.dumps(self._data, ensure_ascii=False, indent=2, allow_nan=False)
        with Path(path).open("x", encoding="utf-8") as output:
            output.write(payload + "\n")

    def to_dict(self):
        return deepcopy(self._data)

    def fork(self):
        return type(self)(self._data)

    @staticmethod
    def _validate(d):
        if not isinstance(d, dict) or d.get("schema_version") != "0.3.0":
            raise ValueError("Unsupported state schema_version")
        baseline = _initial_data(d.get("request"), validate_library="meter_template" not in d)
        if d["request"] != baseline["request"]:
            raise ValueError("Request must contain normalized meter/input fields")
        required = set(baseline)
        if not required <= set(d) or set(d) - required - {"input_parse", "meter_template"}:
            raise ValueError("Missing or unknown state fields")
        if d["poem_type"] != baseline["poem_type"] or d["form"] != baseline["form"]:
            raise ValueError("Poem form must agree with request")
        if not _text(d["theme"]) or type(d["revision"]) is not int or d["revision"] < 0:
            raise ValueError("Invalid theme or revision")
        for name in ("emotion", "imagery", "constraints", "assumptions", "lines"):
            if not _strings(d[name]):
                raise ValueError(f"{name} must contain nonempty strings")
        if len(d["lines"]) > d["form"]["line_count"]:
            raise ValueError("Too many lines")
        if d["template_id"] is not None and not _text(d["template_id"]):
            raise ValueError("Invalid template_id")
        rhyme = d["rhyme"]
        if (not isinstance(rhyme, dict) or set(rhyme) != {"system", "group", "first_line_rhymes"}
                or not _text(rhyme["system"])
                or (rhyme["group"] is not None and not _text(rhyme["group"]))
                or (rhyme["first_line_rhymes"] is not None and type(rhyme["first_line_rhymes"]) is not bool)):
            raise ValueError("Invalid rhyme context")
        requested_first = d["request"]["meter"]["first_line_rhymes"]
        if requested_first is not None and rhyme["first_line_rhymes"] is not requested_first:
            raise ValueError("Rhyme context conflicts with explicit user preference")
        requested_id = d["request"]["meter"]["template_id"]
        if requested_id is not None and d["template_id"] not in (None, requested_id):
            raise ValueError("Template id conflicts with explicit user preference")
        if not isinstance(d["structure"], list) or any(
            not isinstance(x, dict) or set(x) != {"unit", "intent"}
            or not _text(x["unit"]) or not _text(x["intent"]) for x in d["structure"]
        ):
            raise ValueError("Invalid structure records")
        if not isinstance(d["budget"], dict) or set(d["budget"]) != set(baseline["budget"]):
            raise ValueError("Invalid budget fields")
        for key, value in d["budget"].items():
            if type(value) is not int or value < (0 if key == "max_backtracks" else 1):
                raise ValueError("Invalid budget value")
        if not _score(d["quality_score"]):
            raise ValueError("Score must be null or a finite number between 0 and 1")
        checks = d["checks"]
        if not isinstance(checks, dict):
            raise ValueError("checks must be an object")
        if checks:
            PoemState._validate_assessment(checks, d["revision"])
            if BINDING_FIELDS & checks.keys():
                require_binding(checks, d)
        if not isinstance(d["candidate_history"], list):
            raise ValueError("candidate_history must be an array")
        ids = set()
        for c in d["candidate_history"]:
            if not isinstance(c, dict) or set(c) != {"id", "text", "line_index", "parent_path", "revision", "checks", "quality_score", "decision", "reason"}:
                raise ValueError("Invalid candidate record")
            if not _text(c["id"]) or c["id"] in ids or not _text(c["text"]):
                raise ValueError("Invalid or duplicate candidate id/text")
            ids.add(c["id"])
            if type(c["line_index"]) is not int or not 1 <= c["line_index"] <= d["form"]["line_count"]:
                raise ValueError("Invalid candidate line index")
            if type(c["revision"]) is not int or not 0 <= c["revision"] <= d["revision"]:
                raise ValueError("Invalid candidate revision")
            if c["parent_path"] is not None and not _text(c["parent_path"]):
                raise ValueError("Invalid parent path")
            if not _score(c["quality_score"]) or c["decision"] not in ("pending", "retained", "rejected") or not isinstance(c["reason"], str):
                raise ValueError("Invalid candidate evaluation")
            if not isinstance(c["checks"], dict):
                raise ValueError("Invalid candidate checks")
            if c["checks"]:
                PoemState._validate_assessment(c["checks"], c["revision"], full_poem=False)
                if BINDING_FIELDS & c["checks"].keys():
                    require_binding(c["checks"], d, c, historical=c['revision'] != d['revision'])
            if c["decision"] == "retained" and c["checks"].get("valid") is not True:
                raise ValueError("Retained candidates require passing checks")
        if d["status"] not in ("planning", "drafting", "reviewing", "completed"):
            raise ValueError("Invalid status")
        if d["status"] in ("reviewing", "completed") and len(d["lines"]) != d["form"]["line_count"]:
            raise ValueError("Review requires a full poem")
        if d["status"] == "completed" and (checks.get("valid") is not True or d["template_id"] is None or rhyme["group"] is None):
            raise ValueError("Completion requires current passing checks, template and rhyme group")
        if "input_parse" in d and not isinstance(d["input_parse"], dict):
            raise ValueError("Invalid input_parse metadata")
        if "meter_template" in d:
            from poem_templates import validate_template
            snapshot = d["meter_template"]
            if not isinstance(snapshot, dict) or set(snapshot) != {"data", "source_sha256"}:
                raise ValueError("Invalid meter template snapshot")
            digest = snapshot["source_sha256"]
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Invalid template source digest")
            t = snapshot["data"]
            validate_template(t)
            if not matches_meter(t, d["request"]["meter"]):
                raise ValueError("Template conflicts with explicit user meter preferences")
            if t["id"] != d["template_id"] or t["type"] != d["poem_type"] or t["rhyme"]["first_line_rhyme"] != rhyme["first_line_rhymes"]:
                raise ValueError("Template snapshot conflicts with state")
        try:
            json.dumps(d, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("State must be finite JSON data") from exc

    @staticmethod
    def _validate_assessment(result, revision, full_poem=True):
        base = {"revision", "valid", "checks", "errors", "warnings"}
        if (not isinstance(result, dict) or not base <= set(result) or set(result) - base - BINDING_FIELDS
                or type(result["revision"]) is not int or result["revision"] != revision
                or not any(result["valid"] is v for v in (True, False, None))
                or not isinstance(result["checks"], dict) or not result["checks"]
                or not _strings(result["errors"]) or not _strings(result["warnings"])):
            raise ValueError("Invalid or stale assessment")
        validate_binding_shape(result)
        statuses = []
        for item in result["checks"].values():
            if not isinstance(item, dict) or item.get("status") not in ("pass", "fail", "unknown", "not_run", "not_applicable"):
                raise ValueError("Invalid check status")
            if "required" in item and type(item["required"]) is not bool:
                raise ValueError("Check required must be a boolean")
            if item.get("required", True):
                statuses.append(item["status"])
        if any(result["checks"].get(key, {}).get("required", True) is False
               for key in ("length", "pingze", "rhyme", "reading")):
            raise ValueError("Core checks must be required")
        expected = False if "fail" in statuses else None if any(x in statuses for x in ("unknown", "not_run")) else True
        if result["valid"] is not expected:
            raise ValueError("Assessment summary disagrees with individual checks")
        if result["valid"] is True and (result["errors"] or not {"length", "pingze", "rhyme"} <= set(result["checks"])
                or any(result["checks"][key]["status"] != "pass" for key in ("length", "pingze"))
                or result["checks"]["rhyme"]["status"] not in (("pass",) if full_poem else ("pass", "not_applicable"))
                or ("reading" in result["checks"] and result["checks"]["reading"]["status"] != "pass")):
            raise ValueError("Passing assessment requires all core checks to pass")

    def _commit(self, data, invalidate=False):
        if invalidate:
            data["revision"] += 1
            data["checks"] = {}
            data["quality_score"] = None
            data["status"] = "drafting" if data["lines"] else "planning"
        self._validate(data)
        self._data = deepcopy(data)

    def set_plan(self, **changes):
        allowed = {"theme", "emotion", "imagery", "structure", "template_id", "rhyme", "constraints"}
        if set(changes) - allowed:
            raise ValueError("Unsupported plan field")
        changes = deepcopy(changes)
        rhyme = changes.get("rhyme")
        if (isinstance(rhyme, dict) and rhyme.get("system") == "平水韵"
                and isinstance(rhyme.get("group"), str) and rhyme["group"].strip()):
            from rhyme_lexicon import RhymeLexicon
            canonical = RhymeLexicon().normalize_group(rhyme["group"])
            if canonical is not None:
                rhyme["group"] = canonical
        data = self.to_dict()
        data.update(changes)
        if data != self._data:
            self._commit(data, invalidate=True)

    def set_budget(self, **changes):
        data = self.to_dict()
        data["budget"].update(changes)
        self._commit(data)

    def bind_template(self, template, source_sha256):
        """Bind a validated immutable-by-copy snapshot and invalidate prior checks."""
        from poem_templates import validate_template
        validate_template(template)
        data = self.to_dict()
        data["meter_template"] = {"data": deepcopy(template), "source_sha256": source_sha256}
        data["template_id"] = template["id"]
        data["rhyme"]["first_line_rhymes"] = template["rhyme"]["first_line_rhyme"]
        if data != self._data:
            self._commit(data, invalidate=True)

    def append_line(self, text):
        data = self.to_dict()
        data["lines"].append(text)
        self._commit(data, invalidate=True)

    def replace_line(self, line_index, text):
        if type(line_index) is not int or not 1 <= line_index <= len(self._data["lines"]):
            raise ValueError("line_index is one-based and must exist")
        data = self.to_dict()
        data["lines"][line_index - 1] = text
        self._commit(data, invalidate=True)

    def truncate_lines(self, keep):
        if type(keep) is not int or not 0 <= keep <= len(self._data["lines"]):
            raise ValueError("Invalid retained line count")
        data = self.to_dict()
        data["lines"] = data["lines"][:keep]
        self._commit(data, invalidate=True)

    def record_candidate(self, candidate_id, text, line_index, parent_path=None):
        data = self.to_dict()
        data["candidate_history"].append({
            "id": candidate_id, "text": text, "line_index": line_index,
            "parent_path": parent_path, "revision": data["revision"],
            "checks": {}, "quality_score": None, "decision": "pending", "reason": "",
        })
        self._commit(data)

    def evaluate_candidate(self, candidate_id, *, checks, quality_score=None, decision="pending", reason=""):
        data = self.to_dict()
        candidate = next((x for x in data["candidate_history"] if x["id"] == candidate_id), None)
        if candidate is None or candidate["revision"] != data["revision"]:
            raise ValueError("Candidate is absent or belongs to an earlier context")
        self._validate_assessment(checks, data['revision'], full_poem=False)
        require_binding(checks, data, candidate)
        candidate.update(checks=deepcopy(checks), quality_score=quality_score, decision=decision, reason=reason)
        self._commit(data)

    def record_candidate_batch(self, records):
        """Atomically register/evaluate a batch using the ordinary candidate API.

        Empty checks are allowed only for rejected, unscored input duplicates.
        No creative-context or poem assessment changes are made.
        """
        if not isinstance(records, list) or not records:
            raise ValueError("Candidate batch must be a nonempty array")
        staged = self.fork()
        fields = {"id", "text", "line_index", "parent_path", "checks",
                  "quality_score", "decision", "reason"}
        for record in records:
            if not isinstance(record, dict) or set(record) != fields:
                raise ValueError("Invalid batch candidate record")
            staged.record_candidate(record['id'], record['text'], record['line_index'], record['parent_path'])
            if record['checks']:
                staged.evaluate_candidate(record['id'], **{k: record[k] for k in
                    ('checks', 'quality_score', 'decision', 'reason')})
            else:
                if record['decision'] != 'rejected' or record['quality_score'] is not None:
                    raise ValueError("Unchecked batch items must be rejected and unscored")
                data = staged.to_dict()
                data['candidate_history'][-1].update(decision='rejected', reason=record['reason'])
                staged._commit(data)
        self._commit(staged.to_dict())

    def set_assessment(self, checks, quality_score=None):
        data = self.to_dict()
        self._validate_assessment(checks, data['revision'])
        require_binding(checks, data)
        data.update(checks=deepcopy(checks), quality_score=quality_score)
        self._commit(data)

    def set_status(self, status):
        transitions = {"planning": {"drafting"}, "drafting": {"reviewing"}, "reviewing": {"completed", "drafting"}, "completed": set()}
        if not isinstance(status, str) or status not in transitions[self._data["status"]]:
            raise ValueError("Invalid lifecycle transition")
        data = self.to_dict()
        if status == 'completed':
            require_binding(data['checks'], data)
        data["status"] = status
        self._commit(data)

