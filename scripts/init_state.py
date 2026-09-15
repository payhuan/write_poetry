"""Initialize PoemState from a structured request using only the standard library."""

import argparse
import json
from pathlib import Path


from poem_state import PoemState


def initialize(request):
    """Backward-compatible dictionary interface."""
    return PoemState.create(request).to_dict()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        state = initialize(json.loads(args.input.read_text(encoding="utf-8-sig")))
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(state, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Initialization failed: {exc}\n")


if __name__ == "__main__":
    main()
