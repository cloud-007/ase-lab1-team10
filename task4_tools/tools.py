"""Validated tools for the lab workspace, with explicit execution limits.

These checks constrain tool requests. They are not an OS sandbox for Python
code executed by pytest or tinyledger; see README.md for that boundary.
"""

from __future__ import annotations

import copy
import difflib
import fnmatch
import json
import math
import os
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import regex
from tool_contract import (
    DEFAULT_TIMEOUT_S,
    MAX_LIST_ENTRIES,
    MAX_OUTPUT_CHARS,
    MAX_READ_LINES,
    MAX_SEARCH_RESULTS,
    ToolRegistryBase,
    ToolResult,
)

SKIP = {".git", "__pycache__", ".pytest_cache", ".venv"}
MAX_FILE_BYTES = 1_000_000
MAX_ARGUMENT_CHARS = 64_000
MAX_PROCESS_BYTES = 40_000
MAX_REPORT_BYTES = 256_000


def _schema(name: str, description: str, properties: dict, required=()) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _shrink(value: Any) -> Any:
    """Shrink the largest payload component without discarding summary fields."""
    if isinstance(value, str):
        return value[: len(value) // 2]
    if isinstance(value, list):
        return value[: len(value) // 2] if len(value) > 1 else []
    if isinstance(value, dict) and value:
        key = max(value, key=lambda k: len(_encoded(value[k])))
        return {**value, key: _shrink(value[key])}
    return None


def bounded(result: ToolResult) -> ToolResult:
    """Limit the final wire representation, including escaped text and errors."""
    while len(result.to_content()) > MAX_OUTPUT_CHARS:
        result.truncated = True
        if result.ok:
            result.output = _shrink(result.output)
        else:
            result.error = str(result.error)[: len(str(result.error)) // 2]
    return result


class ToolRegistry(ToolRegistryBase):
    def __init__(
        self,
        workspace: Path,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        *,
        trace_path: Path | None = None,
    ) -> None:
        super().__init__(workspace=workspace, timeout_s=timeout_s)
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")
        self.root = self.workspace.resolve()
        # Test fixture registries must not append to a submitted experiment trace.
        self.trace_path = trace_path or self.root / ".tool-trace.jsonl"
        path = {"type": "string", "minLength": 1, "maxLength": 1024}
        self._schemas = [
            _schema(
                "list_files",
                "List workspace-relative files, capped at 500 entries; no writes.",
                {"path": path, "pattern": {"type": "string", "maxLength": 256}},
            ),
            _schema(
                "read_file",
                "Read numbered lines, capped at 200 lines; no writes.",
                {
                    "path": path,
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                ["path"],
            ),
            _schema(
                "search_code",
                "Search bounded text files with a regex; return path:line matches.",
                {
                    "pattern": {"type": "string", "minLength": 1, "maxLength": 256},
                    "glob": {"type": "string", "maxLength": 256},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_SEARCH_RESULTS,
                    },
                },
                ["pattern"],
            ),
            _schema(
                "run_tests",
                "Execute the suite or one tests/*.py[::test_name] target; return counts and failures.",
                {"target": path},
            ),
            _schema(
                "run_cli",
                "Execute tinyledger report/balances/settle on an existing workspace CSV, optionally --month YYYY-MM.",
                {
                    "args": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "items": {"type": "string", "maxLength": 1024},
                    }
                },
                ["args"],
            ),
            _schema(
                "edit_file",
                "Replace exactly one nonempty old_str in an existing src/ file. Tests/config are read-only.",
                {
                    "path": path,
                    "old_str": {"type": "string", "minLength": 1, "maxLength": 20000},
                    "new_str": {"type": "string", "maxLength": 20000},
                },
                ["path", "old_str", "new_str"],
            ),
        ]
        self._validators = {
            s["function"]["name"]: jsonschema.Draft202012Validator(
                s["function"]["parameters"]
            )
            for s in self._schemas
        }

    def schemas(self) -> list[dict]:
        return copy.deepcopy(self._schemas)

    def _safe_path(self, relative: str) -> Path:
        if not relative or Path(relative).is_absolute():
            raise ValueError("Use a nonempty workspace-relative path.")
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("Path escapes workspace, possibly through a symlink.")
        return candidate

    def _source_root(self) -> Path:
        source = self.root / "src"
        if source.is_symlink() or not source.is_dir():
            raise ValueError("src/ must be a real directory inside the workspace.")
        return source

    def execute(
        self, name: str, arguments: str, *, tool_call_id: str | None = None
    ) -> ToolResult:
        started = time.perf_counter()
        try:
            if not isinstance(name, str) or name not in self._validators:
                raise ValueError("Unknown tool: " + str(name)[:200])
            if not isinstance(arguments, str) or len(arguments) > MAX_ARGUMENT_CHARS:
                raise ValueError(
                    "Arguments must be a JSON string of at most 64000 characters."
                )
            args = json.loads(arguments)
            error = next(self._validators[name].iter_errors(args), None)
            if error is not None:
                # Do not echo attacker-controlled argument values in validation errors.
                raise ValueError(
                    f"Invalid arguments at {list(error.path)}: failed {error.validator} constraint."
                )
            result = bounded(self._run(name, args))
        except Exception as exc:  # noqa: BLE001 - contract converts every tool failure
            result = bounded(
                ToolResult(
                    ok=False,
                    error=f"{type(exc).__name__}: {str(exc)[:MAX_OUTPUT_CHARS]}",
                )
            )
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "tool": str(name)[:200],
            "args": arguments[:MAX_ARGUMENT_CHARS]
            if isinstance(arguments, str)
            else "[not a JSON string]",
            "ok": result.ok,
            "error": result.error,
            "truncated": result.truncated,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "result": json.loads(result.to_content()),
        }
        if tool_call_id is not None:
            record["tool_call_id"] = tool_call_id
        try:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            with self.trace_path.open("a", encoding="utf-8") as stream:
                stream.write(_encoded(record) + "\n")
        except Exception:  # noqa: BLE001 - logging must not violate execute's contract
            # An edit/process may already have happened: never suggest retrying it blindly.
            return ToolResult(
                ok=False,
                error="Trace logging failed; action may have executed. Stop and inspect state.",
            )
        return result

    def _files(self, base: Path):
        for directory, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in SKIP and not (Path(directory) / d).is_symlink()
            )
            for name in sorted(files):
                path = Path(directory) / name
                if path == self.trace_path or name == ".tool-trace.jsonl":
                    continue
                try:
                    resolved = self._safe_path(path.relative_to(self.root).as_posix())
                    if resolved.is_file():
                        yield path.relative_to(self.root).as_posix(), resolved
                except (OSError, ValueError):
                    continue

    def _text(self, path: Path) -> tuple[str, bool]:
        if not path.is_file():
            raise ValueError("Path must name a regular file.")
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        return data[:MAX_FILE_BYTES].decode("utf-8", errors="replace"), len(
            data
        ) > MAX_FILE_BYTES

    def _process(self, command: list[str], report: Path | None = None) -> dict:
        """Capture bounded pipes; kill the process group on timeout/output overflow."""
        source = self._source_root()
        with tempfile.TemporaryDirectory(prefix="tinyledger-process-") as home:
            env = {
                "PATH": os.defpath,
                "HOME": home,
                "TMPDIR": home,
                "PYTHONPATH": str(source),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONIOENCODING": "utf-8",
            }
            outputs = {"stdout": bytearray(), "stderr": bytearray()}
            reason = None
            deadline = time.monotonic() + self.timeout_s
            with subprocess.Popen(
                command,
                cwd=self.root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            ) as process:
                try:
                    with selectors.DefaultSelector() as selector:
                        for name in outputs:
                            selector.register(
                                getattr(process, name), selectors.EVENT_READ, name
                            )
                        while selector.get_map():
                            if time.monotonic() >= deadline:
                                reason = "Process timeout"
                                break
                            if (
                                report
                                and report.exists()
                                and report.stat().st_size > MAX_REPORT_BYTES
                            ):
                                reason = "Test report exceeded size limit"
                                break
                            for key, _ in selector.select(
                                min(0.05, max(0, deadline - time.monotonic()))
                            ):
                                chunk = os.read(key.fileobj.fileno(), 8192)
                                if not chunk:
                                    selector.unregister(key.fileobj)
                                    continue
                                available = MAX_PROCESS_BYTES - sum(
                                    map(len, outputs.values())
                                )
                                outputs[key.data].extend(chunk[:available])
                                if len(chunk) > available:
                                    reason = "Process output exceeded size limit"
                                    break
                            if reason:
                                break
                        if not reason:
                            try:
                                process.wait(
                                    timeout=max(0.001, deadline - time.monotonic())
                                )
                            except subprocess.TimeoutExpired:
                                reason = "Process timeout"
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
            return {
                "exit_code": process.returncode,
                **{
                    k: bytes(v).decode("utf-8", errors="replace")
                    for k, v in outputs.items()
                },
                "interrupted": reason,
            }

    def _tests(self, target: str | None) -> ToolResult:
        tests = self._safe_path("tests")
        if (self.root / "tests").is_symlink() or not tests.is_dir():
            raise ValueError("tests/ must be a real workspace directory.")
        selected = str(tests)
        if target is not None:
            file, *selectors_ = target.split("::")
            path = self._safe_path(file)
            if (
                not path.is_relative_to(tests)
                or not path.is_file()
                or path.suffix != ".py"
            ):
                raise ValueError(
                    "Target must be an existing Python file beneath tests/."
                )
            if any(
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\[[A-Za-z0-9_.-]+\])?", s)
                for s in selectors_
            ):
                raise ValueError("Invalid pytest node selector.")
            selected = str(path) + "".join("::" + s for s in selectors_)
        with tempfile.TemporaryDirectory(prefix="tinyledger-report-") as directory:
            report = Path(directory) / "pytest.xml"
            process = self._process(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-o",
                    "addopts=",
                    "-p",
                    "no:cacheprovider",
                    "--tb=short",
                    f"--junitxml={report}",
                    selected,
                ],
                report,
            )
            if process["interrupted"]:
                return ToolResult(
                    ok=False,
                    error=process["interrupted"],
                    truncated="size limit" in process["interrupted"],
                )
            if not report.exists() or report.stat().st_size > MAX_REPORT_BYTES:
                return ToolResult(
                    ok=False, error="Pytest did not produce a bounded report."
                )
            xml = ET.parse(report).getroot()
            suites = [xml] if xml.tag == "testsuite" else list(xml.iter("testsuite"))
            counts = {
                key: sum(int(s.get(key, 0)) for s in suites)
                for key in ("tests", "failures", "errors", "skipped")
            }
            failures = []
            for case in xml.iter("testcase"):
                failure = case.find("failure")
                if failure is None:
                    failure = case.find("error")
                if failure is not None:
                    failures.append(
                        {
                            "test": case.get("name", ""),
                            "traceback": (failure.text or failure.get("message", ""))[
                                :1000
                            ],
                        }
                    )
            return ToolResult(
                ok=True,
                output={
                    "exit_code": process["exit_code"],
                    "collected": counts["tests"],
                    "passed": max(
                        0,
                        counts["tests"]
                        - counts["failures"]
                        - counts["errors"]
                        - counts["skipped"],
                    ),
                    "failed": counts["failures"],
                    "errors": counts["errors"],
                    "skipped": counts["skipped"],
                    "failures": failures[:3],
                    "failures_omitted": max(0, len(failures) - 3),
                },
                truncated=len(failures) > 3,
            )

    def _cli(self, args: list[str]) -> ToolResult:
        if args[0] not in {"report", "balances", "settle"}:
            raise ValueError("CLI command must be report, balances, or settle.")
        path = self._safe_path(args[1])
        if not path.is_file() or path.suffix.lower() != ".csv":
            raise ValueError("CLI input must be an existing workspace CSV file.")
        if len(args) != 2 and (
            len(args) != 4
            or args[2] != "--month"
            or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", args[3])
        ):
            raise ValueError("Use command csv_file [--month YYYY-MM].")
        process = self._process(
            [sys.executable, "-m", "tinyledger", args[0], str(path), *args[2:]]
        )
        if process["interrupted"]:
            return ToolResult(
                ok=False,
                error=process["interrupted"],
                truncated="size limit" in process["interrupted"],
            )
        return ToolResult(
            ok=True, output={k: process[k] for k in ("exit_code", "stdout", "stderr")}
        )

    def _run(self, name: str, args: dict) -> ToolResult:
        if name == "list_files":
            base = self._safe_path(args.get("path", "."))
            if not base.is_dir():
                raise ValueError("Not a directory.")
            matches = []
            for relative, _ in self._files(base):
                if (
                    not args.get("pattern")
                    or fnmatch.fnmatch(relative, args["pattern"])
                    or fnmatch.fnmatch(Path(relative).name, args["pattern"])
                ):
                    matches.append(relative)
                    if len(matches) > MAX_LIST_ENTRIES:
                        break
            return ToolResult(
                ok=True,
                output=matches[:MAX_LIST_ENTRIES],
                truncated=len(matches) > MAX_LIST_ENTRIES,
            )
        if name == "read_file":
            path = self._safe_path(args["path"])
            text, clipped = self._text(path)
            start, requested_end = args.get("start_line", 1), args.get("end_line")
            if requested_end is not None and requested_end < start:
                raise ValueError("end_line must not precede start_line.")
            lines = text.splitlines()
            end = min(
                len(lines), requested_end if requested_end is not None else len(lines)
            )
            shown_end = min(end, start + MAX_READ_LINES - 1)
            return ToolResult(
                ok=True,
                output="\n".join(
                    f"{i}: {lines[i - 1]}" for i in range(start, shown_end + 1)
                ),
                truncated=clipped or shown_end < end,
            )
        if name == "search_code":
            expression = regex.compile(args["pattern"])
            deadline = time.monotonic() + self.timeout_s
            matches, clipped = [], False
            limit = args.get("max_results", 50)
            for relative, path in self._files(self.root):
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Search timeout; narrow the glob or simplify the regex."
                    )
                if args.get("glob") and not (
                    fnmatch.fnmatch(relative, args["glob"])
                    or fnmatch.fnmatch(path.name, args["glob"])
                ):
                    continue
                text, cut = self._text(path)
                clipped |= cut
                for number, line in enumerate(text.splitlines(), 1):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            "Search timeout; narrow the glob or simplify the regex."
                        )
                    if expression.search(line, timeout=remaining):
                        matches.append(f"{relative}:{number}: {line.strip()}")
                        if len(matches) > limit:
                            return ToolResult(
                                ok=True, output=matches[:limit], truncated=True
                            )
            return ToolResult(ok=True, output=matches, truncated=clipped)
        if name == "run_tests":
            return self._tests(args.get("target"))
        if name == "run_cli":
            return self._cli(args["args"])
        if name == "edit_file":
            path = self._safe_path(args["path"])
            if not path.is_relative_to(self._source_root()) or not path.is_file():
                raise ValueError("Edits only allowed on existing files beneath src/.")
            text, clipped = self._text(path)
            if clipped:
                raise ValueError("File exceeds safe edit size limit.")
            if text.count(args["old_str"]) != 1:
                raise ValueError(
                    f"Expected exactly one old_str match, found {text.count(args['old_str'])}."
                )
            updated = text.replace(args["old_str"], args["new_str"], 1)
            path.write_text(updated, encoding="utf-8")
            diff = "".join(
                difflib.unified_diff(
                    text.splitlines(True),
                    updated.splitlines(True),
                    args["path"],
                    args["path"],
                )
            )
            return ToolResult(ok=True, output=diff)
        raise ValueError("Unknown tool")
