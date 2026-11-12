"""Custom JSON-protocol coding agent and independently testable action handlers."""

from __future__ import annotations

import json
import argparse
import inspect
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.embed_images import embed_images  # noqa: E402
from common.llm_client import LLMClient, count_tokens  # noqa: E402
from common.site_checker import check_site, member_names_from_content  # noqa: E402
from task2_own_agent import rag  # noqa: E402

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}
HTML_BLOCK_RE = re.compile(r"```html\s*\r?\n(.*?)```", re.IGNORECASE | re.DOTALL)

SYSTEM_PROMPT = """You are a small coding agent that creates a single-file team website.
Work iteratively using only the JSON action protocol and the four actions below.
Retrieve all website style rules from the style guide when needed; do not assume
rules from memory. Do not put style-guide rules in your response unless they were
returned by search_style_guide. Read the team profiles and use their details.
Never generate base64 image data; reference available files with placeholders such
as {{image:anna.jpg}}. The write_site action checks your saved page; fix failures
and write again. Finish only after a passing report.

For every step return exactly one JSON object with these three fields:
{"thought":"short reasoning","action":"action_name","args":{}}
Allowed actions and arguments:
- search_style_guide: {"query": "..."}
- read_profiles: {}
- write_site: {} followed immediately by a ```html code block containing the page,
  or write_site: {"html":"complete HTML document"}
- finish: {"summary":"..."}
Do not use markdown fences around the JSON object. For write_site, the optional HTML
fence comes after the closing JSON object. Use one action per step and wait for its
observation before deciding what to do next."""

DEFAULT_TASK_DESCRIPTION = """Create a complete, polished website for this team using the supplied member profiles.
Begin by reading all member profiles and noting the available image files. Search the
style guide for the requirements relevant to each major part of the page; rely on
those retrieved documents rather than guessing about its design rules.

Build a complete, responsive team website in one standalone HTML file. Include each
member, their study background, interests, fun fact, view on AI/software engineering,
future outlook and hot take from their profile. Use image placeholders referencing
the provided image files, never base64 data. Include the interactive page behavior
expected by the site's technical checker. Create a cohesive team introduction and
shared section about the future of software engineering.

Write the site, inspect the complete validation report, and correct every reported
failure. Repeat until all checks pass, then call finish. The finished HTML must be
saved at the configured output path."""


def _error(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "observation": f"Error: {message}", **details}


def _chunk_value(chunk: Any, name: str, default: Any = None) -> Any:
    if isinstance(chunk, dict):
        return chunk.get(name, default)
    return getattr(chunk, name, default)


def _chunk_dict(chunk: Any) -> dict[str, Any]:
    if hasattr(chunk, "as_dict"):
        return chunk.as_dict()
    if isinstance(chunk, dict):
        return dict(chunk)
    values = getattr(chunk, "__dict__", None)
    if isinstance(values, dict):
        return dict(values)
    return {
        "document_id": _chunk_value(chunk, "document_id", _chunk_value(chunk, "id")),
        "text": str(chunk),
    }


def _format_chunks(chunks: list[Any]) -> str:
    parts = []
    for chunk in chunks:
        document_id = _chunk_value(
            chunk, "document_id", _chunk_value(chunk, "id", "unknown source")
        )
        title = _chunk_value(chunk, "title", "")
        status = _chunk_value(chunk, "status", "active")
        heading = _chunk_value(chunk, "heading")
        text = _chunk_value(chunk, "text", "")
        suffix = f" — {title}" if title else ""
        suffix += f" / {heading}" if heading else ""
        parts.append(f"[{document_id} | {str(status).upper()}{suffix}]\n{text}")
    return "\n\n---\n\n".join(parts)


