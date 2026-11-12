"""Additional boundary tests; the supplied public tests stay unchanged."""

import json
import os
import time

import pytest
from tool_contract import MAX_OUTPUT_CHARS
from tools import ToolRegistry


def call(registry, name, **arguments):
    result = registry.execute(name, json.dumps(arguments))
    assert len(result.to_content()) <= MAX_OUTPUT_CHARS
    return result


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("read_file", {"path": "../outside.txt"}),
        ("read_file", {"path": "/etc/passwd"}),
        ("read_file", {"path": "../../workspace-sibling/outside.txt"}),
        ("read_file", {"path": "README.md", "start_line": True}),
        ("read_file", {"path": "README.md", "start_line": 0}),
        ("read_file", {"path": "README.md", "end_line": 1, "start_line": 2}),
        ("read_file", {"path": "README.md", "extra": "ignored?"}),
        ("edit_file", {"path": "ISSUE.md", "old_str": "March", "new_str": "April"}),
        ("edit_file", {"path": "src/new.py", "old_str": "x", "new_str": "y"}),
        (
            "edit_file",
            {"path": "src/tinyledger/ledger.py", "old_str": "", "new_str": "x"},
        ),
        ("run_tests", {"target": "src/tinyledger/ledger.py"}),
        ("run_tests", {"target": "-p evil"}),
        ("run_tests", {"target": "tests/test_money.py::test_name --help"}),
        ("run_cli", {"args": ["report", "../outside.csv"]}),
        ("run_cli", {"args": ["report", "data/sample.csv", "--month", "2026-13"]}),
        ("run_cli", {"args": ["report; cat secret", "data/sample.csv"]}),
        ("run_cli", {"args": ["report", "data/sample.csv", "--help"]}),
    ],
)
def test_invalid_or_unauthorized_requests(registry, name, arguments):
    assert not call(registry, name, **arguments).ok


@pytest.mark.parametrize("arguments", ["null", "[]", "true", "{", "0", "NaN"])
def test_nonobject_or_malformed_json(registry, arguments):
    assert not registry.execute("read_file", arguments).ok


def test_cli_rejects_external_symlink(registry, workspace):
    outside = workspace.parent / "outside.csv"
    outside.write_text("private external data")
    (workspace / "data/linked.csv").symlink_to(outside)
    result = call(registry, "run_cli", args=["report", "data/linked.csv"])
    assert not result.ok
    assert "private external data" not in result.to_content()


def test_reads_and_searches_skip_external_links(registry, workspace):
    outside = workspace.parent / "outside.txt"
    outside.write_text("synthetic-private-marker")
    (workspace / "src/leak.txt").symlink_to(outside)
    (workspace / "src/external-dir").symlink_to(
        workspace.parent, target_is_directory=True
    )
    assert not call(registry, "read_file", path="src/leak.txt").ok
    assert not call(registry, "read_file", path="src/external-dir/outside.txt").ok
    assert (
        "synthetic-private-marker"
        not in call(
            registry, "search_code", pattern="synthetic-private-marker"
        ).to_content()
    )
    assert "leak.txt" not in call(registry, "list_files").to_content()


def test_edit_cannot_follow_link_to_tests(registry, workspace):
    target = workspace / "tests/test_money.py"
    before = target.read_bytes()
    (workspace / "src/linked_test.py").symlink_to(target)
    assert not call(
        registry,
        "edit_file",
        path="src/linked_test.py",
        old_str="assert",
        new_str="pass",
    ).ok
    assert target.read_bytes() == before


def test_replaced_src_root_is_rejected(registry, workspace):
    (workspace / "src").rename(workspace / "original-src")
    (workspace / "src").symlink_to(workspace / "tests", target_is_directory=True)
    assert not call(
        registry,
        "edit_file",
        path="src/test_money.py",
        old_str="assert",
        new_str="pass",
    ).ok
    assert not call(registry, "run_tests").ok


def test_test_target_link_is_rejected(registry, workspace):
    target = workspace.parent / "test_external.py"
    target.write_text("raise RuntimeError('must not execute')")
    (workspace / "tests/test_link.py").symlink_to(target)
    assert not call(registry, "run_tests", target="tests/test_link.py").ok


