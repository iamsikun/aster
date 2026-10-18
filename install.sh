#!/usr/bin/env bash
# Aster installer: one install that stays current.
# Installs the skill for every detected agent and the `aster` command (with uv).
#
#   curl -fsSL https://raw.githubusercontent.com/iamsikun/aster/stable/install.sh | bash
#
# Commands:
#   install.sh [install] [--no-auto-update] [--no-cli]
#                          clone, link into every detected agent, install the aster CLI
#                          with uv, and schedule daily updates
#   install.sh update      fast-forward to the latest stable release (run by the scheduler)
#   install.sh status      show installed version, links, CLI, and last update
#   install.sh uninstall   remove links, the CLI, the scheduled job, and the clone
#
# Environment overrides:
#   ASTER_HOME  where the clone lives            (default: ~/.local/share/aster)
#   ASTER_REF   branch or tag to track           (default: stable)
#   ASTER_REPO  git URL to clone from            (default: https://github.com/iamsikun/aster.git)

set -euo pipefail

ASTER_HOME="${ASTER_HOME:-$HOME/.local/share/aster}"
ASTER_REF="${ASTER_REF:-stable}"
ASTER_REPO="${ASTER_REPO:-https://github.com/iamsikun/aster.git}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/aster"
LOG_FILE="$STATE_DIR/update.log"
LAUNCHD_LABEL="io.github.iamsikun.aster.update"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/$LAUNCHD_LABEL.plist"
CRON_MARKER="# aster-auto-update"
CLI_STATE="$STATE_DIR/cli"   # path of the uv that installed the CLI; present only if we own it
CLI_METADATA="$STATE_DIR/cli-metadata"  # fingerprint of the last successfully installed pyproject.toml
LAST_ERROR="$STATE_DIR/last-error"
CLONE_STATE="$STATE_DIR/clone"
TOOL_NAME="aster-scout"        # the package name uv installs the aster command under

say() { printf 'aster: %s\n' "$*"; }
die() { printf 'aster: error: %s\n' "$*" >&2; exit 1; }

skill_src() { printf '%s/skills/aster' "$ASTER_HOME"; }

# Skill directories of agents that appear to be installed. ~/.agents/skills is the
# shared location read by Codex, Gemini CLI, and Cursor.
agent_skill_dirs() {
  [ -d "$HOME/.claude" ] && printf '%s\n' "$HOME/.claude/skills"
  if [ -d "$HOME/.agents" ] || [ -d "$HOME/.codex" ] || [ -d "$HOME/.gemini" ] || [ -d "$HOME/.cursor" ]; then
    printf '%s\n' "$HOME/.agents/skills"
  fi
  [ -d "$HOME/.copilot" ] && printf '%s\n' "$HOME/.copilot/skills"
  [ -d "$HOME/.config/opencode" ] && printf '%s\n' "$HOME/.config/opencode/skills"
  return 0
}

installed_version() {
  sed -n 's/^  version: "\(.*\)"$/\1/p' "$(skill_src)/SKILL.md" 2>/dev/null | head -n1
}

link_skill() {
  local found=0 dir target
  while IFS= read -r dir; do
    [ -n "$dir" ] || continue
    found=1
    target="$dir/aster"
    mkdir -p "$dir"
    if [ -L "$target" ]; then
      if [ "$(readlink "$target")" = "$(skill_src)" ]; then
        say "linked $target"
      else
        say "kept $target (symlink owned elsewhere: $(readlink "$target"))"
      fi
    elif [ -e "$target" ]; then
      say "skipped $target (already exists and is not a symlink; remove it to let Aster manage it)"
    else
      ln -s "$(skill_src)" "$target"
      say "linked $target"
    fi
  done < <(agent_skill_dirs)
  if [ "$found" -eq 0 ]; then
    say "no supported agent detected; link manually, e.g.:"
    say "  ln -s $(skill_src) ~/.claude/skills/aster"
  fi
}

