"""md_log — mirror a learning session into a markdown notebook.

Hermes port of the Pi `md-log` extension. Long teaching sessions are easier to
re-read as a rendered markdown file (Obsidian or any markdown viewer: math,
code, images and callouts all render natively), so this keeps one.

Captures only reading-relevant content:
  * the learner's messages
  * the tutor's replies (MEDIA:/path images become markdown image embeds)
  * quiz / clarify Q&A blocks (question, options in the order shown, answer,
    and for quizzes the verdict and explanation)
Other tools (terminal, files, web, subagents, ...) are omitted.

Linking is done with the `md_log` tool (the agent calls it when the learner
asks for a notebook). Append-only, never overwrites: on link, everything said
so far in the session is appended at the end of that turn; after that each
turn is appended when it ends (post_llm_call hook).
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

STATE_KEY = "md_log.links"  # {session_id: {"file": str, "logged": int, "header": bool}}
QA_TOOLS = ("quiz", "clarify")

MD_LOG_SCHEMA = {
    "name": "md_log",
    "description": (
        "Link this chat session to a markdown notebook so the lesson can be re-read later, rendered "
        "(Obsidian or any markdown viewer). The notebook gets the learner's messages, your replies "
        "(MEDIA:/path images become embedded images) and every quiz / clarify question with its answer; "
        "other tool calls are left out. Append-only — it never overwrites: on link, the session so far "
        "is appended at the end of this turn, then each later turn is appended as it ends.\n"
        "action='link' (default) needs `path`; 'unlink' stops logging; 'status' shows the linked file and "
        "the learning folder."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["link", "unlink", "status"],
                "description": "Default 'link'.",
            },
            "path": {
                "type": "string",
                "description": (
                    "The .md notebook (for 'link'): a bare name like 'tcp-basics.md' goes in the learner's "
                    "learning folder (skills.config.learn.dir); an absolute path is used as-is. Created if missing."
                ),
            },
        },
        "required": [],
    },
}

DEFAULT_LEARN_DIR = "/workspace/learning"

_write_lock = threading.Lock()


def learn_dir() -> str:
    """The learning folder: skills.config.learn.dir from the active profile, else the default."""
    try:
        from agent.skill_utils import resolve_skill_config_values

        value = resolve_skill_config_values([{"key": "learn.dir", "default": DEFAULT_LEARN_DIR}]).get("learn.dir")
    except Exception:
        value = None
    return os.path.expanduser(str(value or DEFAULT_LEARN_DIR))


# ── state ─────────────────────────────────────────────────────────────────────

class _Links:
    """Per-session link table kept in the plugin's profile-scoped state."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._lock = threading.Lock()

    def all(self) -> Dict[str, dict]:
        try:
            value = self._ctx.state.get(STATE_KEY, default={})
        except Exception:
            value = {}
        return dict(value) if isinstance(value, dict) else {}

    def get(self, session_id: str) -> Optional[dict]:
        entry = self.all().get(session_id)
        return dict(entry) if isinstance(entry, dict) else None

    def put(self, session_id: str, entry: Optional[dict]) -> None:
        with self._lock:
            links = self.all()
            if entry is None:
                links.pop(session_id, None)
            else:
                links[session_id] = entry
            self._ctx.state.set(STATE_KEY, links)


# ── formatting ────────────────────────────────────────────────────────────────

def _callout(kind: str, title: str, body: List[str]) -> str:
    lines = [f"> [!{kind}] {title}"]
    lines += [">" if not line else f"> {line}" for line in body]
    return "\n".join(lines)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


_MEDIA_RE = re.compile(r"MEDIA:(\S+)")
_DIRECTIVE_RE = re.compile(r"\[\[(?:as_document|audio_as_voice)\]\]")


