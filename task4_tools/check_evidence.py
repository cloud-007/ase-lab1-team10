"""Read-only audit of a reviewed run's calls, hashes, and reproducible patch."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from tool_contract import MAX_OUTPUT_CHARS
from verification import snapshot

HERE = Path(__file__).resolve().parent


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def check_run(directory: Path) -> dict:
    metadata = json.loads((directory / "run.json").read_text())
    verification = json.loads((directory / "verification.json").read_text())
    require(
        metadata["verified"] and verification["verified"],
        "Run was not independently verified",
    )
    require(
        metadata["initial_hashes"] == snapshot(HERE / "tinyledger"),
        "Initial state was not the original starter",
    )
    for name, expected in metadata["implementation_hashes"].items():
        path = (HERE.parent / name).resolve()
        require(
            path.is_relative_to(HERE.parent),
            "Implementation hash path escapes repository",
        )
        require(
            hashlib.sha256(path.read_bytes()).hexdigest() == expected,
            f"Implementation changed since this run: {name}",
        )
    records = read_rows(directory / "llm.jsonl")
    chats = [record for record in records if record.get("type") == "chat"]
    calls = [call for chat in chats for call in chat["response"].get("tool_calls", [])]
    events = [record for record in records if record.get("event") == "tool_result"]
    trace = read_rows(directory / "trace.jsonl")
    conversation_results = [
        message
        for message in chats[-1]["request"]["messages"]
        if message["role"] == "tool"
    ]
    require(
        len(calls)
        == len(events)
        == len(trace)
        == len(conversation_results)
        == metadata["loop"]["tool_calls"],
        "Native calls, results, and trace counts disagree",
    )
    for call, event, entry, message in zip(calls, events, trace, conversation_results):
        require(
            (call["id"], call["function"]["name"], call["function"]["arguments"])
            == (entry["tool_call_id"], entry["tool"], entry["args"]),
            "Trace call does not match model request",
        )
        require(
            event["tool_call_id"] == entry["tool_call_id"]
            and event["content"] == entry["result"],
            "Trace result does not match conversation result",
        )
        require(
            message["tool_call_id"] == entry["tool_call_id"]
            and json.loads(message["content"]) == entry["result"],
            "Model conversation does not contain the recorded tool result",
        )
        require(
            len(json.dumps(entry["result"], ensure_ascii=False)) <= MAX_OUTPUT_CHARS,
            "Oversized serialized tool result",
        )
    require(
        all(chat["model"] == metadata["model"] for chat in chats),
        "Model changed during run",
    )
    require(
        all(
            chat["request"]["tool_choice"] == "auto"
            and chat["request"]["max_tokens"] == metadata["configuration"]["max_tokens"]
            and "temperature" not in chat["request"]
            for chat in chats
        ),
        "Request parameters disagree with run configuration",
    )
    require(
        sum(chat["usage"]["prompt_tokens"] or 0 for chat in chats)
        == metadata["usage"]["prompt_tokens"],
        "Prompt usage differs from run summary",
    )
    require(
        sum(chat["usage"]["completion_tokens"] or 0 for chat in chats)
        == metadata["usage"]["completion_tokens"],
        "Completion usage differs from run summary",
    )
    patch = (directory / "final.diff").read_bytes()
    require(
        hashlib.sha256(patch).hexdigest() == metadata["diff_sha256"],
        "Final diff hash mismatch",
    )
    require(not verification["protected_changes"], "Protected files changed")
    with tempfile.TemporaryDirectory(prefix="tinyledger-patch-audit-") as temporary:
        workspace = Path(temporary) / "workspace"
        shutil.copytree(
            HERE / "tinyledger",
            workspace,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
        )
        subprocess.run(
            ["git", "-C", str(workspace), "apply", "--check", "-"],
            input=patch,
            check=True,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "-C", str(workspace), "apply", "-"],
            input=patch,
            check=True,
            capture_output=True,
            timeout=10,
        )
        require(
            snapshot(workspace) == verification["final_hashes"],
            "Applying the submitted patch does not reproduce the verified files",
        )
    return {
        "mode": metadata["mode"],
        "model": metadata["model"],
        "native_calls": len(calls),
        "rejected_calls": sum(not entry["ok"] for entry in trace),
        "passed_tests": verification["tests"]["output"]["passed"],
        "march_total_cents": verification["reports"]["2026-03"]["actual_total_cents"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=HERE / "logs")
    args = parser.parse_args()
    try:
        result = check_run(args.directory.resolve())
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Evidence audit failed: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