unlink_skill() {
  local dir target
  for dir in "$HOME/.claude/skills" "$HOME/.agents/skills" "$HOME/.copilot/skills" "$HOME/.config/opencode/skills"; do
    target="$dir/aster"
    if [ -L "$target" ] && [ "$(readlink "$target")" = "$(skill_src)" ]; then
      rm "$target"
      say "removed $target"
    fi
  done
}

# The scheduler runs with a minimal PATH, so also look where uv's installers put it.
find_uv() {
  local candidate
  for candidate in "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" \
                   /opt/homebrew/bin/uv /usr/local/bin/uv; do
    [ -n "$candidate" ] && [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
  done
  return 1
}

metadata_fingerprint() { git -C "$ASTER_HOME" hash-object pyproject.toml; }

record_error() {
  mkdir -p "$STATE_DIR"
  printf '%s\n' "$*" > "$LAST_ERROR"
  say "$*"
}

cli_owned() {
  local receipt
  receipt="$("$1" tool dir)/$TOOL_NAME/uv-receipt.toml"
  [ -f "$receipt" ] && grep -qF "editable = \"$ASTER_HOME\"" "$receipt"
}

record_cli_success() {
  mkdir -p "$STATE_DIR"
  printf '%s\n' "$1" > "$CLI_STATE"
  metadata_fingerprint > "$CLI_METADATA"
  rm -f "$LAST_ERROR"
}

# Editable install from the clone, so each update reaches the CLI with no extra step.
install_cli() {
  local uv receipt
  if ! uv="$(find_uv)"; then
    say "uv not found; skipped the aster CLI (install uv from https://docs.astral.sh/uv/, then rerun install.sh)"
    say "the skill still works in web-only mode without it"
    return 0
  fi
  receipt="$("$uv" tool dir)/$TOOL_NAME/uv-receipt.toml"
  if [ -f "$receipt" ] && ! cli_owned "$uv"; then
    say "kept the existing aster CLI, which was installed from elsewhere (see 'uv tool list')"
    return 0
  fi
  if ! "$uv" tool install --quiet --reinstall --editable "$ASTER_HOME"; then
    record_error "could not install the aster CLI; rerun install.sh to retry; the skill works without it"
    return 0
  fi
  record_cli_success "$uv"
  say "installed the aster CLI with uv"
  local bin
  bin="$("$uv" tool dir --bin)"
  case ":$PATH:" in
    *":$bin:"*) ;;
    *) say "add $bin to your PATH to run 'aster'" ;;
  esac
}

# Reinstalling refreshes the package metadata (version, dependencies, entry points).
refresh_cli() {
  [ -f "$CLI_STATE" ] || return 0
  local uv receipt
  uv="$(cat "$CLI_STATE")"
  [ -x "$uv" ] || uv="$(find_uv)" || { record_error "uv not found; aster CLI refresh pending"; return 1; }
  receipt="$("$uv" tool dir)/$TOOL_NAME/uv-receipt.toml"
  if [ -f "$receipt" ] && ! cli_owned "$uv"; then
    record_error "aster CLI receipt no longer belongs to $ASTER_HOME; kept the external installation"
    return 1
  fi
  if [ -f "$receipt" ] && [ -f "$CLI_METADATA" ] && [ "$(cat "$CLI_METADATA")" = "$(metadata_fingerprint)" ]; then
    return 0
  fi
  if "$uv" tool install --quiet --reinstall --editable "$ASTER_HOME"; then
    record_cli_success "$uv"
    say "refreshed the aster CLI"
  else
    record_error "could not refresh the aster CLI; next update will retry"
    return 1
  fi
}

uninstall_cli() {
  [ -f "$CLI_STATE" ] || return 0
  local uv
  uv="$(cat "$CLI_STATE")"
  [ -x "$uv" ] || uv="$(find_uv)" || { die "uv not found; remove the CLI with 'uv tool uninstall $TOOL_NAME' before uninstalling"; }
  if cli_owned "$uv"; then
    "$uv" tool uninstall "$TOOL_NAME" >/dev/null 2>&1 || die "could not uninstall the CLI; kept the clone and ownership state"
    say "removed the aster CLI"
  else
    say "kept the aster CLI installed elsewhere"
  fi
  rm -f "$CLI_STATE" "$CLI_METADATA"
}

