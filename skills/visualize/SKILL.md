---
name: visualize
description: "Draw a verified diagram or geometric picture for a lesson. Adds a correct, minimal visual, shown inline in chat and in the lesson notebook. Use when an idea is genuinely clearer as a picture: a dependency graph, system/flow, sequence, state machine, tree, comparison, or a spatial/geometric thing (coordinate geometry, number line, vectors, a plot, a physical layout). Outsources authoring+rendering to a maker subagent that verifies the image by looking at it, then you embed the returned file."
version: 1.0.0
author: Amos Blomqvist (original, github.com/amosblomqvist/learn); Hermes port by henrykrinkle01
metadata:
  hermes:
    tags: [teaching, diagrams, mermaid, svg]
    related_skills: [teach]
    requires_toolsets: [delegation, terminal, vision]
    config:
      - key: learn.dir
        description: Folder for learning notebooks (md_log) and lesson diagrams (its viz/ subfolder)
        default: /workspace/learning
        prompt: Folder for learning notebooks and diagrams
---

# Visualize

A picture earns its place only when it shows something words can't — shape, structure, direction, relationship, geometry. This skill produces ONE such picture, guarantees it is **correct** (the maker renders it and looks at it before returning), and drops it into the lesson so it shows inline in chat and in the `md_log` notebook.

You are the **creative director**. You decide the exact idea and distill it to its fewest carrying elements. A **maker subagent** does the authoring, rendering, visual verification, and saving, then returns a file path. You embed that path in your reply.

## When to visualize (and when not to)

This teaching system builds a **dependency graph in the learner's head** — axioms at the root, derived facts hanging off them. A visual is powerful exactly when it makes that structure (or a geometry) visible. Reach for one when:

- The idea is a **structure or relationship**: dependencies, a system with parts and arrows, a flow/pipeline, a sequence of exchanges, a state machine, a tree/hierarchy, a comparison, a containment (what's inside vs outside).
- The idea is **spatial or geometric**: coordinate geometry, a number line, vectors, a function's shape, a physical arrangement.

Do NOT visualize when prose or a single equation already carries it. A decorative diagram that just restates the sentence next to it adds noise and a chance to be wrong. When in doubt, don't — a missing visual is cheaper than a false one.

## Choose the maker

Two makers, each a brief in `${HERMES_SKILL_DIR}/references/`:

- **`mermaid-maker.md`** — structural/relational visuals: dependency graphs, flowcharts, sequence/state/ER/class diagrams, trees, mindmaps, timelines. This is the default and fits the dependency-graph pedagogy directly (it's also how the lesson plan's map becomes a picture).
- **`svg-maker.md`** — spatial/geometric visuals Mermaid can't lay out: exact coordinates, geometry figures, number lines, vectors, plots, custom shapes.

Rule of thumb: if it's *nodes-and-edges / relationships*, use mermaid-maker. If it's *positions-and-shapes / geometry*, use svg-maker.

## Brief the maker well: one idea, fewest elements

The most common failure is **cramming** — every extra label makes the picture harder to read AND harder to lay out correctly. Before briefing, prune to the fewest elements that carry the idea, and for each ask: *"if I delete this, is the idea still clear?"* If yes, delete it.

Give the maker the concept AND the concrete elements you want — not a vague topic, and not a long checklist.

- BAD: "make a diagram about how TCP works"
- GOOD: "graph TD: a node 'packet' at the top; arrows down to 'ordering' and 'retransmit on loss'; both arrows down into 'reliable stream'. No title. Show that reliability is built FROM packets, not alongside them."

Keep the idea intact but trust the maker to compose; if your brief lists more than ~5–7 elements, cut it first.

## Invoke

Dispatch the maker with `delegate_task`. The child knows nothing of the lesson, so the brief file, the render helper and the output folder all go in `context`:

```
delegate_task(tasks=[{
  "goal": "Make ONE diagram: <your minimal, concrete brief>",
  "context": "Read ${HERMES_SKILL_DIR}/references/mermaid-maker.md first and follow it exactly.\nRender helper: node ${HERMES_SKILL_DIR}/scripts/viz.mjs"
}])
```

(`svg-maker.md` for the svg-maker.) Published pictures land in the `viz/` folder of the learner's learning folder (`skills.config.learn.dir`), next to their `md_log` notebooks.

The maker authors the source with its file tools, renders it with `viz.mjs`, **looks at the PNG with `vision_analyze` and iterates until it is correct and clean**, publishes it with a unique filename, and ends with:

```
RESULT:
filename: viz-<slug>-<timestamp>.png
path: <learning folder>/viz/viz-<slug>-<timestamp>.png
```

It runs in the background: carry on with whatever doesn't need the picture (or tell the learner in one line that a picture is coming) and end your turn; the result arrives as a new message.

If it returns `RESULT: NONE`, it couldn't make a correct picture of the brief — simplify or rethink, or decide the visual isn't worth it. Never hand-author or fake a diagram yourself; correctness depends on the maker's render-and-inspect loop.

## Embed it in the lesson

Put the returned **path** on its own line in your teaching reply:

```
MEDIA:<path>
```

The chat shows it as an inline image, and the `md_log` notebook turns the same line into an embedded image — so what the maker verified is pixel-identical to what the learner sees, in both places. Introduce the visual in a sentence, then let it carry the idea — don't narrate every element back in prose.

## Why this is reliable

- The maker never returns a picture it hasn't **looked at**, so "renders fine but says something false" is caught before it reaches the learner.
- PNG embed means **what the maker verified is exactly what the learner sees** — no re-render drift.
- Unique filenames keep embeds unambiguous across lessons.

## Setup (once per machine)

`viz.mjs` renders Mermaid through the bundled `@mermaid-js/mermaid-cli` and a headless Chrome, and SVG through the bundled `resvg` (no system dependencies). If a maker reports that a renderer is missing, run `bash ${HERMES_SKILL_DIR}/scripts/setup.sh` once; `node ${HERMES_SKILL_DIR}/scripts/viz.mjs check` shows what's available. You don't render anything yourself — you only brief the maker and embed the path it returns.
