"""Validate, select and bind user-supplied meter templates; no tone lookup."""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from request_options import normalize_meter, matches_meter

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "shi_ge_lv.json"


def validate_template(t):
    def require(condition, message):
        if not condition:
            raise ValueError(message)
    require(isinstance(t, dict), "Template must be an object")
    required = {"id", "sketch", "type", "pattern", "line_count", "char_count", "rhyme", "constraints", "tunes"}
    require(required <= set(t), "Missing template fields")
    forms = json.loads(Path(__file__).with_name("poem-forms.json").read_text(encoding="utf-8"))
    require(isinstance(t["type"], str) and t["type"] in forms, "Unsupported poem type")
    for key in ("id", "sketch", "pattern"):
        require(isinstance(t[key], str) and bool(t[key].strip()), f"Invalid {key}")
    form = forms[t["type"]]
    count, width = form["line_count"], form["line_length"]
    require(type(t["line_count"]) is int and t["line_count"] == count, "Wrong line count")
    require(type(t["char_count"]) is int and t["char_count"] == count * width, "Wrong char count")
    require(isinstance(t["constraints"], list) and all(isinstance(x, str) and x.strip() for x in t["constraints"]), "Invalid constraints")
    r = t["rhyme"]
    require(isinstance(r, dict) and {"type", "first_line_rhyme", "positions"} <= set(r), "Invalid rhyme fields")
    require(r["type"] == "平韵" and type(r["first_line_rhyme"]) is bool, "Unsupported rhyme mode")
    positions = ([1] if r["first_line_rhyme"] else []) + list(range(2, count + 1, 2))
    require(isinstance(r["positions"], list) and all(type(x) is int for x in r["positions"]) and r["positions"] == positions, "Rhyme positions disagree")
    require(t["pattern"] in [start + suffix for start in ("平起", "仄起") for suffix in ("首句入韵", "首句不入韵")], "Unsupported starting pattern")
    require(t["pattern"].endswith("首句入韵") == r["first_line_rhyme"], "Pattern/rhyme conflict")
    require(isinstance(t["tunes"], list) and len(t["tunes"]) == count * width, "Incomplete tune grid")
    grid = {}
    for cell in t["tunes"]:
        require(isinstance(cell, dict) and {"line", "pos", "tune"} <= set(cell), "Invalid tune cell")
        line, pos = cell["line"], cell["pos"]
        require(type(line) is int and type(pos) is int and 1 <= line <= count and 1 <= pos <= width, "Tune coordinate outside grid")
        require((line, pos) not in grid, "Duplicate tune coordinate")
        require(cell["tune"] in ("平", "仄", "中"), "Unsupported tone symbol")
        grid[line, pos] = cell["tune"]
        if pos == width:
            require(cell.get("rhythm") == ("韵" if line in positions else "句"), "Line ending marker disagrees with rhyme")
            require(cell["tune"] == ("平" if line in positions else "仄"), "Line ending tone disagrees with rhyme")
        else:
            require("rhythm" not in cell, "Unexpected interior rhythm marker")
    require(grid[1, 2] == t["pattern"][0], "Starting tone disagrees with pattern")
    # Check the fixed even-position skeleton: alternation, opposition and linkage.
    for line in range(1, count + 1):
        even = [grid[line, pos] for pos in range(2, width + 1, 2)]
        require(all(x in ("平", "仄") for x in even) and all(a != b for a, b in zip(even, even[1:])), "Invalid even-position alternation")
        if line > 1:
            for pos in range(2, width + 1, 2):
                require((grid[line, pos] == grid[line - 1, pos]) == (line % 2 == 1), "Invalid couplet opposition/linkage")
    return ["".join(grid[line, pos] for pos in range(1, width + 1)) for line in range(1, count + 1)]


class TemplateLibrary:
    def __init__(self, path=DEFAULT_PATH):
        self.path = Path(path)
        raw = self.path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(data, dict) or not data:
            raise ValueError("Template library must be a nonempty object")
        self._templates = {}
        for poem_type, group in data.items():
            if not isinstance(group, dict) or not isinstance(group.get("formats"), list) or not group["formats"]:
                raise ValueError("Invalid template group")
            for template in group["formats"]:
                validate_template(template)
                if template["type"] != poem_type or template["id"] in self._templates:
                    raise ValueError("Duplicate id or mismatched group")
                self._templates[template["id"]] = deepcopy(template)

    def candidates(self, poem_type=None, *, meter=None):
        meter = normalize_meter(meter)
        return [deepcopy(t) for t in self._templates.values()
                if (poem_type is None or t["type"] == poem_type) and matches_meter(t, meter)]

    def select(self, poem_type, *, template_id=None, pattern=None, start=None, first_line_rhymes=None):
        if template_id is not None and (not isinstance(template_id, str) or template_id not in self._templates):
            raise ValueError("Unknown template id")
        matches = self.candidates(poem_type, meter={"template_id": template_id, "pattern": pattern,
                                                  "start": start, "first_line_rhymes": first_line_rhymes})
        if len(matches) != 1:
            raise ValueError("Specify one matching template id or pattern; selection is ambiguous or absent")
        return deepcopy(matches[0])

    def list(self, poem_type=None):
        return [{"id": t["id"], "type": t["type"], "pattern": t["pattern"],
                 "lines": validate_template(t), "rhyme_positions": deepcopy(t["rhyme"]["positions"])}
                for t in self._templates.values() if poem_type is None or t["type"] == poem_type]

    def bind(self, state, *, template_id=None, pattern=None):
        data = state.to_dict()
        matches = self.candidates(data["poem_type"], meter=data["request"]["meter"])
        selected = normalize_meter({"template_id": template_id, "pattern": pattern})
        matches = [t for t in matches if matches_meter(t, selected)]
        if len(matches) != 1:
            raise ValueError("Template selection conflicts with user preferences or remains ambiguous")
        template = matches[0]
        state.bind_template(template, self.sha256)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=DEFAULT_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--poem-type")
    binding = commands.add_parser("bind")
    binding.add_argument("--state", type=Path, required=True)
    binding.add_argument("--output", type=Path, required=True)
    selector = binding.add_mutually_exclusive_group()
    selector.add_argument("--template-id")
    selector.add_argument("--pattern")
    args = parser.parse_args()
    try:
        library = TemplateLibrary(args.library)
        if args.command == "list":
            print(json.dumps(library.list(args.poem_type), ensure_ascii=False, indent=2))
        else:
            from poem_state import PoemState
            state = PoemState.load(args.state)
            library.bind(state, template_id=args.template_id, pattern=args.pattern)
            state.save(args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Template error: {exc}\n")


if __name__ == "__main__":
    main()
