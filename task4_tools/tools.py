from __future__ import annotations
import difflib, fnmatch, json, os, re, subprocess, sys, tempfile, time, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
import jsonschema
from tool_contract import (
    DEFAULT_TIMEOUT_S, MAX_LIST_ENTRIES, MAX_OUTPUT_CHARS,
    MAX_READ_LINES, MAX_SEARCH_RESULTS, ToolRegistryBase, ToolResult,
)

SKIP = {".git", "__pycache__", ".pytest_cache", ".venv"}

def _schema(name: str, desc: str, props: dict, req: list[str] | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req or [], "additionalProperties": False}}}

class ToolRegistry(ToolRegistryBase):
    def __init__(self, workspace: Path, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        super().__init__(workspace=workspace, timeout_s=timeout_s)
        self.root = self.workspace.resolve()
        self.trace_path = Path(__file__).resolve().parent / "logs" / "trace.jsonl"
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._schemas = [
            _schema("list_files", "List relative file paths.", {"path": {"type": "string"}, "pattern": {"type": "string"}}),
            _schema("read_file", "Read numbered lines from a file.", {"path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1}}, ["path"]),
            _schema("search_code", "Regex search across files.", {"pattern": {"type": "string"}, "glob": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_RESULTS}}, ["pattern"]),
            _schema("run_tests", "Run pytest and return pass/fail summary.", {"target": {"type": "string"}}),
            _schema("run_cli", "Run python -m tinyledger <args>.", {"args": {"type": "array", "items": {"type": "string"}}}, ["args"]),
            _schema("edit_file", "Replace unique old_str in a file below src/.", {"path": {"type": "string"},
                    "old_str": {"type": "string"}, "new_str": {"type": "string"}}, ["path", "old_str", "new_str"]),
        ]
        self._validators = {s["function"]["name"]: jsonschema.Draft202012Validator(s["function"]["parameters"]) for s in self._schemas}

    def schemas(self) -> list[dict]:
        return self._schemas

    def _safe_path(self, rel: str) -> Path:
        if not rel or Path(rel).is_absolute():
            raise ValueError("Invalid or absolute path.")
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("Path escapes workspace.")
        return p

    def execute(self, name: str, arguments: str) -> ToolResult:
        t0 = time.perf_counter()
        try:
            if name not in self._validators:
                res = ToolResult(ok=False, error=f"Unknown tool: {name}")
            else:
                args = json.loads(arguments)
                errs = [e.message for e in self._validators[name].iter_errors(args)] if isinstance(args, dict) else ["Args must be object"]
                res = ToolResult(ok=False, error="; ".join(errs)) if errs else self._run(name, args)
        except Exception as exc:
            res = ToolResult(ok=False, error=str(exc))

        if len(res.to_content()) > MAX_OUTPUT_CHARS:
            out = res.output if isinstance(res.output, str) else json.dumps(res.output)
            res = ToolResult(ok=res.ok, output=out[:MAX_OUTPUT_CHARS - 300] + "...[truncated]", error=res.error, truncated=True)

        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "tool": name, "args": arguments,
                                "ok": res.ok, "error": res.error, "truncated": res.truncated,
                                "ms": round((time.perf_counter() - t0) * 1000, 1)}) + "\n")
        return res

    def _run(self, name: str, a: dict) -> ToolResult:
        env = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"}
        if name == "list_files":
            base = self._safe_path(a.get("path", "."))
            if not base.is_dir():
                return ToolResult(ok=False, error="Not a directory.")
            out = []
            for r, dirs, files in os.walk(base, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in SKIP)
                for fn in sorted(files):
                    fp = (Path(r) / fn).resolve()
                    if fp.is_relative_to(self.root) and fp.is_file():
                        rel = (Path(r) / fn).relative_to(self.root).as_posix()
                        if not a.get("pattern") or fnmatch.fnmatch(rel, a["pattern"]) or fnmatch.fnmatch(fn, a["pattern"]):
                            out.append(rel)
            return ToolResult(ok=True, output=out[:MAX_LIST_ENTRIES], truncated=len(out) > MAX_LIST_ENTRIES)

        if name == "read_file":
            fp = self._safe_path(a["path"])
            if not fp.is_file():
                return ToolResult(ok=False, error="File not found.")
            s, e = a.get("start_line", 1), a.get("end_line")
            if e is not None and e < s:
                return ToolResult(ok=False, error="end_line < start_line.")
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
            end = min(len(lines), e if e is not None else len(lines))
            trunc = (end - s + 1) > MAX_READ_LINES
            end = s + MAX_READ_LINES - 1 if trunc else end
            return ToolResult(ok=True, output="\n".join(f"{i}: {lines[i-1]}" for i in range(s, end + 1)), truncated=trunc)

        if name == "search_code":
            rx, limit, hits = re.compile(a["pattern"]), a.get("max_results", 50), []
            for r, dirs, files in os.walk(self.root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in SKIP)
                for fn in sorted(files):
                    fp = (Path(r) / fn).resolve()
                    if not fp.is_relative_to(self.root) or not fp.is_file():
                        continue
                    rel = (Path(r) / fn).relative_to(self.root).as_posix()
                    if a.get("glob") and not (fnmatch.fnmatch(rel, a["glob"]) or fnmatch.fnmatch(fn, a["glob"])):
                        continue
                    for idx, line in enumerate(fp.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        if rx.search(line):
                            hits.append(f"{rel}:{idx}: {line.strip()}")
                            if len(hits) >= limit:
                                return ToolResult(ok=True, output=hits, truncated=True)
            return ToolResult(ok=True, output=hits, truncated=False)

        if name == "run_tests":
            cmd = [sys.executable, "-m", "pytest", "--tb=short"]
            if a.get("target"):
                t = a["target"]
                if t.startswith("-") or not self._safe_path(t.split("::", 1)[0]).exists():
                    return ToolResult(ok=False, error="Invalid test target.")
                cmd.append(t)
            with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
                xml_path = Path(tmp.name)
            try:
                p = subprocess.run(cmd + [f"--junitxml={xml_path}"], cwd=self.root, env=env, capture_output=True, text=True, timeout=self.timeout_s)
                if not xml_path.exists() or xml_path.stat().st_size == 0:
                    return ToolResult(ok=True, output={"passed": 0, "failed": 0, "errors": 1, "details": (p.stderr or p.stdout)[-2000:]})
                root_el = ET.parse(xml_path).getroot()
                suites = [root_el] if root_el.tag == "testsuite" else list(root_el.findall("testsuite"))
                tot = sum(int(s.attrib.get("tests", 0)) for s in suites)
                fail = sum(int(s.attrib.get("failures", 0)) for s in suites)
                err = sum(int(s.attrib.get("errors", 0)) for s in suites)
                skip = sum(int(s.attrib.get("skipped", 0)) for s in suites)
                fails = []
                for tc in root_el.iter("testcase"):
                    n = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
                    if n is not None:
                        fails.append({"test": tc.attrib.get("name", ""), "traceback": (n.text or n.attrib.get("message", ""))[:1000]})
                return ToolResult(ok=True, output={"passed": max(0, tot - fail - err - skip), "failed": fail, "errors": err, "failures": fails})
            finally:
                xml_path.unlink(missing_ok=True)

        if name == "run_cli":
            bad = ";|&$\n\r" + chr(96)
            if any(any(c in arg for c in bad) or ".." in Path(arg).parts or Path(arg).is_absolute() for arg in a["args"]):
                return ToolResult(ok=False, error="Unsafe CLI argument.")
            p = subprocess.run([sys.executable, "-m", "tinyledger", *a["args"]], cwd=self.root, env=env, capture_output=True, text=True, timeout=self.timeout_s)
            return ToolResult(ok=True, output={"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr})

        if name == "edit_file":
            fp = self._safe_path(a["path"])
            if not a["old_str"] or not fp.is_relative_to((self.root / "src").resolve()) or not fp.is_file():
                return ToolResult(ok=False, error="Edits only allowed on existing files below src/.")
            txt = fp.read_text(encoding="utf-8")
            if txt.count(a["old_str"]) != 1:
                return ToolResult(ok=False, error=f"Expected 1 match, found {txt.count(a['old_str'])}.")
            new_txt = txt.replace(a["old_str"], a["new_str"], 1)
            fp.write_text(new_txt, encoding="utf-8")
            return ToolResult(ok=True, output="".join(difflib.unified_diff(txt.splitlines(True), new_txt.splitlines(True), a["path"], a["path"])))
        return ToolResult(ok=False, error="Unknown tool.")