def _embed_media(text: str, notebook: str) -> str:
    base = os.path.dirname(notebook)

    def repl(m: "re.Match[str]") -> str:
        path = m.group(1).rstrip(".,;)")
        trailing = m.group(1)[len(path):]
        try:
            rel = os.path.relpath(path, base) if os.path.isabs(path) else path
        except ValueError:
            rel = path
        return f"![](<{rel}>){trailing}"

    return _DIRECTIVE_RE.sub("", _MEDIA_RE.sub(repl, text)).strip()


def _describe_user_text(text: str) -> Optional[str]:
    """Learner text as typed; None for injected system/subagent turns that aren't the learner."""
    text = text.strip()
    if not text:
        return None
    try:
        from agent.skill_commands import describe_skill_invocation

        described = describe_skill_invocation(text)
        if described:
            return described
    except Exception:
        pass
    head = text[:300].casefold()
    if text.startswith("[") and any(k in head for k in ("subagent", "delegat", "background", "system")):
        return None
    return text


def _user_block(text: str) -> str:
    return f"> [!quote] YOU\n\n{text}"


def _assistant_block(text: str) -> str:
    return f"> [!abstract] TUTOR\n\n{text}"


def _question_block(label: str, question: str, details: Optional[str], options: List[str]) -> str:
    body = question.split("\n")
    if details:
        body += [""] + details.split("\n")
    if options:
        body += [""] + [f"{i}. {o}" for i, o in enumerate(options, start=1)]
    return _callout("question", label, body)


def _render_quiz(result: dict) -> List[str]:
    status = result.get("status")
    if status in (None, "error"):
        return []
    options = [o.get("label", "") for o in result.get("options") or []]
    blocks = [_question_block("Quiz", str(result.get("question", "")), result.get("details"), options)]
    if status == "cancelled":
        return blocks + [_callout("warning", "Quiz — skipped", ["(no answer)"])]
    if status == "unavailable":
        return blocks + [_callout("warning", "Quiz — unavailable", [str(result.get("message", ""))])]

    verdict = result.get("verdict")
    kind, title = {
        "correct": ("success", "Quiz — correct ✓"),
        "incorrect": ("failure", "Quiz — incorrect ✗"),
        "dont_know": ("question", "Quiz — I don't know"),
        "freeform": ("example", "Quiz — own answer"),
    }.get(verdict, ("example", "Quiz"))

    def refs(items: Any) -> str:
        return ", ".join(f"{r.get('index')}. {r.get('label')}" for r in items or []) or "(none)"

    body: List[str] = []
    if verdict == "dont_know":
        body.append("Your answer: I don't know")
    elif verdict == "freeform":
        body.append(f"Your answer: {result.get('learner_text', '')}")
    else:
        body.append(f"Your answer: {refs(result.get('selected'))}")
    body.append(f"Correct answer: {refs(result.get('correct_answer'))}")
    explanation = str(result.get("explanation") or "")
    if explanation:
        body += [""] + explanation.split("\n")
    return blocks + [_callout(kind, title, body)]


