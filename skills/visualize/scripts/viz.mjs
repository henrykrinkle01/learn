#!/usr/bin/env node
/**
 * viz.mjs — render helper for the visualize skill's maker subagents.
 *
 * Hermes port of the Pi `visual-tools` extension. The makers author a source
 * file with their normal file tools (write_file / patch), then call this script
 * through `terminal` to turn it into a PNG they can LOOK at (vision_analyze),
 * and finally publish the verified PNG into the learner's viz folder.
 *
 *   node viz.mjs render  <source.mmd|source.svg> [--out <file.png>]
 *       Preview render. Prints `png: <path>` (default: next to the source).
 *   node viz.mjs publish <source.mmd|source.svg> --slug <kebab-topic> [--dir <viz dir>]
 *       Render and copy into <viz dir> as viz-<slug>-<timestamp>.png.
 *       Prints `filename:` and `path:`. <viz dir> defaults to $LEARN_VIZ_DIR,
 *       else $LEARN_DIR/viz, else <skills.config.learn.dir in the Hermes
 *       profile's config.yaml>/viz, else /workspace/learning/viz.
 *   node viz.mjs check
 *       Report which renderers are available on this machine.
 *
 * Mermaid renders through the bundled @mermaid-js/mermaid-cli (mmdc) driving a
 * headless Chrome found on disk (see findChrome). SVG renders through the
 * bundled @resvg/resvg-js (no system deps), falling back to rsvg-convert, then
 * ImageMagick. Exit code is non-zero on failure, with the renderer's error text
 * on stderr so the maker can fix the source and retry.
 */

import { spawnSync } from "node:child_process"
import { accessSync, constants, copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs"
import { homedir, tmpdir } from "node:os"
import { basename, dirname, extname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const SCRIPTS_DIR = dirname(fileURLToPath(import.meta.url))
const MMDC_BIN = join(SCRIPTS_DIR, "node_modules", ".bin", "mmdc")
const RENDER_TIMEOUT_MS = 120_000

function fail(message) {
  process.stderr.write(message.trimEnd() + "\n")
  process.exit(1)
}

function parseArgs(argv) {
  const positional = []
  const flags = {}
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a.startsWith("--")) {
      const key = a.slice(2)
      const next = argv[i + 1]
      if (next === undefined || next.startsWith("--")) flags[key] = true
      else flags[key] = argv[++i]
    } else positional.push(a)
  }
  return { positional, flags }
}

// ── Chrome discovery ─────────────────────────────────────────────────────────

/** Newest-first list of `<root>/<prefix>*` subdirectories. */
function versionDirs(root, prefix) {
  if (!existsSync(root)) return []
  return readdirSync(root)
    .filter((d) => d.startsWith(prefix))
    .sort()
    .reverse()
    .map((d) => join(root, d))
}

/** First existing file named one of `names` up to two levels under `dir`. */
function findBinaryUnder(dir, names) {
  if (!existsSync(dir)) return undefined
  for (const n of names) if (existsSync(join(dir, n))) return join(dir, n)
  for (const sub of readdirSync(dir)) {
    const subdir = join(dir, sub)
    for (const n of names) if (existsSync(join(subdir, n))) return join(subdir, n)
    try {
      for (const sub2 of readdirSync(subdir)) {
        for (const n of names) if (existsSync(join(subdir, sub2, n))) return join(subdir, sub2, n)
      }
    } catch {
      // not a directory
    }
  }
  return undefined
}

export function findChrome() {
  for (const env of ["LEARN_CHROME", "PUPPETEER_EXECUTABLE_PATH"]) {
    if (process.env[env] && existsSync(process.env[env])) return process.env[env]
  }
  const shellNames = ["chrome-headless-shell", "headless_shell"]
  const chromeNames = ["chrome"]
  // 1. Per-user Puppeteer (what setup.sh installs) / Playwright caches. Chrome
  //    must live on a local disk: it can't mmap its data files from virtiofs.
  const home = homedir()
  const cache = process.env.XDG_CACHE_HOME || join(home, ".cache")
  for (const d of versionDirs(join(process.env.PUPPETEER_CACHE_DIR || join(cache, "puppeteer"), "chrome-headless-shell"), "")) {
    const hit = findBinaryUnder(d, shellNames)
    if (hit) return hit
  }
  for (const d of versionDirs(join(cache, "ms-playwright"), "chromium_headless_shell-")) {
    const hit = findBinaryUnder(d, shellNames)
    if (hit) return hit
  }
  for (const d of versionDirs(join(cache, "ms-playwright"), "chromium-")) {
    const hit = findBinaryUnder(d, chromeNames)
    if (hit) return hit
  }
  // 2. System installs.
  for (const p of [
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/snap/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
  ]) {
    if (existsSync(p)) return p
  }
  return undefined
}

