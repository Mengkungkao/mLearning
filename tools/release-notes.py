#!/usr/bin/env python3
"""Print the CHANGELOG.md section of a version (the GitHub release notes)."""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def notes(version: str) -> str:
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r"^## \[" + re.escape(version) + r"\][^\n]*\n(.*?)(?=^## \[|\Z)", text,
                      re.S | re.M)
    return match.group(1).strip() if match else f"mLearning {version}"


if __name__ == "__main__":
    print(notes(sys.argv[1]))
