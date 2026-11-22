"""Build dossiers for the demo candidates in data/examples/.

    uv run python scripts/build_examples.py            # all examples
    uv run python scripts/build_examples.py Ivanov     # only folders whose name contains the text
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.examples import example_dirs, example_input  # noqa: E402
from app.pipeline import build_dossier  # noqa: E402


def main() -> None:
    flt = " ".join(sys.argv[1:]).lower()
    for folder in example_dirs():
        if flt and flt not in folder.name.lower():
            continue
        t = time.time()
        d = build_dossier(example_input(folder))
        m = d.metrics
        status = ", ".join(f"{s.name}:{s.status}" for s in d.sources)
        print(f"{folder.name:32} pubs={m.pubs_in_window:3} Q1/Q2={m.q1q2_any:3} first={m.first_author:3} "
              f"hScholar={m.h_index_scholar.value} hOA={m.h_index_openalex.value} "
              f"profile={d.profile.generated_by} ({time.time() - t:.1f}s)\n    {status}")


if __name__ == "__main__":
    main()
