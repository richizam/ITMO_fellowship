"""Download sample applications from a Google Drive folder into data/examples/.

The folder contains applicants' personal data, so its link is not stored in the repository:
pass it as an argument or set EXAMPLES_DRIVE_URL in .env.

    uv run python scripts/fetch_examples.py <folder-url>
"""
from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "examples"


def main() -> None:
    import gdown  # dev dependency

    import os

    from dotenv import load_dotenv

    load_dotenv(OUT.parents[1] / ".env")
    url = sys.argv[1] if len(sys.argv) > 1 else os.getenv("EXAMPLES_DRIVE_URL", "")
    if not url:
        sys.exit("usage: fetch_examples.py <google-drive-folder-url>  (or set EXAMPLES_DRIVE_URL)")
    OUT.mkdir(parents=True, exist_ok=True)
    gdown.download_folder(url=url, output=str(OUT), quiet=False)
    print("saved to", OUT)


if __name__ == "__main__":
    main()
