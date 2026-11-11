# infrastructure – smart home infrastructure (Raspberry Pi)

Docker-based smart home stack (nginx reverse proxy, Pi-hole ×2, unbound, portainer, resticprofile backups,
container health monitoring) for the Raspberry Pis `raspi`, `dungeon` and `testbench`, running Debian/Raspberry Pi OS
trixie (arm64).

## State of the repo

Everything is deployed with Ansible (`ansible/`); the former bash scripts (`deploy.sh` → `setup/setup.sh` →
`run.sh`) are gone (raspberry#238).

- **Ansible** (`ansible/`) handles host setup: ssh port, base packages, sysctl, sudoers, rsnapshot, Docker, secrets
  (`.env` files and the restic key pair from KeePass), certificates, the backup SSD of `dungeon` (`backup_target`),
  and Pi-hole DNS records, lists, groups, clients and domains through its REST API.
- The **`stack` role** assembles the Docker stack from the component roles (networks, merged compose file, restore,
  `docker compose up`); the component roles carry the templates. It runs in
  `bootstrap.yaml` under the tag `stack` (raspberry#238).

The repo was created on 2026-11-08 from the private `TheRealArthurDent/raspberry` with a cleaned history
(no private e-mail addresses, no `.env` files). Issues and PRs from before that live there; references in old
commits and in the code point there (`raspberry#238`). Still open, moved here: #5 (refactor), #1–#4 (nginx and
landing page ideas). Check the open issues before changing an affected area, and reference them in commits.
Never push from a clone of raspberry: it would bring back the old history. `dungeon` (Pi 5, private cloud and backup
target) is in the inventory since #15; the fork `private-cloud-infrastructure` it was deployed from is obsolete.
Apps for dungeon (OpenCloud, ...) go into their own repo, like smart-home for raspi.

## Layout

- `ansible/` – Ansible: playbooks (`bootstrap.yaml`, `check-certificates.yaml`, `setup-pihole.yaml`,
  `update.yaml`), `roles/`, `tasks/` (Pi-hole API helpers), `configs/pihole.yaml`,
  `inventory/` (`hosts.yaml`, `group_vars/all/`, `host_vars/`), `lookup_plugins/keepass.py`
- `ansible/roles/` – besides host roles (`ssh`, `base`, `docker`, `secrets`, `argon_fan`, `certificate_check`), the
  stack components `reverse_proxy`, `portainer`, `unbound`, `pihole`, `outbound`, `resticprofile` and
  `healthmonitor`, each with its compose fragment (`templates/docker-compose.yaml.j2`); `stack` merges them, restores
  and runs `docker compose up`. `stack_components` (default: all seven) switches them per host in `host_vars`:
  raspi and testbench run all seven, dungeon all but `pihole`. The host role `backup_target` (dungeon only) mounts the
  backup SSD and creates the `restic` user.
- `ansible/molecule/default/` – container-based role tests
- `ansible/tests/unit/` – pytest tests (KeePass lookup, `--limit` guard, templates, Claude hook, rulesets,
  repo hygiene)
- `.claude/` – Claude Code settings, the guard hook (`hooks/guard.py`) and skills
- `.github/rulesets/` + `scripts/rulesets.sh` – rulesets for `main`, applied by the user (`rulesets.sh apply`)

## Hosts

| Host | Purpose |
| --- | --- |
| `raspi` (192.168.1.2) | **Production** smart home. Only the user runs anything against it; hand them the commands. |
| `dungeon` (192.168.1.5, ssh port 1359, user `dragon`) | **Production** private cloud and backup target (the SSD at `/media/backup` holds the restic repositories of raspi, dungeon and testbench). Only the user runs anything against it; hand them the commands. |
| `testbench` (192.168.1.7) | Test Pi for real integration runs, done by the user from the home network: propose the commands (`--check --diff`, real run, second run with `changed=0`). |

Always pass `--limit <host>`. Before a real run, do `--check --diff` first.

A `PreToolUse` hook (`.claude/hooks/guard.py`, tests in `ansible/tests/unit/test_guard_hook.py`) blocks the risky
actions for Claude: anything against raspi (dungeon is production too, but the hook only learns it once the user
applies the pending patch from #15), playbooks without `--limit` or with `all`, pushes to `main`, force pushes,
PR merges, storing or forgetting the KeePass master password, printing `KEEPASS_PASSWORD`, `git add -f`, and edits to
the hook and settings themselves. Don't work around a block; give the user the command or the proposed change instead.

## Secrets

Secrets come from KeePass databases through the custom `keepass` lookup plugin (pykeepass). There is one database per
host, named by the host variable `keepass_database` (`raspberry` by default, `dungeon` on dungeon); the root CA always
comes from `keepass_ca_database` (`raspberry`, until #10 moves it). The paths are in `local_keepass_databases` in the
user's git-ignored `ansible/inventory/group_vars/all/local.yaml`; the master passwords come from `KEEPASS_PASSWORD`
(`raspberry`) and `KEEPASS_PASSWORD_<NAME>` (e.g. `KEEPASS_PASSWORD_DUNGEON`), which `ansible/bin/with-keepass` sets
from Windows Credential Manager, or a prompt. The entries each database needs are in `ansible/README.md`. There is no ansible-vault
(raspberry#241 was dropped). Never run `with-keepass --store`/`--forget` or read a stored password yourself. Never commit
secrets, and never print them: every task that handles secret values needs `no_log: true`.

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

- Commit messages: conventional style (`feat:`, `fix:`, `test:`, `chore:`), referencing issues (`Refs #5`).
- Never commit or push to `main`. Branch off `main` and open a PR into `main`; the user merges.
- Container images in `ansible/roles/<component>/templates/docker-compose.yaml.j2` are pinned and updated by Renovate
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
