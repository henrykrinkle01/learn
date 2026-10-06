#!/usr/bin/env bash
# Install the learn system into a Hermes profile.
#
#   ./install.sh [--skills-dir DIR] [--no-skills] [--no-plugin] [--no-viz]
#
#   skills  symlink <skills-dir>/learn -> <repo>/skills
#           (default <skills-dir>: $HERMES_HOME/skills; point it at a directory
#           listed in skills.external_dirs to share the skills between profiles)
#   plugin  symlink $HERMES_HOME/plugins/learn -> <repo>/plugin and enable it
#           (adds the `quiz` and `md_log` tools)
#   viz     run skills/visualize/scripts/setup.sh (render deps for diagrams)
#
# HERMES_HOME selects the profile (default ~/.hermes), e.g.
#   HERMES_HOME=~/.hermes/profiles/tutor ./install.sh
# Idempotent: safe to re-run. Restart a running gateway/WebUI afterwards so it
# loads the plugin; new CLI sessions pick everything up immediately.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKILLS_DIR="$HERMES_HOME/skills"
DO_SKILLS=1 DO_PLUGIN=1 DO_VIZ=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skills-dir) SKILLS_DIR="$2"; shift 2 ;;
    --no-skills) DO_SKILLS=0; shift ;;
    --no-plugin) DO_PLUGIN=0; shift ;;
    --no-viz) DO_VIZ=0; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

link() {  # link <target> <link-path>
  local target="$1" path="$2"
  if [[ -L "$path" ]]; then
    ln -sfn "$target" "$path"
  elif [[ -e "$path" ]]; then
    echo "  ! $path exists and is not a symlink — leaving it alone" >&2
    return 1
  else
    mkdir -p "$(dirname "$path")"
    ln -s "$target" "$path"
  fi
  echo "  $path -> $target"
}

if (( DO_SKILLS )); then
  echo "Skills:"
  link "$REPO/skills" "$SKILLS_DIR/learn"
fi

if (( DO_PLUGIN )); then
  echo "Plugin (HERMES_HOME=$HERMES_HOME):"
  link "$REPO/plugin" "$HERMES_HOME/plugins/learn"
  HERMES_HOME="$HERMES_HOME" hermes plugins enable --no-allow-tool-override learn
fi

if (( DO_VIZ )); then
  echo "Diagram rendering:"
  bash "$REPO/skills/visualize/scripts/setup.sh" || echo "  ! viz setup failed — diagrams unavailable until fixed (teaching still works)" >&2
fi
