# raspberry – smart home infrastructure on Raspberry Pi

Docker-based smart home stack (nginx reverse proxy, Pi-hole ×2, unbound, portainer, resticprofile backups,
container health monitoring) for a Raspberry Pi running Debian/Raspberry Pi OS trixie (arm64).

## State of the repo

The deployment is being migrated from bash scripts to Ansible, and the migration is only half done:

- **Ansible** (`bootstrap/`) handles host setup: ssh port, base packages, sysctl, sudoers, rsnapshot, Docker, secrets
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
- `bootstrap/` – Ansible: playbooks (`bootstrap.yaml`, `check-certificates.yaml`, `setup-pihole.yaml`),
  `roles/`, `tasks/` (Pi-hole API helpers), `configs/pihole.yaml`, `inventory/hosts.yaml`,
  `lookup_plugins/keepass.py`
- `bootstrap/molecule/default/` – container-based role tests
- `bootstrap/tests/unit/` – pytest tests for the KeePass lookup plugin

## Hosts

| Host | Purpose |
|---|---|
| `raspi` (192.168.1.2) | **Production** smart home. Never run playbooks against it unless the user explicitly asks. |
| `testbed` (192.168.1.7) | Test Pi. Use it for real integration runs. |

Always pass `--limit <host>`. Before a real run, do `--check --diff` first.

## Secrets

Secrets come from a KeePass database through the custom `keepass` lookup plugin (pykeepass), with the master
password prompted at runtime. There is no ansible-vault. Never commit secrets, and never print them:
every task that handles secret values needs `no_log: true`.

## Commands

Tooling lives in a venv at the repo root:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest bootstrap/tests/unit                  # unit tests, no Docker
(cd bootstrap && ../.venv/bin/molecule test)           # role tests in a trixie container, needs Docker, ~3–5 min
(cd bootstrap && ../.venv/bin/molecule converge)       # faster loop while developing; then verify / destroy
.venv/bin/ansible-lint bootstrap/                      # lint (currently many legacy violations, see #240)
```

Reports land in `test-results/` (JUnit XML). CI (`.github/workflows/molecule.yaml`) runs pytest and Molecule on
every push touching `bootstrap/`.

When writing or changing Ansible roles, follow the `ansible-role` skill in `.claude/skills/`.

## Conventions

- Commit messages: conventional style (`feat:`, `fix:`, `test:`, `chore:`), referencing issues (`Refs #238`).
- Work on feature branches; `main` is the default branch.
- Container images in `docker-compose.yaml` are pinned and updated by Renovate; keep them pinned.
