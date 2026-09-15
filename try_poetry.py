"""Launch the portable write-poetry terminal experience."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from poetry_experience import main

if __name__ == "__main__":
    main()

