#!/usr/bin/env bash
# Keeps the repository's GitHub settings in files:
#
#   .github/rulesets/<name>.json  one file per ruleset, in the format GitHub uses to export and import rulesets
#   .github/repo-settings.json    other settings (auto merge, branch cleanup, secret scanning, ...), in the format
#                                 of `PATCH /repos/{owner}/{repo}`; settings not in the file are left alone
#
#   scripts/github-settings.sh export [--force]  # write the live settings to the files
#   scripts/github-settings.sh diff              # show how the files differ from GitHub
#   scripts/github-settings.sh apply             # send the files to GitHub
#
# A ruleset file matches a ruleset by its "name". export skips files that already exist unless --force is given,
# so it never overwrites local edits; for repo-settings.json it exports the fields the file already lists. apply
# leaves rulesets without a file alone; delete those in the UI.
#
# Needs gh, logged in as a repository admin, and jq.
set -euo pipefail

REPO="${REPO:-TheRealArthurDent/infrastructure}"
ROOT="$(git rev-parse --show-toplevel)"
RULESETS_DIR="$ROOT/.github/rulesets"
SETTINGS_FILE="$ROOT/.github/repo-settings.json"

# The fields GitHub accepts when creating or updating a ruleset.
FIELDS='{name, target, enforcement, conditions, rules, bypass_actors}'

# --- rulesets ---------------------------------------------------------------

live_ids() {
  gh api "repos/$REPO/rulesets?includes_parents=false" --paginate --jq '.[].id'
}

live_ruleset() {
  gh api "repos/$REPO/rulesets/$1" | jq --sort-keys "$FIELDS"
}

id_by_name() {
  gh api "repos/$REPO/rulesets?includes_parents=false" --paginate \
    --jq ".[] | select(.name == \"$1\") | .id"
}

rulesets_export() {
  local force="${1:-}"
  mkdir -p "$RULESETS_DIR"
  for id in $(live_ids); do
    local json name file
    json="$(live_ruleset "$id")"
    name="$(jq -r .name <<<"$json")"
    file="$RULESETS_DIR/$name.json"
    if [[ -e "$file" && "$force" != "--force" ]]; then
      echo "skip   ruleset $name (file exists, use --force to overwrite)"
      continue
    fi
    jq . <<<"$json" >"$file"
    echo "export ruleset $name -> ${file#"$ROOT/"}"
  done
}

rulesets_diff() {
  local changed=0
  for file in "$RULESETS_DIR"/*.json; do
    local name id
    name="$(jq -r .name "$file")"
    id="$(id_by_name "$name")"
    if [[ -z "$id" ]]; then
      echo "new    ruleset $name (not on GitHub yet)"
      changed=1
      continue
    fi
    if ! diff -u --label "github/ruleset/$name" --label "file/ruleset/$name" \
      <(live_ruleset "$id") <(jq --sort-keys "$FIELDS" "$file"); then
      changed=1
    fi
  done
  return "$changed"
}

rulesets_apply() {
  for file in "$RULESETS_DIR"/*.json; do
    local name id
    name="$(jq -r .name "$file")"
    id="$(id_by_name "$name")"
    if [[ -z "$id" ]]; then
      jq "$FIELDS" "$file" | gh api --method POST "repos/$REPO/rulesets" --input - >/dev/null
      echo "create ruleset $name"
    else
      jq "$FIELDS" "$file" | gh api --method PUT "repos/$REPO/rulesets/$id" --input - >/dev/null
      echo "update ruleset $name"
    fi
  done
}

# --- repository settings ----------------------------------------------------

# The live values of exactly the fields the file sets, nested ones included.
live_settings() {
  gh api "repos/$REPO" | jq --sort-keys --slurpfile want "$SETTINGS_FILE" '
    . as $live
    | [$want[0] | paths(scalars)] as $paths
    | reduce $paths[] as $p ({}; setpath($p; $live | getpath($p)))'
}

settings_export() {
  if [[ "${1:-}" != "--force" ]]; then
    echo "skip   repo-settings.json (file exists, use --force to overwrite)"
    return
  fi
  local live
  live="$(live_settings)"
  jq . <<<"$live" >"$SETTINGS_FILE"
  echo "export repo settings -> ${SETTINGS_FILE#"$ROOT/"}"
}

settings_diff() {
  diff -u --label "github/repo-settings" --label "file/repo-settings" \
    <(live_settings) <(jq --sort-keys . "$SETTINGS_FILE")
}

settings_apply() {
  gh api --method PATCH "repos/$REPO" --input "$SETTINGS_FILE" >/dev/null
  echo "update repo settings"
}

# --- commands ---------------------------------------------------------------

cmd_diff() {
  local changed=0
  rulesets_diff || changed=1
  settings_diff || changed=1
  [[ "$changed" == 0 ]] && echo "GitHub matches the files"
  return "$changed"
}

case "${1:-}" in
  export)
    rulesets_export "${2:-}"
    settings_export "${2:-}"
    ;;
  diff) cmd_diff ;;
  apply)
    rulesets_apply
    settings_apply
    ;;
  *)
    echo "usage: $0 export [--force] | diff | apply" >&2
    exit 2
    ;;
esac