class ActionHandlers:
    """Implement the four required actions with injectable, test-friendly helpers."""

    def __init__(
        self,
        content_dir: str | Path,
        out_path: str | Path,
        images_dir: str | Path | None = None,
        search_k: int = 5,
        rag_module: Any = rag,
        image_embedder: Callable[[str, Path], str] = embed_images,
        site_checker: Callable[..., Any] = check_site,
    ) -> None:
        self.content_dir = Path(content_dir)
        self.out_path = Path(out_path)
        self.images_dir = (
            Path(images_dir) if images_dir is not None else self.content_dir / "images"
        )
        if search_k < 1:
            raise ValueError("search_k must be at least 1")
        self.search_k = search_k
        self.rag = rag_module
        self.image_embedder = image_embedder
        self.site_checker = site_checker
        self.latest_checker_report: Any | None = None

    @staticmethod
    def _validate_args(args: Any, allowed: set[str]) -> str | None:
        if not isinstance(args, dict):
            return "args must be a JSON object"
        unexpected = sorted(set(args) - allowed)
        if unexpected:
            return f"unexpected argument(s): {', '.join(unexpected)}"
        return None

    def search_style_guide(self, args: Any) -> dict[str, Any]:
        """Search current style-guide rules and return source-cited chunks."""
        error = self._validate_args(args, {"query"})
        if error:
            return _error(error)
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return _error("query must be a non-empty string")

        try:
            chunks = self.rag.search(query.strip(), k=self.search_k)
            # Normal RAG search filters deprecated material, but also filter here so
            # alternative/injected implementations cannot return obsolete guidance.
            active_chunks = [
                chunk
                for chunk in chunks
                if str(_chunk_value(chunk, "status", "active")).lower() == "active"
            ]
            formatter = getattr(self.rag, "format_observation", None)
            observation = (
                formatter(active_chunks)
                if callable(formatter)
                else _format_chunks(active_chunks)
            )

            # Defensively enforce the observation cap even with injected RAG objects.
            selected: list[Any] = []
            for chunk in active_chunks:
                candidate = [*selected, chunk]
                formatted = (
                    formatter(candidate)
                    if callable(formatter)
                    else _format_chunks(candidate)
                )
                if count_tokens(str(formatted)) <= 1_000:
                    selected.append(chunk)
            observation = (
                formatter(selected) if callable(formatter) else _format_chunks(selected)
            )
            return {
                "ok": True,
                "chunks": [_chunk_dict(chunk) for chunk in selected],
                "observation": observation
                or "No matching active style-guide rules were found.",
                "token_count": count_tokens(str(observation)),
            }
        except Exception as exc:
            return _error(f"style-guide search failed: {exc}")

    def read_profiles(self, args: Any) -> dict[str, Any]:
        """Read member profile Markdown and list available image files."""
        error = self._validate_args(args, set())
        if error:
            return _error(error)
        if not self.content_dir.is_dir():
            return _error(f"profile directory does not exist: {self.content_dir}")

        profiles = []
        try:
            for path in sorted(self.content_dir.glob("*.md")):
                if path.name == "_template.md":
                    continue
                profiles.append(
                    {
                        "name": path.stem,
                        "file": path.name,
                        "text": path.read_text(encoding="utf-8"),
                    }
                )

            image_files: list[str] = []
            if self.images_dir.is_dir():
                image_files = sorted(
                    path.name
                    for path in self.images_dir.iterdir()
                    if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
                )
            if not profiles:
                return {
                    "ok": True,
                    "profiles": [],
                    "images": image_files,
                    "observation": f"No profile Markdown files found in {self.content_dir} (excluding _template.md).",
                    "warning": "profile directory is empty",
                }
            return {
                "ok": True,
                "profiles": profiles,
                "images": image_files,
                "observation": json.dumps(
                    {"profiles": profiles, "available_images": image_files},
                    ensure_ascii=False,
                ),
            }
        except OSError as exc:
            return _error(f"could not read profiles or images: {exc}")

    @staticmethod
    def _html_from_response(
        args: dict[str, Any], raw_response: str | None
    ) -> tuple[str | None, str | None]:
        arg_html = args.get("html")
        if arg_html is not None and (
            not isinstance(arg_html, str) or not arg_html.strip()
        ):
            return None, "args.html must be a non-empty string when provided"
        if arg_html is not None and raw_response:
            try:
                stripped = raw_response.lstrip()
                _, json_end = json.JSONDecoder().raw_decode(stripped)
                if HTML_BLOCK_RE.match(stripped[json_end:].lstrip()):
                    return (
                        None,
                        "provide HTML either in args.html or in the following ```html block, not both",
                    )
            except json.JSONDecodeError:
                pass

        code_html = None
        if raw_response:
            # The protocol is a JSON action followed immediately by the optional
            # HTML fence. Decode the JSON prefix first so unrelated fenced text
            # elsewhere in a response cannot be mistaken for the requested page.
            stripped = raw_response.lstrip()
            try:
                _, json_end = json.JSONDecoder().raw_decode(stripped)
                trailer = stripped[json_end:].lstrip()
            except json.JSONDecodeError:
                trailer = ""
            match = HTML_BLOCK_RE.match(trailer)
            if match:
                code_html = match.group(1).strip()

        html = arg_html if arg_html is not None else code_html
        if not html:
            return (
                None,
                "HTML was not provided; use args.html or an immediately following ```html code block",
            )
        if "<html" not in html.lower() and "<!doctype html" not in html.lower():
            return None, "extracted content does not look like a complete HTML document"
        return html, None

    def write_site(self, args: Any, raw_response: str | None = None) -> dict[str, Any]:
        """Embed image placeholders, save the HTML, and run the full site checker."""
        error = self._validate_args(args, {"html"})
        if error:
            return _error(error)
        # Any new write invalidates the previous acceptance state, including a write
        # attempt that fails before the checker can run.
        self.latest_checker_report = None
        html, extraction_error = self._html_from_response(args, raw_response)
        if extraction_error:
            return _error(extraction_error)

        try:
            embedded_html = (
                self.image_embedder(html, self.images_dir) if html is not None else ""
            )
        except FileNotFoundError as exc:
            return _error(f"could not resolve image placeholder: {exc}")
        except ValueError as exc:
            return _error(f"could not embed image placeholder: {exc}")
        except OSError as exc:
            return _error(f"could not read image files: {exc}")

        if re.search(r"\{\{\s*image:", embedded_html):
            return _error(
                "one or more image placeholders remain unresolved after embedding"
            )

        try:
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            self.out_path.write_text(embedded_html, encoding="utf-8")
            member_names = (
                member_names_from_content(self.content_dir)
                if self.content_dir.is_dir()
                else None
            )
            report = self.site_checker(
                embedded_html,
                mode="full",
                member_names=member_names,
                file_size=self.out_path.stat().st_size,
            )
            self.latest_checker_report = report
            report_dict = report.to_dict() if hasattr(report, "to_dict") else report
            summary = (
                report.summary() if hasattr(report, "summary") else str(report_dict)
            )
            return {
                "ok": True,
                "saved_to": str(self.out_path),
                "passed": bool(
                    getattr(
                        report,
                        "passed",
                        (
                            report_dict.get("passed", False)
                            if isinstance(report_dict, dict)
                            else False
                        ),
                    )
                ),
                "report": report_dict,
                "summary": summary,
                "observation": summary,
            }
        except (OSError, ValueError, TypeError) as exc:
            return _error(f"could not save or check website: {exc}")
        except Exception as exc:
            return _error(f"site checker failed: {exc}")

    def finish(self, args: Any) -> dict[str, Any]:
        """Accept completion only after the most recent site check passed."""
        error = self._validate_args(args, {"summary"})
        if error:
            return _error(error)
        summary = args.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return _error("summary must be a non-empty string")

        report = self.latest_checker_report
        if report is None:
            return {
                "ok": False,
                "accepted": False,
                "observation": "Finish rejected: no website has been checked yet. Use write_site first.",
            }
        if not bool(getattr(report, "passed", False)):
            failures = (
                report.failures() if callable(getattr(report, "failures", None)) else []
            )
            failure_lines = [
                f"[FAIL] {getattr(item, 'name', 'check')}: {getattr(item, 'message', str(item))}"
                for item in failures
            ]
            if not failure_lines and hasattr(report, "summary"):
                failure_lines = [report.summary(only_failures=True)]
            return {
                "ok": False,
                "accepted": False,
                "failures": failure_lines,
                "observation": "Finish rejected: the latest website check did not pass.\n"
                + "\n".join(failure_lines),
            }
        return {
            "ok": True,
            "accepted": True,
            "summary": summary.strip(),
            "observation": f"Finish accepted: {summary.strip()}",
        }

    def handle(
        self, action: Any, args: Any, raw_response: str | None = None
    ) -> dict[str, Any]:
        """Dispatch a protocol action, returning a useful error for unknown actions."""
        handlers = {
            "search_style_guide": self.search_style_guide,
            "read_profiles": self.read_profiles,
            "write_site": lambda value: self.write_site(
                value, raw_response=raw_response
            ),
            "finish": self.finish,
        }
        if not isinstance(action, str) or action not in handlers:
            return _error(f"unknown action: {action!r}")
        return handlers[action](args)


