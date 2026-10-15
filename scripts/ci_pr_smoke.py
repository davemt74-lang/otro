#!/usr/bin/env python3
"""Lint changed Python, JS and JSON sources before broad regression gates.

The PR base must be the exact GitHub SHA. Missing base fails closed.
No third-party dependency installs or network calls.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys


def checks(paths: list[str]) -> list[list[str]]:
    tasks: list[list[str]] = []
    for name in sorted(set(paths)):
        p = Path(name)
        if not p.parts or p.is_absolute() or ".." in p.parts:
            raise ValueError("Unsafe source path from Git diff")
        if not p.is_file():
            continue
        if name.endswith(".py"):
            tasks.append([sys.executable, "-m", "py_compile", name])
        elif name.endswith((".js", ".mjs", ".cjs")):
            tasks.append(["node", "--check", name])
        elif name.endswith(".json"):
            tasks.append([sys.executable, "-c",
                          "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))", name])
    return tasks


def diff_files(base: str) -> list[str]:
    if not re.fullmatch(r"[a-fA-F0-9]{40}", base):
        raise ValueError("Missing exact PR base SHA")
    raw = subprocess.check_output(
        ["git", "diff", "-z", "--name-only", "--diff-filter=ACMR", base + "...HEAD"])
    return [part.decode("utf-8", errors="surrogateescape")
            for part in raw.split(b"\0") if part]


def main() -> None:
    commands = checks(diff_files(os.environ.get("BASE_SHA", "")))
    for cmd in commands:
        print("CI-G1 checking:", cmd[-1], flush=True)
        subprocess.run(cmd, check=True)
    print(f"CI-G1: {len(commands)} changed Python/JS/JSON files validated")


if __name__ == "__main__":
    main()
