# learn — for Hermes

[![video](assets/thumbnail.png)](https://www.youtube.com/watch?v=kzcI5F4tGiU)

A port of [amosblomqvist/learn](https://github.com/amosblomqvist/learn) — the AI learning system from the video [How I Use AI to Learn Things](https://www.youtube.com/watch?v=kzcI5F4tGiU) — from a [pi](https://github.com/earendil-works/pi) configuration to [Hermes Agent](https://github.com/NousResearch/hermes-agent). The teaching philosophy is unchanged; the Pi-specific pieces (extensions, agent definitions, slash commands) are rebuilt on Hermes' skills, plugin tools and `delegate_task`.

## What's in it

- `skills/teach/` — the philosophy and the process (probe → plan → teach)
  - `references/researcher.md` — brief for the researcher subagent (truth verification, topic scoping)
- `skills/visualize/` — adds a correct, minimal diagram to a lesson when an idea is clearer as a picture
  - `references/mermaid-maker.md`, `references/svg-maker.md` — briefs for the two maker subagents
  - `scripts/viz.mjs` — renders Mermaid / SVG to PNG and publishes it (replaces Pi's `visual-tools`)
- `plugin/` — the `learn` Hermes plugin:
  - `quiz` — graded questions with instant feedback (✓/✗, correct answer, explanation), shuffled options and an always-present "I don't know"
  - `md_log` — mirror the session into a markdown notebook (Obsidian or any markdown viewer)
- `install.sh` — links the skills and plugin into a Hermes profile

### Pi → Hermes mapping

| Pi (upstream) | Hermes (here) |
|---|---|
| `extensions/ask-user-question` | built-in `clarify` tool |
| `extensions/quiz` | `quiz` tool (plugin) — calls the same UI as `clarify`, without its "(Recommended)" label on the first choice, which would leak the answer |
| `extensions/md-log` + `/md-log` | `md_log` tool (plugin) + `post_llm_call` hook. Append-only; the agent links a notebook when the learner asks |
| `agents/*.md` + pi-interactive-subagents | `delegate_task` children that read a brief from `references/` |
| `extensions/visual-tools` (`write_/edit_/render_*`) | the makers' normal file tools + `viz.mjs` via `terminal`, and `vision_analyze` to look at the render |
| Obsidian `![[file.png\|500]]` embeds | `MEDIA:/path.png` (shown inline in chat; `md_log` turns it into an embed) |

## Install

```bash
git clone https://github.com/henrykrinkle01/learn
cd learn && ./install.sh
```

`install.sh` symlinks `skills/` into `$HERMES_HOME/skills/learn`, symlinks `plugin/` into `$HERMES_HOME/plugins/learn` and enables it, and installs the diagram renderer. Use `HERMES_HOME=…` to pick a profile and `--skills-dir` to put the skills in a shared directory listed in `skills.external_dirs` instead. Restart a running gateway/WebUI afterwards so it loads the plugin.

Set where notebooks and diagrams go (default `/workspace/learning`):

```bash
hermes config set skills.config.learn.dir ~/notes/Learning
```

## Requirements

- Hermes Agent with the `clarify`, `skills` and `file` toolsets. That's enough to teach.
- `web` (research), `delegation` (researcher + makers), `terminal` and `vision` (diagrams) for the full system. Without them it degrades gracefully: no `delegate_task` → the teacher fact-checks with `web_search` itself; no terminal/delegation/vision → no diagrams (the `visualize` skill hides itself).
- Without the plugin, `teach` falls back to `clarify` for quizzes (it parks the "(Recommended)" label on "I don't know") and there's no notebook.
- Diagrams: Node ≥ 18. `scripts/setup.sh` installs the npm deps and, for Mermaid, a headless Chrome into `~/.cache/puppeteer` (Chrome can't run from a virtiofs/network mount, so run it once per machine). SVG rendering needs no system libraries.

## Notes

You can run the system without subagents. The main session does the teaching. You just lose the researcher (truth verification) and the generated visuals.

The upstream skill is written for one learner. This port speaks of "the learner" so one install can serve several people, triggers only when someone wants to learn something (upstream: on every explanation), and scales probing and step size to the learner (e.g. a child). Edit the skill to fit how you learn best.
