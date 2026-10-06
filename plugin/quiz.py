"""quiz — a GRADED sibling of clarify.

Hermes port of the Pi `quiz` extension. Where `clarify` collects a preference
or decision with no notion of right or wrong, `quiz` poses a question that HAS
a correct answer, grades the learner's pick instantly, and hands the agent the
verdict, the correct answer and the explanation to show the learner.

The question is put to the learner through the same platform callback that
`clarify` uses (CLI picker, WebUI card, messaging buttons), but called directly
so that:
  * options are shuffled (the correct answer isn't always in the same slot),
  * nothing is labelled "(Recommended)" — clarify marks its first choice that
    way, which would leak the answer,
  * an "I don't know" choice is always present and is reported as its own
    outcome, never as a wrong guess,
  * more than clarify's 4 choices can be shown.

Options only: single-select or multi-select. If the learner types a free-text
answer instead of picking, it comes back ungraded for the agent to judge.
"""

from __future__ import annotations

import inspect
import json
import random
import re
from typing import Any, Callable, List, Optional

DONT_KNOW_LABEL = "I don't know"
_DONT_KNOW_TYPED = {"i don't know", "i dont know", "dont know", "don't know", "idk", "no idea", "?"}
_RECOMMENDED = "(recommended)"
_TIMEOUT_MARKERS = ("did not provide a response", "no user available")

QUIZ_SCHEMA = {
    "name": "quiz",
    "description": (
        "Ask the learner ONE graded multiple-choice question that has a definite right answer, "
        "and get back whether they were right. Use it for probing what they already know and for "
        "checking that a step landed. (For questions with no right answer — preferences, goals, "
        "what to do next — use clarify instead.)\n\n"
        "Options are shuffled before display and an \"I don't know\" choice is always added, so "
        "don't add one yourself. The learner sees ONLY the question and the option labels: keep "
        "every option a bare claim of the same shape and length, with no justification — all "
        "reasoning goes in `explanation`, which you reveal after they answer. The popup shows plain "
        "text, so prefer Unicode math (x², √2, π, ≤) over LaTeX inside the question and options.\n\n"
        "The result tells you the verdict (correct / incorrect / dont_know / freeform), what they "
        "picked, and the correct answer. The learner has NOT seen the verdict or the explanation: "
        "open your next reply with them."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The single quiz question. One question per call.",
            },
            "details": {
                "type": "string",
                "description": "Optional extra context shown under the question (e.g. a formula or snippet the question refers to).",
            },
            "options": {
                "type": "array",
                "minItems": 2,
                "description": (
                    "The answer options (2 or more; 3-5 is typical). Give each a short stable `value`; "
                    "correct_answer refers to options by that value."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Text shown to the learner."},
                        "value": {
                            "type": "string",
                            "description": "Short machine id for the option (e.g. 'a', 'mercury'). Defaults to the label.",
                        },
                    },
                    "required": ["label"],
                },
            },
            "correct_answer": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "REQUIRED. The `value` of the correct option, as a one-element array for "
                    "single-select (e.g. [\"mercury\"]); every correct value for multi-select "
                    "(the learner is right only if they pick exactly this set)."
                ),
            },
            "explanation": {
                "type": "string",
                "description": "REQUIRED. Why the correct answer is correct (and, ideally, what the tempting wrong ones get wrong). Revealed only after the learner answers.",
            },
            "multi_select": {
                "type": "boolean",
                "description": "True when more than one option is correct and the learner must pick all of them.",
            },
            "shuffle": {
                "type": "boolean",
                "description": "Default true. Set false only when option order is meaningful (ordered numbers, or an 'all of the above' that must stay last).",
            },
        },
        "required": ["question", "options", "correct_answer", "explanation"],
    },
}


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off", ""}
    return bool(value)


def _normalize_options(raw: Any) -> List[dict]:
    if not isinstance(raw, list):
        raise ValueError("`options` must be a list of {label, value} objects.")
    seen_values, seen_labels, out = set(), set(), []
    for item in raw:
        if isinstance(item, str):
            label, value = item.strip(), item.strip()
        elif isinstance(item, dict):
            label = str(item.get("label") or "").strip()
            value = str(item.get("value") or "").strip() or label
        else:
            continue
        if not label:
            continue
        if value in seen_values:
            raise ValueError(f'duplicate option value "{value}"')
        if label.casefold() in seen_labels:
            raise ValueError(f'duplicate option label "{label}"')
        if label.casefold() == DONT_KNOW_LABEL.casefold():
            raise ValueError("don't add an \"I don't know\" option — the quiz always adds one")
        seen_values.add(value)
        seen_labels.add(label.casefold())
        out.append({"label": label, "value": value})
    return out


