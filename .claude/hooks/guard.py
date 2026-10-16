#!/usr/bin/env python3
"""PreToolUse guard for Claude Code in this repo.

Reads the hook event (JSON) on stdin. Exit 2 blocks the tool call and shows stderr to Claude;
exit 0 lets it through. Prose rules in CLAUDE.md can be overlooked, this can't.
"""
import json
import re
import sys

event = json.load(sys.stdin)
tool = event.get("tool_name", "")
args = event.get("tool_input", {}) or {}


def block(reason):
    print(f"Blocked by .claude/hooks/guard.py: {reason}", file=sys.stderr)
    sys.exit(2)


PROD = r"(\braspi\b|192\.168\.1\.2\b)"
# -f, --force, or -f among combined short flags (-Af); not --force-with-lease.
FORCE = r"\s(-[a-zA-Z]*f[a-zA-Z]*|--force)(?=\s|$)"


def runs(program):
    """Regex for `program` run as a command (also via a path, sudo, bash, env vars or bin/with-keepass),
    not merely named as an argument like `cat deploy.sh` or `ansible/`."""
    return (
        r"(?:^|[;&|(]|\$\()\s*(?:\w+=\S*\s+)*(?:(?:sudo|bash|sh|exec|\S*with-keepass)\s+)*(?:\S*/)?"
        + program
        + r"(?=\s|$|[;&|)])"
    )

if tool == "Bash":
    cmd = args.get("command", "")

    # 1. Production: the user runs everything against raspi.
    if re.search(runs(r"ansible(?:-playbook)?"), cmd):
        if not re.search(r"(--limit|-l)[ =]", cmd):
            block("ansible without --limit. Use --limit testbed.")
        if re.search(r"(--limit|-l)[ =]['\"]?[^ ]*(" + PROD[1:-1] + r"|\ball\b|\*)", cmd):
            block("ansible against raspi (production) or all hosts. Hand the user the command instead.")
    if re.search(runs(r"deploy\.sh"), cmd):
        if not re.search(runs(r"deploy\.sh") + r"\s+testbed\b", cmd):
            block("deploy.sh defaults to raspi (production). Only 'deploy.sh testbed ...' is allowed.")
    if re.search(runs(r"(?:ssh|scp|rsync|sftp)"), cmd) and re.search(PROD, cmd):
        block("direct access to raspi (production).")

    # 2. main branch: work on a branch, PR into main.
    # Flags are checked per command, so `rm -f x && git push` or a branch named `...-f75dkr` don't count.
    for part in re.split(r"&&|\|\||[;|\n]", cmd):
        if re.search(r"\bgit\s+push\b", part):
            if re.search(r"(\s|:)(refs/heads/)?main\b", part) or re.search(r"\s(--mirror|--all)\b", part):
                block("push to main. Push your feature branch and open a PR.")
            if re.search(FORCE, part):
                block("force push. Use --force-with-lease on your own branch, if at all.")
        if re.search(r"\bgit\s+add\b", part) and re.search(FORCE, part):
            block("git add --force bypasses .gitignore (local.yaml, .env).")
        if re.search(r"\bgit\s+(add|commit)\b", part) and re.search(r"(local\.yaml|\.env|\.kdbx|\.keyx?)(\s|$)", part):
            block("committing local KeePass config or secrets.")

    # 3. Secrets: never read the master password, never commit local KeePass config.
    if re.search(r"with-keepass\s+--(store|forget)", cmd):
        block("with-keepass --store/--forget is the user's job.")
    if re.search(r"KEEPASS_PASSWORD", cmd) and re.search(r"\b(echo|printf|printenv|env|set|cat|export)\b", cmd):
        block("touching KEEPASS_PASSWORD. The user types it into the prompt.")

    # 4. Guardrails protect themselves.
    if re.search(r"\.claude/(settings[^ ]*\.json|hooks/)", cmd) and re.search(
        r"(>|\bsed\s+-i|\brm\b|\bmv\b|\bcp\b|\btee\b|\bchmod\b|\btruncate\b)", cmd
    ):
        block("changing Claude's own guardrails from the shell. Propose the change to the user.")

elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
    path = args.get("file_path", "") or args.get("notebook_path", "")
    if re.search(r"\.claude/(settings[^/]*\.json|hooks/)", path):
        block("editing Claude's own guardrails. Propose the change to the user.")
    if re.search(r"(\.kdbx|\.keyx?)$", path):
        block("writing KeePass files.")

elif tool.startswith("mcp__github__"):
    # GitHub MCP tools write without git, so the git push rule doesn't see them.
    if tool.endswith(("merge_pull_request", "enable_pr_auto_merge")):
        block("merging is the user's decision.")
    if tool.endswith(("push_files", "create_or_update_file", "delete_file")):
        if args.get("branch") in ("main", "refs/heads/main"):
            block("writing to main through the GitHub API.")

sys.exit(0)
