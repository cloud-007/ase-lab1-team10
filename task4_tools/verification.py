"""Independent checks: trusted CSV arithmetic, protected files, and actual tests.

No workspace module is imported into the host process. Repository Python runs
only through the same limited subprocess tools used by the agent.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from tools import ToolRegistry

IGNORED = {".git", "__pycache__", ".pytest_cache"}


def snapshot(root: Path) -> dict[str, str]:
    """Hash the small lab fixture, excluding generated metadata."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if not (set(path.relative_to(root).parts) & IGNORED)
        and path.name != ".tool-trace.jsonl"
        and path.is_file()
        and not path.is_symlink()
    }


def verify_workspace(
    registry: ToolRegistry, baseline: dict[str, str], reference_csv: Path
) -> dict:
    """Verify tests and every sample month against independently parsed dates."""
    tests = registry.execute("run_tests", "{}")
    test_output = tests.output if tests.ok else {}
    tests_pass = bool(
        tests.ok
        and test_output.get("collected", 0) > 0
        and test_output.get("exit_code") == 0
        and test_output.get("failed") == 0
        and test_output.get("errors") == 0
    )

    expected = defaultdict(lambda: {"dates": [], "total_cents": 0})
    with reference_csv.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter=";"):
            day, month, year = map(int, row["date"].split("."))
            parsed = date(year, month, day)
            record = expected[parsed.strftime("%Y-%m")]
            record["dates"].append(parsed.isoformat())
            record["total_cents"] += int(
                Decimal(row["amount"].replace(".", "").replace(",", ".")) * 100
            )
    expected["2026-01"]  # An empty month must contain no expenses.
    reports = {}
    for month, values in sorted(expected.items()):
        result = registry.execute(
            "run_cli",
            json.dumps(
                {
                    "args": ["report", "data/sample.csv", "--month", month],
                }
            ),
        )
        output = result.output if result.ok else {}
        text = output.get("stdout", "")
        total = re.search(r"^Total: (\d+),(\d{2}) €$", text, re.MULTILINE)
        total_cents = int(total[1]) * 100 + int(total[2]) if total else None
        dates = re.findall(r"^(\d{4}-\d{2}-\d{2})\s", text, re.MULTILINE)
        reports[month] = {
            "ok": bool(
                result.ok
                and not result.truncated
                and output.get("exit_code") == 0
                and total_cents == values["total_cents"]
                and dates == sorted(values["dates"])
            ),
            "expected_total_cents": values["total_cents"],
            "actual_total_cents": total_cents,
            "expected_dates": sorted(values["dates"]),
            "actual_dates": dates,
        }
    current = snapshot(registry.root)
    changes = sorted(
        name
        for name in baseline.keys() | current.keys()
        if baseline.get(name) != current.get(name)
    )
    protected_changes = [name for name in changes if not name.startswith("src/")]
    return {
        "verified": tests_pass
        and all(r["ok"] for r in reports.values())
        and not protected_changes,
        "tests": {"ok": tests.ok, "output": tests.output, "error": tests.error},
        "reports": reports,
        "changed_files": changes,
        "protected_changes": protected_changes,
        "final_hashes": current,
    }
