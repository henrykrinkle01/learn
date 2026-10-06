"""learn — Hermes plugin backing the learn skills (teach / visualize).

Registers:
  * `quiz`   — graded multiple-choice question with instant feedback (quiz.py)
  * `md_log` — mirror the session into a markdown notebook (md_log.py),
               plus the post_llm_call hook that does the appending.

Hermes port of the Pi extensions in https://github.com/amosblomqvist/learn
(`ask-user-question` maps to Hermes' built-in `clarify`, so it isn't ported).
"""

from __future__ import annotations

from typing import Any

from .md_log import MD_LOG_SCHEMA, make_handlers
from .quiz import QUIZ_SCHEMA, run_quiz

TOOLSET = "learn"


def register(ctx: Any) -> None:
    def quiz_handler(args: dict, parent_agent: Any = None, **_: Any) -> str:
        return run_quiz(args, parent_agent=parent_agent)

    ctx.register_tool(name="quiz", toolset=TOOLSET, schema=QUIZ_SCHEMA, handler=quiz_handler)

    md_log_tool, on_post_llm_call = make_handlers(ctx)
    ctx.register_tool(name="md_log", toolset=TOOLSET, schema=MD_LOG_SCHEMA, handler=md_log_tool)
    ctx.register_hook("post_llm_call", on_post_llm_call)