def _coerce_correct(raw: Any) -> List[str]:
    """Accept a list, a single string, or a JSON-stringified list."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if str(v).strip()]
    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except json.JSONDecodeError:
            pass
    return [text] if text else []


def _accepts_kwarg(callback: Callable, name: str) -> bool:
    try:
        params = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def _strip_recommended(text: str) -> str:
    stripped = str(text).strip()
    if stripped.casefold().endswith(_RECOMMENDED):
        stripped = stripped[: -len(_RECOMMENDED)].strip()
    return stripped


def _split_multi(raw: Any) -> List[str]:
    if isinstance(raw, list):
        items = raw
    else:
        text = str(raw).strip()
        items = None
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                items = parsed if isinstance(parsed, list) else None
            except json.JSONDecodeError:
                items = None
        if items is None:
            items = re.split(r"[,\n;]", text)
    return [str(i).strip() for i in items if str(i).strip()]


def _match(answer: str, display: List[dict]) -> Optional[int]:
    """1-based index of the option the answer names (by label, or by its number), else None."""
    text = _strip_recommended(answer)
    folded = text.casefold()
    for i, opt in enumerate(display, start=1):
        if opt["label"].casefold() == folded:
            return i
    m = re.fullmatch(r"\(?(\d{1,2})[.)]?", text)
    if m and 1 <= int(m.group(1)) <= len(display):
        return int(m.group(1))
    return None


def _is_dont_know(answer: str) -> bool:
    folded = _strip_recommended(answer).casefold().strip(" .!")
    return folded == DONT_KNOW_LABEL.casefold() or folded in _DONT_KNOW_TYPED


def _refs(indices: List[int], display: List[dict]) -> List[dict]:
    return [{"index": i, "label": display[i - 1]["label"]} for i in indices]


def _result(status: str, question: str, details: Optional[str], display: List[dict], correct: List[int],
            explanation: str, **extra: Any) -> str:
    payload = {
        "status": status,
        "question": question,
        **({"details": details} if details else {}),
        "options": [{"index": i, "label": o["label"]} for i, o in enumerate(display, start=1)],
        "correct_answer": _refs(correct, display),
        "explanation": explanation,
        **extra,
    }
    return json.dumps(payload, ensure_ascii=False)


def run_quiz(args: dict, parent_agent: Any = None) -> str:
    question = str(args.get("question") or "").strip()
    details = str(args.get("details") or "").strip() or None
    explanation = str(args.get("explanation") or "").strip()
    multi = _as_bool(args.get("multi_select", args.get("multiSelect")), False)
    shuffle = _as_bool(args.get("shuffle"), True)

    def error(msg: str) -> str:
        return json.dumps({"status": "error", "error": msg}, ensure_ascii=False)

    if not question:
        return error("`question` is required.")
    if not explanation:
        return error("`explanation` is required (it's revealed after the learner answers).")
    try:
        options = _normalize_options(args.get("options"))
    except ValueError as exc:
        return error(str(exc))
    if len(options) < 2:
        return error("A quiz needs at least 2 options.")

    display = list(options)
    if shuffle:
        random.shuffle(display)

    correct_values = _coerce_correct(args.get("correct_answer", args.get("correctAnswer")))
    if not correct_values:
        return error("`correct_answer` is required: the `value`(s) of the correct option(s).")
    by_value = {o["value"]: i for i, o in enumerate(display, start=1)}
    correct: List[int] = []
    for v in correct_values:
        if v not in by_value:
            known = ", ".join(f'"{o["value"]}"' for o in options)
            return error(f'correct_answer "{v}" does not match any option value ({known}).')
        correct.append(by_value[v])
    correct = sorted(set(correct))
    if not multi and len(correct) > 1:
        return error("Several correct answers given for a single-select quiz — set multi_select=true or give one value.")

    callback = getattr(parent_agent, "clarify_callback", None) if parent_agent is not None else None
    if callback is None:
        return _result("unavailable", question, details, display, correct, explanation,
                       message="No interactive learner on this surface (e.g. a subagent, cron, or one-shot run). Ask the question in plain text instead.")
    supports_multi = _accepts_kwarg(callback, "multi_select")
    if multi and not supports_multi:
        return _result("unavailable", question, details, display, correct, explanation,
                       message="This chat surface can't do multi-select. Re-ask it as one or more single-select quizzes.")

    prompt = question + (f"\n\n{details}" if details else "")
    if multi:
        prompt += "\n\n(Select all that apply.)"
    choices = [o["label"] for o in display] + [DONT_KNOW_LABEL]

    try:
        raw = callback(prompt, choices, multi_select=multi) if supports_multi else callback(prompt, choices)
    except Exception as exc:  # the UI went away mid-question
        return _result("unavailable", question, details, display, correct, explanation, message=f"Could not ask: {exc}")

    if raw is None or (isinstance(raw, str) and any(m in raw.casefold() for m in _TIMEOUT_MARKERS)):
        return _result("cancelled", question, details, display, correct, explanation,
                       message="The learner didn't answer (timed out or skipped). Don't count it either way.")

    answers = _split_multi(raw) if multi else [str(raw).strip()]
    answers = [a for a in answers if a]
    if not answers:
        return _result("cancelled", question, details, display, correct, explanation,
                       message="The learner skipped the question.")

    if any(_is_dont_know(a) for a in answers):
        return _result("answered", question, details, display, correct, explanation, verdict="dont_know",
                       selected=[],
                       next_step=("The learner said \"I don't know\" — a genuine gap, not a wrong answer. "
                                  "Reveal the correct answer and explanation without marking it wrong."))

    picked = [_match(a, display) for a in answers]
    if any(p is None for p in picked):
        return _result("answered", question, details, display, correct, explanation, verdict="freeform",
                       selected=_refs(sorted({p for p in picked if p}), display),
                       learner_text=str(raw).strip(),
                       next_step=("The learner typed their own answer instead of picking an option. "
                                  "Judge it yourself against the correct answer, tell them the verdict, "
                                  "then give the explanation."))

    selected = sorted(set(picked))  # type: ignore[arg-type]
    is_correct = selected == correct
    return _result("answered", question, details, display, correct, explanation,
                   verdict="correct" if is_correct else "incorrect",
                   selected=_refs(selected, display),
                   next_step=("The learner has NOT seen the verdict or the explanation yet. Open your reply "
                              "with ✓/✗, the correct answer, and the explanation, then continue."))