def test_trace_failure_returns_error(registry, workspace):
    registry.trace_path = workspace / "README.md/trace.jsonl"
    result = call(registry, "list_files")
    assert not result.ok
    assert "may have executed" in result.error


def test_serialized_errors_and_escaped_text_are_bounded(registry, workspace):
    result = registry.execute("x" * 100_000, "{}")
    assert not result.ok and len(result.to_content()) <= MAX_OUTPUT_CHARS
    (workspace / "src/escaped.txt").write_text('"\\' * 10000)
    result = call(registry, "read_file", path="src/escaped.txt")
    assert result.ok and result.truncated


def test_large_result_keeps_structured_summary(registry, workspace):
    for number in range(100):
        (workspace / f"tests/test_failure_{number}.py").write_text(
            "def test_bad():\n    assert False, 'x' * 5000\n"
        )
    result = call(registry, "run_tests")
    # Pytest's output/report resource limits may stop this adversarial workload.
    if result.ok:
        assert isinstance(result.output, dict)
        assert result.output["failed"] >= 100
        assert result.truncated
    else:
        assert "size limit" in result.error


def test_test_failures_are_not_tool_failures(registry):
    result = call(registry, "run_tests")
    assert result.ok
    assert result.output["exit_code"] == 1
    assert result.output["failed"] == 2
    assert result.output["passed"] == 23


def test_subprocess_does_not_inherit_credentials(registry, workspace, monkeypatch):
    monkeypatch.setenv("UPB_AI_GATEWAY_KEY", "synthetic-api-key")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--broken-flag")
    (workspace / "tests/test_environment.py").write_text(
        "import os\ndef test_environment():\n"
        "    assert 'UPB_AI_GATEWAY_KEY' not in os.environ\n"
        "    assert 'PYTEST_ADDOPTS' not in os.environ\n"
    )
    result = call(registry, "run_tests", target="tests/test_environment.py")
    assert result.ok and result.output["passed"] == 1
    assert "synthetic-api-key" not in result.to_content()


def test_hanging_test_times_out(workspace):
    registry = ToolRegistry(workspace, timeout_s=0.3)
    (workspace / "tests/test_hang.py").write_text(
        "import time\ndef test_hang():\n    time.sleep(30)\n"
    )
    started = time.monotonic()
    result = call(registry, "run_tests", target="tests/test_hang.py")
    assert not result.ok and "timeout" in result.error.lower()
    assert time.monotonic() - started < 5


def test_flooding_cli_is_stopped(registry, workspace):
    (workspace / "src/tinyledger/__main__.py").write_text(
        "import os\nwhile True:\n    os.write(1, b'x' * 8192)\n"
    )
    started = time.monotonic()
    result = call(registry, "run_cli", args=["report", "data/sample.csv"])
    assert not result.ok and result.truncated
    assert "size limit" in result.error
    assert time.monotonic() - started < 5


def test_catastrophic_regex_times_out(workspace):
    registry = ToolRegistry(workspace, timeout_s=0.05)
    (workspace / "src/long.txt").write_text("a" * 100000 + "!")
    started = time.monotonic()
    result = call(registry, "search_code", pattern="(a+)+$", glob="long.txt")
    assert not result.ok and "timeout" in result.error.lower()
    assert time.monotonic() - started < 3


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO test")
def test_fifo_is_not_opened(registry, workspace):
    os.mkfifo(workspace / "fifo")
    assert not call(registry, "read_file", path="fifo").ok


def test_schemas_cannot_mutate_validation(registry):
    schemas = registry.schemas()
    schemas[1]["function"]["parameters"]["additionalProperties"] = True
    assert not call(registry, "read_file", path="README.md", unwanted=True).ok


def test_trace_includes_rejected_call_and_bounded_result(registry):
    call(registry, "read_file", path="../not-permitted")
    row = json.loads(registry.trace_path.read_text().splitlines()[-1])
    assert row["tool"] == "read_file"
    assert row["result"]["ok"] is False
    assert row["ms"] >= 0
