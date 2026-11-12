"""Saved experiment evidence must reproduce the verified files and conversation."""

import json
import shutil
from pathlib import Path

import pytest
from check_evidence import check_run

MOCK = Path(__file__).resolve().parents[1] / "logs/mock"


def test_saved_mock_evidence_is_reproducible():
    summary = check_run(MOCK)
    assert summary["native_calls"] == 18
    assert summary["rejected_calls"] == 6
    assert summary["passed_tests"] == 25


@pytest.mark.parametrize("artifact", ["trace.jsonl", "final.diff", "run.json"])
def test_tampered_evidence_is_rejected(tmp_path, artifact):
    shutil.copytree(MOCK, tmp_path / "run")
    path = tmp_path / "run" / artifact
    if artifact == "trace.jsonl":
        rows = path.read_text().splitlines()
        first = json.loads(rows[0])
        first["tool_call_id"] = "tampered"
        rows[0] = json.dumps(first)
        path.write_text("\n".join(rows) + "\n")
    elif artifact == "run.json":
        data = json.loads(path.read_text())
        data["initial_hashes"] = {}
        path.write_text(json.dumps(data))
    else:
        path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        check_run(tmp_path / "run")