function which(cmd) {
  const res = spawnSync("sh", ["-c", `command -v ${cmd}`], { encoding: "utf8" })
  return res.status === 0 ? res.stdout.trim() : undefined
}

async function loadResvg() {
  try {
    return (await import("@resvg/resvg-js")).Resvg
  } catch {
    return undefined
  }
}

// ── Renderers ────────────────────────────────────────────────────────────────

function detectKind(sourcePath) {
  const ext = extname(sourcePath).toLowerCase()
  if (ext === ".svg") return "svg"
  if (ext === ".mmd" || ext === ".mermaid") return "mermaid"
  const head = readFileSync(sourcePath, "utf8").trimStart().slice(0, 200)
  return head.startsWith("<svg") || head.startsWith("<?xml") ? "svg" : "mermaid"
}

function renderMermaid(sourcePath, outPath) {
  if (!existsSync(MMDC_BIN)) {
    fail(`Mermaid renderer not installed (${MMDC_BIN} missing). Run: bash ${join(SCRIPTS_DIR, "setup.sh")}`)
  }
  const chrome = findChrome()
  if (!chrome) {
    fail(
      "No headless Chrome found for Mermaid rendering. Run: bash " +
        join(SCRIPTS_DIR, "setup.sh") +
        " (or set LEARN_CHROME=/path/to/chrome).",
    )
  }
  // Chrome's profile goes in TMPDIR. Hermes points TMPDIR at a scratch dir under
  // HERMES_HOME, which may be a virtiofs/network mount Chrome can't mmap from — so
  // give it a work dir on local disk instead.
  const workDir = mkdtempLike("mmdc")
  const cfgPath = join(workDir, "puppeteer.json")
  writeFileSync(
    cfgPath,
    JSON.stringify({ executablePath: chrome, args: ["--no-sandbox", "--disable-gpu"], headless: "shell" }),
    "utf8",
  )
  const res = spawnSync(MMDC_BIN, ["-i", sourcePath, "-o", outPath, "-p", cfgPath, "-s", "2", "-b", "white", "-q"], {
    encoding: "utf8",
    timeout: RENDER_TIMEOUT_MS,
    env: { ...process.env, PUPPETEER_SKIP_DOWNLOAD: "1", TMPDIR: workDir, TMP: workDir, TEMP: workDir },
  })
  rmSync(workDir, { recursive: true, force: true })
  if (res.status !== 0 || !existsSync(outPath)) {
    const detail = (res.stderr || res.stdout || String(res.error || "unknown error")).split("\n").slice(-30).join("\n")
    fail(`Mermaid render FAILED — no image produced. Fix the source and render again.\n\n${detail}`)
  }
}

async function renderSvg(sourcePath, outPath) {
  const errors = []
  const Resvg = await loadResvg()
  if (Resvg) {
    try {
      const svg = readFileSync(sourcePath, "utf8")
      const resvg = new Resvg(svg, {
        fitTo: { mode: "zoom", value: 2 },
        background: "white",
        font: { loadSystemFonts: true, defaultFontFamily: "Liberation Sans", sansSerifFamily: "Liberation Sans" },
      })
      writeFileSync(outPath, resvg.render().asPng())
      return
    } catch (err) {
      errors.push(`resvg: ${err?.message || err}`)
    }
  }
  if (which("rsvg-convert")) {
    const res = spawnSync("rsvg-convert", ["-z", "2", "-b", "white", sourcePath, "-o", outPath], {
      encoding: "utf8",
      timeout: RENDER_TIMEOUT_MS,
    })
    if (res.status === 0 && existsSync(outPath)) return
    errors.push(`rsvg-convert: ${res.stderr || res.error}`)
  }
  const magick = which("magick") || which("convert")
  if (magick) {
    const res = spawnSync(magick, ["-density", "192", "-background", "white", sourcePath, outPath], {
      encoding: "utf8",
      timeout: RENDER_TIMEOUT_MS,
    })
    if (res.status === 0 && existsSync(outPath)) return
    errors.push(`${basename(magick)}: ${res.stderr || res.error}`)
  }
  if (errors.length === 0) errors.push(`no SVG renderer available. Run: bash ${join(SCRIPTS_DIR, "setup.sh")}`)
  fail(`SVG render FAILED — no image produced. Fix the source and render again.\n\n${errors.join("\n")}`)
}

/** A temp root on local disk: /tmp or /var/tmp when writable, else the OS default. */
function localTmpRoot() {
  for (const dir of ["/tmp", "/var/tmp"]) {
    try {
      accessSync(dir, constants.W_OK)
      return dir
    } catch {
      // not writable here
    }
  }
  return tmpdir()
}

function mkdtempLike(group) {
  const dir = join(localTmpRoot(), "learn-viz-work", `${group}-${process.pid}-${Date.now()}`)
  mkdirSync(dir, { recursive: true })
  return dir
}

