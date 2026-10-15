# raspberry – smart home infrastructure on Raspberry Pi

Docker-based smart home stack (nginx reverse proxy, Pi-hole ×2, unbound, portainer, resticprofile backups,
container health monitoring) for a Raspberry Pi running Debian/Raspberry Pi OS trixie (arm64).

## State of the repo

The deployment is being migrated from bash scripts to Ansible, and the migration is only half done:

- **Ansible** (`ansible/`) handles host setup: ssh port, base packages, sysctl, sudoers, rsnapshot, Docker, secrets
  (`.env` files from KeePass), certificates, and Pi-hole DNS records/lists through its REST API.
- **Bash** (`deploy.sh` → `setup/setup.sh` → `run.sh`) still deploys the stack itself: nginx/restic config with
  `%HOSTNAME%` sed templating, docker networks, `docker compose up`. It depends on files that only the Ansible
  `secrets` role creates.
- The plan is to move the stack deployment into an Ansible role and then delete the bash scripts (#238).
  Don't extend the bash scripts; put new deployment logic into Ansible.

Open review findings are tracked as GitHub issues #229–#240 (security, bugs, idempotency, migration, refactor, CI).
Check them before changing an affected area, and reference them in commits.

## Layout

- `docker-compose.yaml`, `setup/` – the stack and its config (nginx snippets, resticprofile)
- `ansible/` – Ansible: playbooks (`bootstrap.yaml`, `check-certificates.yaml`, `setup-pihole.yaml`),
  `roles/`, `tasks/` (Pi-hole API helpers), `configs/pihole.yaml`, `inventory/hosts.yaml`,
  `lookup_plugins/keepass.py`
- `ansible/molecule/default/` – container-based role tests
- `ansible/tests/unit/` – pytest tests for the KeePass lookup plugin

## Hosts

| Host | Purpose |
|---|---|
| `raspi` (192.168.1.2) | **Production** smart home. Never run playbooks against it unless the user explicitly asks. |
| `testbed` (192.168.1.7) | Test Pi. Use it for real integration runs. |

Always pass `--limit <host>`. Before a real run, do `--check --diff` first.

## Secrets

Secrets come from a KeePass database through the custom `keepass` lookup plugin (pykeepass). Its paths are in the
user's git-ignored `ansible/inventory/group_vars/all/local.yaml`; the master password comes from `KEEPASS_PASSWORD`
(`ansible/bin/with-keepass` sets it from Windows Credential Manager) or a prompt. There is no ansible-vault (#241
was dropped). Never run `with-keepass --store` or read the stored password yourself. Never commit secrets, and
never print them: every task that handles secret values needs `no_log: true`.

## Commands

Tooling lives in a venv at the repo root:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest ansible/tests/unit                    # unit tests, no Docker
(cd ansible && ../.venv/bin/molecule test)             # role tests in a trixie container, needs Docker, ~3–5 min
(cd ansible && ../.venv/bin/molecule converge)         # faster loop while developing; then verify / destroy
.venv/bin/ansible-lint --strict ansible/               # lint, must pass with 0 findings (CI blocks)
```

Reports land in `test-results/` (JUnit XML). CI (`.github/workflows/molecule.yaml`) runs pytest and Molecule on
every PR and every push to `main` that touches `ansible/`.

When writing or changing Ansible roles, follow the `ansible-role` skill in `.claude/skills/`.

## Conventions

- Commit messages: conventional style (`feat:`, `fix:`, `test:`, `chore:`), referencing issues (`Refs #238`).
- Work on feature branches; `main` is the default branch.
- Container images in `docker-compose.yaml` are pinned and updated by Renovate; keep them pinned.
- Work test-driven, unless something speaks against it (then say why). Write the test first and watch it fail
  for the right reason before changing the code: pytest for Python, `verify.yaml` checks for Ansible roles,
  run against the old role. Since a Molecule run takes minutes, one red/green cycle may cover a group of
  related checks.
- YAML files use the `.yaml` extension, not `.yml`. Exceptions only where a tool requires a fixed name
  (e.g. Molecule's `molecule.yml`).
- Everything in the repo is written in English (code, comments, docs, commit messages), even when the user
  chats in German.

## Working with the user

- Whenever a task would benefit from a skill you don't have, say so: which skill, what it would be for, and
  whether a ready-made one exists or it would have to be written.
