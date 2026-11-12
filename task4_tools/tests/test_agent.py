"""Exercise orchestration using the supplied SDK-compatible fake client."""

import json

import pytest
from agent import main, run_loop
from mock_model import MockOpenAIClient, _Function, _Message, _ToolCall
from tools import ToolRegistry
from verification import snapshot, verify_workspace

from common.llm_client import LLMClient


def client(tmp_path, script):
    model = MockOpenAIClient(script=script)
    llm = LLMClient(model="mock", openai_client=model, log_path=tmp_path / "llm.jsonl")
    return llm, model


def test_loop_preserves_tool_ids_errors_and_followup(workspace, tmp_path):
    llm, model = client(
        tmp_path,
        [
            ("malformed", "read_file", "{"),
            ("recover", "read_file", '{"path":"ISSUE.md"}'),
        ],
    )
    registry = ToolRegistry(workspace, trace_path=tmp_path / "trace.jsonl")
    outcome = run_loop(llm, registry)
    assert outcome.reason == "model_finished" and outcome.tool_calls == 2
    messages = model.received[-1]["messages"]
    results = [message for message in messages if message["role"] == "tool"]
    assert [r["tool_call_id"] for r in results] == ["call_001", "call_002"]
    assert not json.loads(results[0]["content"])["ok"]
    assert json.loads(results[1]["content"])["ok"]
    trace = [json.loads(row) for row in registry.trace_path.read_text().splitlines()]
    assert [r["tool_call_id"] for r in trace] == ["call_001", "call_002"]
    assert llm.last_finish_reason == "stop"
    assert all(
        request["tool_choice"] == "auto" and request["max_tokens"] == 8192
        for request in model.received
    )
    assert all("temperature" not in request for request in model.received)


def test_step_and_token_budgets(workspace, tmp_path):
    llm, _ = client(tmp_path, [("look", "list_files", "{}")])
    registry = ToolRegistry(workspace)
    assert run_loop(llm, registry, max_steps=1).reason == "step_budget"
    llm.total_prompt_tokens = 100
    assert run_loop(llm, registry, max_total_tokens=100).reason == "token_budget"


class BatchClient:
    total_prompt_tokens = 0
    total_completion_tokens = 0
    last_finish_reason = "tool_calls"

    def __init__(self, calls):
        self.calls = calls
        self.events = []

    def chat_with_tools(self, messages, **kwargs):
        self.messages = messages
        return _Message(None, self.calls)

    def log_event(self, event):
        self.events.append(event)


def test_multiple_calls_execute_sequentially(workspace, tmp_path):
    path = workspace / "src/tinyledger/money.py"
    original = path.read_text()
    calls = [
        _ToolCall(
            "edit",
            _Function(
                "edit_file",
                json.dumps(
                    {
                        "path": "src/tinyledger/money.py",
                        "old_str": '"""Money helpers.',
                        "new_str": '"""Revised helpers.',
                    }
                ),
            ),
        ),
        _ToolCall("read", _Function("read_file", '{"path":"src/tinyledger/money.py"}')),
    ]
    llm = BatchClient(calls)
    result = run_loop(
        llm, ToolRegistry(workspace, trace_path=tmp_path / "trace.jsonl"), max_steps=1
    )
    assert result.reason == "step_budget"
    assert "Revised helpers" in llm.events[1]["content"]["output"]
    assert path.read_text() != original
    assert [m["tool_call_id"] for m in llm.messages if m["role"] == "tool"] == [
        "edit",
        "read",
    ]


def test_over_budget_calls_get_correlated_rejections(workspace, tmp_path):
    calls = [_ToolCall(str(i), _Function("list_files", "{}")) for i in range(3)]
    llm = BatchClient(calls)
    registry = ToolRegistry(workspace, trace_path=tmp_path / "trace.jsonl")
    outcome = run_loop(llm, registry, max_tool_calls=1)
    assert outcome.reason == "tool_budget"
    assert [e["content"]["ok"] for e in llm.events] == [True, False, False]
    assert len(registry.trace_path.read_text().splitlines()) == 3


@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_incomplete_model_response_is_not_executed(workspace, reason):
    llm = BatchClient([_ToolCall("1", _Function("list_files", "{}"))])
    llm.last_finish_reason = reason
    assert run_loop(llm, ToolRegistry(workspace)).reason == "incomplete_or_refused"
    assert not llm.events


def test_empty_response_is_not_success(workspace):
    llm = BatchClient([])
    llm.last_finish_reason = "stop"
    assert run_loop(llm, ToolRegistry(workspace)).reason == "empty_response"


def test_false_success_does_not_pass_independent_verification(workspace, tmp_path):
    baseline = snapshot(workspace)
    llm, _ = client(tmp_path, [])  # Scripted final answer falsely claims success.
    registry = ToolRegistry(workspace)
    assert run_loop(llm, registry).reason == "model_finished"
    result = verify_workspace(registry, baseline, workspace / "data/sample.csv")
    assert not result["verified"]
    assert result["tests"]["output"]["failed"] == 2
    assert result["reports"]["2026-03"]["actual_total_cents"] == 19740
    assert result["reports"]["2026-03"]["expected_total_cents"] == 36230


def test_mock_fixes_all_three_bugs_and_preserves_protected_files(workspace, tmp_path):
    baseline = snapshot(workspace)
    llm = LLMClient(
        model="mock", openai_client=MockOpenAIClient(), log_path=tmp_path / "llm.jsonl"
    )
    registry = ToolRegistry(workspace, trace_path=tmp_path / "trace.jsonl")
    assert run_loop(llm, registry).reason == "model_finished"
    verification = verify_workspace(registry, baseline, workspace / "data/sample.csv")
    assert verification["verified"]
    assert len(verification["changed_files"]) == 3
    assert verification["protected_changes"] == []


def test_existing_evidence_is_never_overwritten(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text("existing evidence\n")
    with pytest.raises(SystemExit) as exc:
        main(["--mock", "--output-dir", str(tmp_path)])
    assert exc.value.code == 2
    assert trace.read_text() == "existing evidence\n"