schedule_updates() {
  local script="$ASTER_HOME/install.sh"
  mkdir -p "$STATE_DIR"
  case "$(uname -s)" in
    Darwin)
      mkdir -p "$(dirname "$LAUNCHD_PLIST")"
      cat > "$LAUNCHD_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LAUNCHD_LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$script</string><string>update</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ASTER_HOME</key><string>$ASTER_HOME</string>
    <key>ASTER_REF</key><string>$ASTER_REF</string>
  </dict>
  <key>StartInterval</key><integer>86400</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG_FILE</string>
  <key>StandardErrorPath</key><string>$LOG_FILE</string>
</dict>
</plist>
EOF
      launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$LAUNCHD_PLIST"
      say "daily updates scheduled (launchd: $LAUNCHD_LABEL)"
      ;;
    Linux)
      command -v crontab >/dev/null || { say "crontab not found; run '$script update' to update manually"; return 0; }
      local line="17 9 * * * ASTER_HOME='$ASTER_HOME' ASTER_REF='$ASTER_REF' /bin/bash '$script' update >>'$LOG_FILE' 2>&1 $CRON_MARKER"
      { crontab -l 2>/dev/null | grep -v "$CRON_MARKER" || true; printf '%s\n' "$line"; } | crontab -
      say "daily updates scheduled (cron)"
      ;;
    *)
      say "automatic updates not supported on this OS; run '$script update' to update manually"
      ;;
  esac
}

unschedule_updates() {
  if [ -f "$LAUNCHD_PLIST" ]; then
    launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
    rm -f "$LAUNCHD_PLIST"
    say "removed launchd job"
  fi
  if command -v crontab >/dev/null && crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; then
    { crontab -l 2>/dev/null | grep -v "$CRON_MARKER" || true; } | crontab -
    say "removed cron job"
  fi
}

cmd_install() {
  local auto=1 cli=1
  for arg in "$@"; do
    case "$arg" in
      --no-auto-update) auto=0 ;;
      --no-cli) cli=0 ;;
      *) die "unknown option: $arg" ;;
    esac
  done
  command -v git >/dev/null || die "git is required"

  if [ -d "$ASTER_HOME/.git" ]; then
    say "existing install found at $ASTER_HOME; updating"
    cmd_update
  else
    mkdir -p "$(dirname "$ASTER_HOME")"
    git clone --quiet --branch "$ASTER_REF" "$ASTER_REPO" "$ASTER_HOME"
    mkdir -p "$STATE_DIR"
    printf '%s\n' "$ASTER_HOME" > "$CLONE_STATE"
    say "installed Aster $(installed_version) ($ASTER_REF) to $ASTER_HOME"
  fi

  link_skill
  if [ "$cli" -eq 1 ]; then
    install_cli
  fi
  if [ "$auto" -eq 1 ]; then
    schedule_updates
  else
    unschedule_updates
    say "automatic updates off; run '$ASTER_HOME/install.sh update' to update"
  fi
  say "done. Restart your agent to pick up the skill."
}

# Fast-forward only: never discards local edits, never moves to unreleased work.
cmd_update() {
  [ -d "$ASTER_HOME/.git" ] || die "Aster is not installed at $ASTER_HOME"
  local before after
  before="$(installed_version)"
  if [ -n "$(git -C "$ASTER_HOME" status --porcelain)" ]; then
    record_error "local changes in $ASTER_HOME; keeping $before"
    return 1
  fi
  if ! git -C "$ASTER_HOME" fetch --quiet --tags origin "$ASTER_REF" 2>/dev/null; then
    say "$(date '+%F %T') offline or fetch failed; keeping $before"
    refresh_cli
    return
  fi
  if ! git -C "$ASTER_HOME" merge --quiet --ff-only FETCH_HEAD 2>/dev/null; then
    record_error "local history in $ASTER_HOME prevents a fast-forward; keeping $before"
    return 1
  fi
  after="$(installed_version)"
  refresh_cli || return 1
  mkdir -p "$STATE_DIR"
  rm -f "$LAST_ERROR"
  date '+%F %T' > "$STATE_DIR/last-update"
  if [ "$before" = "$after" ]; then
    say "$(date '+%F %T') up to date ($after)"
  else
    say "$(date '+%F %T') updated $before -> $after (see $ASTER_HOME/CHANGELOG.md)"
  fi
}

