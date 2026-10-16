# raspberry – smart home infrastructure on Raspberry Pi

Docker-based smart home stack (nginx reverse proxy, Pi-hole ×2, unbound, portainer, resticprofile backups,
container health monitoring) for a Raspberry Pi running Debian/Raspberry Pi OS trixie (arm64).

## State of the repo

Everything is deployed with Ansible (`ansible/`); the former bash scripts (`deploy.sh` → `setup/setup.sh` →
`run.sh`) are gone (#238).

- **Ansible** (`ansible/`) handles host setup: ssh port, base packages, sysctl, sudoers, rsnapshot, Docker, secrets
  (`.env` files from KeePass), certificates, and Pi-hole DNS records, lists, groups, clients and domains through its
  REST API.
- The **`stack` role** deploys the Docker stack itself (nginx/restic config as Jinja templates, docker networks,
  `docker compose up`), in `bootstrap.yaml` under the tag `stack` (#238).

Review findings from the migration are GitHub issues. Still open are #237 (idempotency), #238 (migration) and #239
(refactor). Check the open issues before changing an affected area, and reference them in commits.

## Layout

- `ansible/` – Ansible: playbooks (`bootstrap.yaml`, `check-certificates.yaml`, `setup-pihole.yaml`,
  `update.yaml`), `roles/`, `tasks/` (Pi-hole API helpers), `configs/pihole.yaml`, `inventory/hosts.yaml`,
  `lookup_plugins/keepass.py`
- `ansible/molecule/default/` – container-based role tests
- `ansible/tests/unit/` – pytest tests (KeePass lookup, `--limit` guard, templates, Claude hook, rulesets, repo hygiene)
- `.claude/` – Claude Code settings, the guard hook (`hooks/guard.py`) and skills
- `.github/rulesets/` + `scripts/rulesets.sh` – rulesets for `main`, applied by the user (`rulesets.sh apply`)

## Hosts

| Host | Purpose |
| --- | --- |
| `raspi` (192.168.1.2) | **Production** smart home. Only the user runs anything against it; hand them the commands. |
| `testbed` (192.168.1.7) | Test Pi for real integration runs, done by the user from the home network: propose the commands (`--check --diff`, real run, second run with `changed=0`). |

Always pass `--limit <host>`. Before a real run, do `--check --diff` first.

A `PreToolUse` hook (`.claude/hooks/guard.py`, tests in `ansible/tests/unit/test_guard_hook.py`) blocks the risky
actions for Claude: anything against raspi, playbooks without `--limit` or with `all`, pushes to `main`, force pushes,
PR merges, storing or forgetting the KeePass master password, printing `KEEPASS_PASSWORD`, `git add -f`, and edits to
the hook and settings themselves. Don't work around a block; give the user the command or the proposed change instead.

## Secrets

Secrets come from a KeePass database through the custom `keepass` lookup plugin (pykeepass). Its paths are in the
user's git-ignored `ansible/inventory/group_vars/all/local.yaml`; the master password comes from `KEEPASS_PASSWORD`
(`ansible/bin/with-keepass` sets it from Windows Credential Manager) or a prompt. There is no ansible-vault (#241
was dropped). Never run `with-keepass --store` or read the stored password yourself. Never commit secrets, and
never print them: every task that handles secret values needs `no_log: true`.

## Commands

Tooling lives in a venv at the repo root:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pre-commit install
.venv/bin/pytest ansible/tests/unit                    # unit tests, no Docker
(cd ansible && ../.venv/bin/molecule test)             # role tests in a trixie container, needs Docker, ~3–5 min
(cd ansible && ../.venv/bin/molecule converge)         # faster loop while developing; then verify / destroy
.venv/bin/ansible-lint --strict ansible/               # lint, must pass with 0 findings (CI blocks)
```

Reports land in `test-results/` (JUnit XML). CI (`.github/workflows/ci.yaml`) runs ansible-lint, pytest and Molecule
on every PR, weekly on `main` and on demand. Its `changes` job skips the jobs a PR doesn't touch (docs-only PRs run
none of them); a skipped job counts as passed for required checks, a workflow skipped by `on.paths` would block the
merge, so never filter by `on.paths` (`test_rulesets.py` checks this). `pytest -n auto` runs the tests in parallel.

When writing or changing Ansible roles, follow the `ansible-role` skill in `.claude/skills/`.

## Conventions

- Commit messages: conventional style (`feat:`, `fix:`, `test:`, `chore:`), referencing issues (`Refs #238`).
- Never commit or push to `main`. Branch off `main` and open a PR into `main`; the user merges.
- Container images in `ansible/roles/stack/templates/docker-compose.yaml.j2` are pinned and updated by Renovate
  (`renovate.json` points its docker-compose manager at the template); keep them pinned.
- Work test-driven, unless something speaks against it (then say why). Write the test first and watch it fail
  for the right reason before changing the code: pytest for Python, `verify.yaml` checks for Ansible roles,
  run against the old role. Since a Molecule run takes minutes, one red/green cycle may cover a group of
  related checks.
- YAML files use the `.yaml` extension, not `.yml`. Exceptions only where a tool requires a fixed name
  (e.g. Molecule's `molecule.yml`).
- Everything in the repo is written in English (code, comments, docs, commit messages), even when the user
  chats in German.

## Working with the user

- Before changing code, give a short plan summary (what, which files, how tested).
- Answer in the language of the user's message (German or English); the repo itself stays English.

- Whenever a task would benefit from a skill you don't have, say so: which skill, what it would be for, and
  whether a ready-made one exists or it would have to be written.
