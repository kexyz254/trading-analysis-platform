"""Run the complete ATS test scope in deterministic Python processes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from collections.abc import Sequence


ROOT = Path(__file__).resolve().parents[1]


def run_pytest(arguments: Sequence[str]) -> None:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--disable-warnings",
        *arguments,
    ]

    print("\n$", " ".join(command), flush=True)

    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
    )

    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> None:
    # Preserve the original CI scope: only the repository's primary tests/.
    # Run the affected data-access suites separately to prevent legacy
    # test-session import contamination.
    run_pytest(
        [
            "tests",
            "--ignore=tests/test_wallet_balance.py",
            "--ignore=tests/test_webapp_data_access.py",
        ]
    )

    run_pytest(
        [
            "tests/test_wallet_balance.py",
            "tests/test_webapp_data_access.py",
        ]
    )


if __name__ == "__main__":
    main()