class AgentLoop:
    """Run the custom JSON action protocol without native tool/function calling."""

    def __init__(
        self,
        llm: Any,
        handlers: ActionHandlers,
        task_description: str = DEFAULT_TASK_DESCRIPTION,
        max_steps: int = 25,
        max_tokens: int | None = 12_000,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], Any] = print,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if not isinstance(task_description, str) or not task_description.strip():
            raise ValueError("task_description must be a non-empty string")
        self.llm = llm
        self.handlers = handlers
        self.task_description = task_description.strip()
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.input_fn = input_fn
        self.output_fn = output_fn
        self.messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self.task_description},
        ]
        self.step = 0

    def _log_event(self, event: dict[str, Any]) -> None:
        logger = getattr(self.llm, "log_event", None)
        if callable(logger):
            try:
                logger(event)
            except Exception as exc:
                # Logging should not terminate a run if a custom/fake client lacks a
                # writable log destination.
                self.output_fn(f"Warning: could not write agent log event: {exc}")

    def _communicate(self) -> str:
        """Send the conversation using only the plain chat API."""
        kwargs: dict[str, Any] = {}
        chat = self.llm.chat
        if self.max_tokens is not None:
            try:
                parameters = inspect.signature(chat).parameters.values()
                accepts_max_tokens = any(
                    parameter.name == "max_tokens"
                    or parameter.kind == inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters
                )
            except (TypeError, ValueError):
                accepts_max_tokens = True
            if accepts_max_tokens:
                kwargs["max_tokens"] = self.max_tokens
        return chat(self.messages, **kwargs)

    @staticmethod
    def parse_response(raw_response: Any) -> tuple[dict[str, Any] | None, str | None]:
        """Parse one JSON action, allowing only a trailing HTML fence after JSON."""
        if (
            raw_response is None
            or not isinstance(raw_response, str)
            or not raw_response.strip()
        ):
            return (
                None,
                "The model returned an empty response. Return one JSON action object.",
            )

        raw = raw_response.lstrip()
        try:
            payload, end = json.JSONDecoder().raw_decode(raw)
        except json.JSONDecodeError as exc:
            likely_truncated = (
                exc.pos >= max(0, len(raw) - 2)
                or raw.count("{") > raw.count("}")
                or raw.count('"') % 2 == 1
            )
            reason = (
                "The response appears incomplete or truncated (possibly the token limit)."
                if likely_truncated
                else "The response is not valid JSON."
            )
            return (
                None,
                f"{reason} Return exactly one complete JSON object with thought, action, and args. Details: {exc.msg}.",
            )

        if not isinstance(payload, dict):
            return (
                None,
                "The response JSON must be one object with thought, action, and args.",
            )
        expected = {"thought", "action", "args"}
        missing = sorted(expected - set(payload))
        if missing:
            return (
                None,
                f"The JSON action is missing required field(s): {', '.join(missing)}.",
            )
        extra = sorted(set(payload) - expected)
        if extra:
            return (
                None,
                f"The JSON action has unexpected field(s): {', '.join(extra)}. Return exactly thought, action, and args.",
            )
        if not isinstance(payload["thought"], str):
            return None, "The thought field must be a string."
        if not isinstance(payload["action"], str) or not payload["action"].strip():
            return None, "The action field must be a non-empty string."
        if not isinstance(payload["args"], dict):
            return None, "The args field must be a JSON object."

        trailer = raw[end:].strip()
        if trailer.startswith("```html") and not HTML_BLOCK_RE.fullmatch(trailer):
            return (
                None,
                "The HTML code block appears incomplete or truncated (possibly the token limit). Return a complete ```html block.",
            )
        if trailer and not HTML_BLOCK_RE.fullmatch(trailer):
            return (
                None,
                "Unexpected text after the JSON action; only a following ```html block is allowed.",
            )
        if trailer and payload["action"] != "write_site":
            return (
                None,
                "An HTML code block may follow the JSON action only for write_site.",
            )
        return payload, None

    def _record_request(self, step: int) -> None:
        """Persist the exact conversation sent to the model before making the call."""
        for message_index, message in enumerate(self.messages):
            self._log_event(
                {
                    "type": "llm_message_sent",
                    "step": step,
                    "message_index": message_index,
                    "role": message.get("role"),
                    "content": message.get("content", ""),
                    "message": dict(message),
                }
            )
        self._log_event(
            {
                "type": "llm_request",
                "step": step,
                "messages": list(self.messages),
                "max_tokens": self.max_tokens,
            }
        )

    def _record_response(self, step: int, raw_response: Any) -> None:
        """Persist the complete model response, including malformed/truncated text."""
        self._log_event(
            {
                "type": "llm_response",
                "step": step,
                "response": (
                    raw_response
                    if isinstance(raw_response, str)
                    else repr(raw_response)
                ),
                "response_was_string": isinstance(raw_response, str),
            }
        )

    def _append_observation(
        self,
        step: int,
        observation: Any,
        action: str | None = None,
        action_args: dict[str, Any] | None = None,
    ) -> None:
        if isinstance(observation, dict):
            content = str(
                observation.get(
                    "observation", json.dumps(observation, ensure_ascii=False)
                )
            )
            status = observation.get("ok", True)
        else:
            content = str(observation)
            status = None
        message = {
            "role": "user",
            "content": f"Observation (step {step}{', ' + action if action else ''}):\n{content}",
        }
        self.messages.append(message)
        self._log_event(
            {
                "type": "agent_observation",
                "step": step,
                "action": action,
                "action_args": action_args,
                "ok": status,
                "observation": content,
                "observation_data": observation,
                "appended_message": message,
            }
        )

    def run(self) -> dict[str, Any]:
        """Run until finish is accepted, feedback ends the run, or steps are exhausted."""
        self._log_event(
            {
                "type": "run_started",
                "max_steps": self.max_steps,
                "max_tokens": self.max_tokens,
                "task_description": self.task_description,
            }
        )
        while self.step < self.max_steps:
            self.step += 1
            step = self.step
            self._record_request(step)
            try:
                raw_response = self._communicate()
            except Exception as exc:
                message = f"LLM communication failed: {type(exc).__name__}: {exc}"
                self.output_fn(f"Step {step}: {message}")
                self._log_event(
                    {
                        "type": "llm_error",
                        "step": step,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                self._append_observation(step, {"ok": False, "observation": message})
                continue

            self._record_response(step, raw_response)
            action_data, parse_error = self.parse_response(raw_response)
            if parse_error:
                self.output_fn(f"Step {step}: {parse_error}")
                if raw_response:
                    self.messages.append(
                        {"role": "assistant", "content": str(raw_response)}
                    )
                self._log_event(
                    {
                        "type": "invalid_response",
                        "step": step,
                        "response": (
                            raw_response
                            if isinstance(raw_response, str)
                            else repr(raw_response)
                        ),
                        "error": parse_error,
                    }
                )
                self._append_observation(
                    step, {"ok": False, "observation": parse_error}
                )
                continue

            assert action_data is not None
            self.messages.append({"role": "assistant", "content": str(raw_response)})
            action = action_data["action"].strip()
            try:
                observation = self.handlers.handle(
                    action, action_data["args"], raw_response=str(raw_response)
                )
            except Exception as exc:
                observation = _error(
                    f"action handler failed: {type(exc).__name__}: {exc}"
                )
            if not isinstance(observation, dict):
                observation = _error(
                    "action handler returned an invalid observation; expected an object"
                )

            self._log_event(
                {
                    "type": "action_execution",
                    "step": step,
                    "action": action,
                    "action_args": action_data["args"],
                    "result": observation,
                }
            )
            if not observation.get("ok", True) or not observation.get("passed", True):
                self._log_event(
                    {
                        "type": "action_error",
                        "step": step,
                        "action": action,
                        "action_args": action_data["args"],
                        "error": observation.get("observation", "Action failed"),
                        "result": observation,
                    }
                )
            self._append_observation(
                step, observation, action=action, action_args=action_data["args"]
            )
            if action == "finish" and observation.get("accepted"):
                self.output_fn(observation["observation"])
                try:
                    feedback = self.input_fn("Feedback (press Enter to finish): ")
                except (EOFError, KeyboardInterrupt):
                    feedback = ""
                if not feedback.strip():
                    self._log_event(
                        {
                            "type": "human_feedback",
                            "step": step,
                            "feedback": "",
                            "continues": False,
                        }
                    )
                    self._log_event(
                        {"type": "run_finished", "step": step, "accepted": True}
                    )
                    return {
                        "status": "finished",
                        "accepted": True,
                        "steps": step,
                        "messages": self.messages,
                    }
                feedback = feedback.strip()
                feedback_message = {
                    "role": "user",
                    "content": f"Human feedback: {feedback}",
                }
                self.messages.append(feedback_message)
                self._log_event(
                    {
                        "type": "human_feedback",
                        "step": step,
                        "feedback": feedback,
                        "continues": True,
                        "appended_message": feedback_message,
                    }
                )
                # A new writing cycle must pass its own latest checker report.
                self.handlers.latest_checker_report = None

        message = f"Maximum step limit reached ({self.max_steps}); stopping the agent."
        self.output_fn(message)
        self._log_event(
            {
                "type": "step_limit",
                "step": self.step,
                "max_steps": self.max_steps,
                "message": message,
            }
        )
        self._log_event(
            {
                "type": "run_finished",
                "step": self.step,
                "accepted": False,
                "status": "step_limit",
            }
        )
        return {
            "status": "step_limit",
            "accepted": False,
            "steps": self.step,
            "message": message,
            "messages": self.messages,
        }


def _next_log_path(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        candidate = log_dir / f"run_{index:02d}.jsonl"
        try:
            # Reserve the filename atomically so concurrent starts cannot select
            # the same run log. LLMClient appends to this empty file.
            descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
            return candidate
        index += 1


def _unique_requested_log_path(log_path: Path) -> Path:
    """Return the requested path, adding a numeric suffix rather than overwriting."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        candidate = (
            log_path
            if index == 1
            else log_path.with_name(
                f"{log_path.stem}_{index:02d}{log_path.suffix or '.jsonl'}"
            )
        )
        try:
            descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            index += 1
        else:
            os.close(descriptor)
            return candidate


def create_agent(
    content_dir: str | Path,
    out_path: str | Path,
    *,
    images_dir: str | Path | None = None,
    llm: Any | None = None,
    rag_instance: Any | None = None,
    max_steps: int = 25,
    max_tokens: int | None = 24_000,
    k: int = 5,
    model: str | None = None,
    log_path: str | Path | None = None,
    task_description: str = DEFAULT_TASK_DESCRIPTION,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], Any] = print,
) -> AgentLoop:
    """Initialize the LLM, RAG index, action handlers, and protocol loop."""
    logs_dir = Path(__file__).resolve().parent / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    if llm is None:
        selected_log_path = (
            _next_log_path(logs_dir)
            if log_path is None
            else _unique_requested_log_path(Path(log_path))
        )
        llm = LLMClient(model=model, log_path=selected_log_path)
    if rag_instance is None:
        rag_instance = rag.StyleGuideRAG(llm=llm)
    handlers = ActionHandlers(
        content_dir=content_dir,
        out_path=out_path,
        images_dir=images_dir,
        search_k=k,
        rag_module=rag_instance,
    )
    return AgentLoop(
        llm=llm,
        handlers=handlers,
        task_description=task_description,
        max_steps=max_steps,
        max_tokens=max_tokens,
        input_fn=input_fn,
        output_fn=output_fn,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Task 2 JSON-protocol coding agent."
    )
    parser.add_argument(
        "--content",
        type=Path,
        default=REPO_ROOT / "task1_opencode" / "content",
        help="directory containing member profile Markdown",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "site" / "index.html",
        help="output HTML file",
    )
    parser.add_argument(
        "--images", type=Path, help="image directory (default: <content>/images)"
    )
    parser.add_argument(
        "--model", help="chat model override (defaults to LAB_CHAT_MODEL)"
    )
    parser.add_argument(
        "--max-steps", type=int, default=25, help="maximum action steps (default: 25)"
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=24_000,
        help="maximum completion tokens per model call",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=5,
        help="number of style-guide chunks to retrieve per search",
    )
    parser.add_argument(
        "--log",
        type=Path,
        help="JSONL log path (default: a new task2_own_agent/logs/run_*.jsonl)",
    )
    parser.add_argument("--task", help="override the task description")
    args = parser.parse_args(argv)

    try:
        agent = create_agent(
            content_dir=args.content,
            out_path=args.out,
            images_dir=args.images,
            max_steps=args.max_steps,
            max_tokens=args.max_tokens,
            k=args.k,
            model=args.model,
            log_path=args.log,
            task_description=args.task or DEFAULT_TASK_DESCRIPTION,
        )
        result = agent.run()
    except Exception as exc:
        print(f"Could not start agent: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ActionHandlers",
    "AgentLoop",
    "create_agent",
    "main",
    "SYSTEM_PROMPT",
    "DEFAULT_TASK_DESCRIPTION",
]