def _render_clarify(result: dict) -> List[str]:
    entries = result.get("responses") if isinstance(result.get("responses"), list) else [result]
    blocks: List[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or "question" not in entry:
            continue
        choices = [str(c) for c in entry.get("choices_offered") or []]
        blocks.append(_question_block("Question", str(entry.get("question", "")), None, choices))
        answer = entry.get("user_response")
        if isinstance(answer, list):
            answer = ", ".join(str(a) for a in answer)
        blocks.append(_callout("example", "Answer", str(answer or "(no answer)").split("\n")))
    return blocks


def _parse_json(raw: Any) -> Optional[dict]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(_content_text(raw) if not isinstance(raw, str) else raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def render_messages(messages: List[dict], start: int, notebook: str) -> List[str]:
    """Markdown blocks for messages[start:], pairing quiz/clarify calls with their results."""
    tool_results = {
        m.get("tool_call_id"): m.get("content")
        for m in messages
        if isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id")
    }
    blocks: List[str] = []
    for msg in messages[start:]:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "user":
            text = _describe_user_text(_content_text(msg.get("content")))
            if text:
                blocks.append(_user_block(text))
        elif role == "assistant":
            text = _content_text(msg.get("content")).strip()
            if text:
                blocks.append(_assistant_block(_embed_media(text, notebook)))
            for call in msg.get("tool_calls") or []:
                fn = (call or {}).get("function") or {}
                name = fn.get("name")
                if name not in QA_TOOLS:
                    continue
                result = _parse_json(tool_results.get(call.get("id")))
                if result is None:
                    continue
                blocks += _render_quiz(result) if name == "quiz" else _render_clarify(result)
    return blocks


def _append(path: str, blocks: List[str]) -> None:
    if not blocks:
        return
    with _write_lock:
        existing = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                existing = fh.read()
        if not existing.strip() or existing.endswith("\n\n"):
            prefix = ""
        elif existing.endswith("\n"):
            prefix = "\n"
        else:
            prefix = "\n\n"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(prefix + "\n\n".join(blocks) + "\n")


# ── tool + hook ───────────────────────────────────────────────────────────────

def make_handlers(ctx: Any):
    links = _Links(ctx)

    def md_log_tool(args: dict, session_id: Optional[str] = None, **_: Any) -> str:
        action = str(args.get("action") or "link").strip().lower()
        if not session_id:
            return json.dumps({"status": "error", "error": "No session id — md_log only works inside a chat session."})
        entry = links.get(session_id)

        if action == "status":
            return json.dumps({"status": "linked" if entry else "not_linked", **({"file": entry["file"]} if entry else {}),
                               "learn_dir": learn_dir()})

        if action == "unlink":
            links.put(session_id, None)
            return json.dumps({"status": "unlinked", **({"file": entry["file"]} if entry else {})})

        if action != "link":
            return json.dumps({"status": "error", "error": f"Unknown action {action!r} (link / unlink / status)."})
        raw_path = str(args.get("path") or "").strip()
        if not raw_path:
            return json.dumps({"status": "error", "error": "`path` is required to link a notebook."})
        path = os.path.expanduser(raw_path)
        path = os.path.abspath(path if os.path.isabs(path) else os.path.join(learn_dir(), path))
        if not path.lower().endswith(".md"):
            return json.dumps({"status": "error", "error": "The notebook must be a .md file."})
        if os.path.isdir(path):
            return json.dumps({"status": "error", "error": f"Not a file: {path}"})
        created = not os.path.exists(path)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if created:
                with open(path, "w", encoding="utf-8") as fh:
                    title = os.path.splitext(os.path.basename(path))[0].replace("-", " ").replace("_", " ")
                    fh.write(f"# {title}\n")
        except OSError as exc:
            return json.dumps({"status": "error", "error": f"Cannot create {path}: {exc}"})

        same_file = bool(entry) and entry.get("file") == path
        links.put(session_id, {
            "file": path,
            # Relinking the same file resumes where it stopped; a new file gets the whole session.
            "logged": int(entry.get("logged", 0)) if same_file else 0,
            "header": not same_file,
        })
        return json.dumps({
            "status": "linked",
            "file": path,
            "created": created,
            "note": "The session so far is appended to the notebook when this turn ends; later turns follow automatically.",
        })

    def on_post_llm_call(session_id: Optional[str] = None, conversation_history: Optional[list] = None, **_: Any) -> None:
        if not session_id or not conversation_history:
            return
        entry = links.get(session_id)
        if not entry:
            return
        messages = [m for m in conversation_history if isinstance(m, dict) and m.get("role") != "system"]
        start = int(entry.get("logged", 0))
        if start > len(messages):
            # History was compressed under us: resume from the start of this turn.
            start = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=0)
        blocks = render_messages(messages, start, entry["file"])
        if entry.get("header"):
            blocks.insert(0, f"## Session {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        try:
            _append(entry["file"], blocks)
        except OSError:
            return  # file moved or deleted; keep the link, try again next turn
        entry.update(logged=len(messages), header=False)
        links.put(session_id, entry)

    return md_log_tool, on_post_llm_call