async function renderTo(sourcePath, outPath) {
  if (!existsSync(sourcePath)) fail(`Source not found: ${sourcePath}`)
  const kind = detectKind(sourcePath)
  mkdirSync(dirname(outPath), { recursive: true })
  if (kind === "svg") await renderSvg(sourcePath, outPath)
  else renderMermaid(sourcePath, outPath)
  return kind
}

/**
 * `skills.config.learn.dir` from the active Hermes profile's config.yaml — the
 * same setting the skills declare. Minimal block-YAML walk (no YAML dependency);
 * undefined when unset or unreadable.
 */
function configuredLearnDir() {
  const home = process.env.HERMES_HOME || join(homedir(), ".hermes")
  let text
  try {
    text = readFileSync(join(home, "config.yaml"), "utf8")
  } catch {
    return undefined
  }
  const want = ["skills", "config", "learn", "dir"]
  const stack = [] // [{ indent, key }]
  for (const raw of text.split("\n")) {
    if (!raw.trim() || raw.trimStart().startsWith("#") || raw.trimStart().startsWith("- ")) continue
    const m = raw.match(/^(\s*)([^:#]+?):\s*(.*)$/)
    if (!m) continue
    const indent = m[1].length
    while (stack.length && stack[stack.length - 1].indent >= indent) stack.pop()
    stack.push({ indent, key: m[2].trim() })
    const path = stack.map((s) => s.key)
    if (path.length === want.length && path.every((k, i) => k === want[i])) {
      const value = m[3].replace(/\s+#.*$/, "").trim().replace(/^(['"])(.*)\1$/, "$2")
      return value ? expandHome(value) : undefined
    }
  }
  return undefined
}

function defaultVizDir() {
  if (process.env.LEARN_VIZ_DIR) return process.env.LEARN_VIZ_DIR
  return join(process.env.LEARN_DIR || configuredLearnDir() || "/workspace/learning", "viz")
}

function expandHome(p) {
  return p === "~" || p.startsWith("~/") ? join(homedir(), p.slice(1)) : p
}

// ── Commands ─────────────────────────────────────────────────────────────────

async function main() {
  const [cmd, ...rest] = process.argv.slice(2)
  const { positional, flags } = parseArgs(rest)

  if (cmd === "render") {
    const source = positional[0] && resolve(positional[0])
    if (!source) fail("usage: viz.mjs render <source.mmd|source.svg> [--out <file.png>]")
    const out = resolve(typeof flags.out === "string" ? flags.out : source.replace(/\.[^./]+$/, "") + ".png")
    const kind = await renderTo(source, out)
    process.stdout.write(
      `Preview rendered (${kind}, not yet published).\npng: ${out}\n` +
        "LOOK at it with vision_analyze before deciding it is correct.\n",
    )
    return
  }

  if (cmd === "publish") {
    const source = positional[0] && resolve(positional[0])
    if (!source || typeof flags.slug !== "string") {
      fail("usage: viz.mjs publish <source.mmd|source.svg> --slug <kebab-topic> [--dir <viz dir>]")
    }
    const vizDir = resolve(expandHome(typeof flags.dir === "string" ? flags.dir : defaultVizDir()))
    const clean =
      flags.slug
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "") || "viz"
    const stagingDir = mkdtempLike("publish")
    const staged = join(stagingDir, "render.png")
    await renderTo(source, staged)
    mkdirSync(vizDir, { recursive: true })
    const filename = `viz-${clean}-${Date.now()}.png`
    const dest = join(vizDir, filename)
    copyFileSync(staged, dest)
    rmSync(stagingDir, { recursive: true, force: true })
    process.stdout.write(`Published.\nfilename: ${filename}\npath: ${dest}\n`)
    return
  }

  if (cmd === "check") {
    const chrome = findChrome()
    const resvg = await loadResvg()
    const lines = [
      `mermaid: ${existsSync(MMDC_BIN) && chrome ? "ok" : "UNAVAILABLE"}` +
        ` (mmdc ${existsSync(MMDC_BIN) ? "installed" : "missing"}, chrome ${chrome || "not found"})`,
      `svg:     ${resvg || which("rsvg-convert") || which("magick") || which("convert") ? "ok" : "UNAVAILABLE"}` +
        ` (resvg ${resvg ? "installed" : "missing"}, rsvg-convert ${which("rsvg-convert") ? "yes" : "no"})`,
      `viz dir: ${defaultVizDir()}`,
    ]
    process.stdout.write(lines.join("\n") + "\n")
    return
  }

  fail(
    "usage:\n  viz.mjs render  <source> [--out <png>]\n  viz.mjs publish <source> --slug <topic> [--dir <viz dir>]\n  viz.mjs check",
  )
}

main().catch((err) => fail(String(err?.stack || err)))
