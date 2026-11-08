#!/usr/bin/env bash
# Keeps the repository's rulesets in .github/rulesets/<name>.json, one file per
# ruleset, in the format GitHub uses to export and import rulesets.
#
#   scripts/rulesets.sh export [--force]  # write the live rulesets to files
#   scripts/rulesets.sh diff              # show how the files differ from GitHub
#   scripts/rulesets.sh apply             # create or update rulesets from files
#
# A file matches a ruleset by its "name". export skips files that already
# exist unless --force is given, so it never overwrites local edits. apply
# leaves rulesets without a file alone; delete those in the UI.
#
# Needs gh, logged in as a repository admin, and jq.
set -euo pipefail

REPO="${REPO:-TheRealArthurDent/infrastructure}"
DIR="$(git rev-parse --show-toplevel)/.github/rulesets"

# The fields GitHub accepts when creating or updating a ruleset.
FIELDS='{name, target, enforcement, conditions, rules, bypass_actors}'

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

cmd_export() {
  local force="${1:-}"
  mkdir -p "$DIR"
  for id in $(live_ids); do
    local json name file
    json="$(live_ruleset "$id")"
    name="$(jq -r .name <<<"$json")"
    file="$DIR/$name.json"
    if [[ -e "$file" && "$force" != "--force" ]]; then
      echo "skip   $name (file exists, use --force to overwrite)"
      continue
    fi
    jq . <<<"$json" >"$file"
    echo "export $name -> ${file#"$(git rev-parse --show-toplevel)/"}"
  done
}

cmd_diff() {
  local changed=0
  for file in "$DIR"/*.json; do
    local name id
    name="$(jq -r .name "$file")"
    id="$(id_by_name "$name")"
    if [[ -z "$id" ]]; then
      echo "new    $name (not on GitHub yet)"
      changed=1
      continue
    fi
    if ! diff -u --label "github/$name" --label "file/$name" \
      <(live_ruleset "$id") <(jq --sort-keys "$FIELDS" "$file"); then
      changed=1
    fi
  done
  return "$changed"
}

cmd_apply() {
  for file in "$DIR"/*.json; do
    local name id
    name="$(jq -r .name "$file")"
    id="$(id_by_name "$name")"
    if [[ -z "$id" ]]; then
      jq "$FIELDS" "$file" | gh api --method POST "repos/$REPO/rulesets" --input - >/dev/null
      echo "create $name"
    else
      jq "$FIELDS" "$file" | gh api --method PUT "repos/$REPO/rulesets/$id" --input - >/dev/null
      echo "update $name"
    fi
  done
}

case "${1:-}" in
  export) cmd_export "${2:-}" ;;
  diff) cmd_diff ;;
  apply) cmd_apply ;;
  *)
    echo "usage: $0 export [--force] | diff | apply" >&2
    exit 2
    ;;
esac