cmd_status() {
  [ -d "$ASTER_HOME/.git" ] || { say "not installed at $ASTER_HOME"; return 0; }
  say "version:     $(installed_version)"
  say "tracking:    $ASTER_REF ($(git -C "$ASTER_HOME" rev-parse --short HEAD))"
  say "location:    $ASTER_HOME"
  say "last update: $(cat "$STATE_DIR/last-update" 2>/dev/null || echo never)"
  [ ! -f "$LAST_ERROR" ] || say "last error:  $(cat "$LAST_ERROR")"
  local dir
  for dir in "$HOME/.claude/skills" "$HOME/.agents/skills" "$HOME/.copilot/skills" "$HOME/.config/opencode/skills"; do
    [ -L "$dir/aster" ] && say "linked:      $dir/aster -> $(readlink "$dir/aster")"
  done
  if [ -f "$CLI_STATE" ]; then
    say "cli:         managed receipt recorded (run aster doctor to check sources)"
    if [ ! -f "$CLI_METADATA" ] || [ "$(cat "$CLI_METADATA")" != "$(metadata_fingerprint)" ]; then
      say "cli refresh: pending; run install.sh update"
    fi
  else
    say "cli:         not managed by the installer"
  fi
  if [ -f "$LAUNCHD_PLIST" ] || { command -v crontab >/dev/null && crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; }; then
    say "auto-update: on (daily)"
  else
    say "auto-update: off"
  fi
}

cmd_uninstall() {
  unschedule_updates
  unlink_skill
  uninstall_cli
  if [ -d "$ASTER_HOME/.git" ]; then
    if [ ! -f "$CLONE_STATE" ] || [ "$(cat "$CLONE_STATE")" != "$ASTER_HOME" ]; then
      say "kept $ASTER_HOME because clone ownership is not recorded (including older installs)"
    elif ! git -C "$ASTER_HOME" status --porcelain >/dev/null 2>&1; then
      say "kept $ASTER_HOME because its Git state could not be checked"
    elif [ -n "$(git -C "$ASTER_HOME" status --porcelain --ignored | grep -v '__pycache__/$')" ]; then
      say "kept $ASTER_HOME because it has local or ignored files; delete it yourself when done"
    elif ! git -C "$ASTER_HOME" show-ref --verify --quiet refs/remotes/origin/"$ASTER_REF"; then
      say "kept $ASTER_HOME because the tracked remote branch cannot establish published history"
    elif [ -n "$(git -C "$ASTER_HOME" rev-list --all --not --remotes=origin)" ]; then
      say "kept $ASTER_HOME because it has unpublished commits; delete it yourself when done"
    else
      rm -rf "$ASTER_HOME"
      say "removed $ASTER_HOME"
    fi
  fi
  rm -rf "$STATE_DIR"
  say "kept your configuration and collected data (default: ~/.config/aster and ~/.local/share/aster-data)"
  say "Aster uninstalled"
}

main() {
  local cmd="${1:-install}"
  case "$cmd" in
    install) shift || true; cmd_install "$@" ;;
    --no-auto-update|--no-cli) cmd_install "$@" ;;
    update) cmd_update ;;
    status) cmd_status ;;
    uninstall) cmd_uninstall ;;
    -h|--help|help) sed -n '2,18p' "$0" 2>/dev/null || true ;;
    *) die "unknown command: $cmd (try: install, update, status, uninstall)" ;;
  esac
}

main "$@"
