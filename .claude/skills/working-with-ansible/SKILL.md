---
name: working-with-ansible
description: Use when a task in this repo involves running, dry-running, testing, verifying or rolling out Ansible playbooks (bootstrap.yaml, check-certificates.yaml, setup-pihole.yaml) or the Docker stack (stack role, deploy.sh fallback), on testbed or raspi, or when deciding what can be checked from a cloud container and what the user has to run on their own machine. Writing role code itself is covered by ansible-role.
---

# Working with Ansible in this repo

## Overview

Ansible here changes a real smart home (DNS, SSH, Docker) on two Pis that only machines in the home
network can reach. Know before you start what you can verify yourself, what only CI can, and what only the user
can run on their developer machine.
How to *write* roles, tasks and Molecule checks is in **ansible-role**; read it too when you touch `ansible/`.

## Who can run what

First find out where you run: a cloud container (claude.ai/code) or a local session in the user's WSL
(working directory under `/home/`, `ansible/bin/with-keepass` usable, `powershell.exe` on the PATH).

| Check | Cloud container | Local session (WSL) | CI |
|---|---|---|---|
| pytest (`ansible/tests/unit`, incl. `--limit` guard) | yes | yes | yes |
| `ansible-playbook --syntax-check`, `ansible-lint` | yes | yes | yes (lint `--strict`, #240) |
| Molecule converge/idempotence/verify | **no**: test containers have no network, Galaxy is blocked | yes, if Docker runs | yes, on every PR and on pushes to `main` touching `ansible/**` |
| Anything against testbed | **no**: the 192.168.1.x LAN is only reachable from the home network, SSH key and KeePass DB are on the user's machine | **only after asking**, every time, `--check --diff` first | no |
| Anything against raspi | no | **no**: hand the user the commands | no |

In a local session the LAN, the SSH key and `with-keepass` are all reachable, so the only things between you and
production are the rule above and `.claude/hooks/guard.py`.
In a cloud container, don't probe the LAN or set up dockerd to find this out again. Never write "Molecule passed" from a local run;
point to the CI run on your commit. The KeePass master password is typed by the user into the prompt, never
into chat, a command line you write, or `KEEPASS_PASSWORD` in a command you hand over.

## Order of work

1. **Plan first.** Before changing code, give the user a short plan summary (what, which files, how tested) and
   check the related open issue (`gh issue list` or the GitHub tools for the current state). Answer in the user's language.
2. **Branch off `main`, PR into `main`**, never push to `main`. Commit style `fix:`/`feat:` + `Refs #<n>`.
3. **Test first** per CLAUDE.md, then syntax-check and lint locally, then let CI run Molecule and drive it green.
4. **Hand the user the rollout** as exact commands, in this order, and say what to look for in each:

```sh
cd ansible                                               # ansible.cfg + inventory live here
ansible-playbook <play>.yaml --limit testbed --check --diff
ansible-playbook <play>.yaml --limit testbed             # real run
ansible-playbook <play>.yaml --limit testbed             # must end with changed=0
# raspi only after the user says so, same three steps with --limit raspi
# stack only: ansible-playbook bootstrap.yaml --limit <host> --tags stack (same three steps)
```

Every playbook is `hosts: all` with an assert that fails without `--limit`. Keep that guard on new playbooks.
The `PreToolUse` hook in `.claude/hooks/guard.py` blocks you from raspi, `--limit all` and `main`; when it blocks
something, hand the user the command instead of looking for another way to run it.

## Things that look wrong but aren't (and traps)

- **`setup-pihole.yaml` has no dry run**: `uri` doesn't support check mode, so `--check` aborts. The testbed
  run is the dry run; read its "to delete / to add" debug lines before running on raspi.
- **The Pi-hole sync deletes** every record or list not in `configs/pihole.yaml`. Ask whether raspi has
  hand-made entries before a first sync.
- **A new subdomain needs three places**: the CNAME in `configs/pihole.yaml`, nginx `server_name` in
  `ansible/roles/stack/templates/nginx/nginx.conf.j2` (and `setup/nginx/nginx.conf` while deploy.sh exists, the
  template test keeps them in sync), and the SAN in `certificate_check_subdomains`. DNS alone lands on nginx's default
  server with a certificate error.
- **`--check` isn't fully side-effect free**: tasks with `check_mode: false` (e.g. `python3-debian` in the docker
  role) really run.
- **`Update apt` reports changed** whenever updates are pending; that is not drift.
- **`no_log` hides diffs**: for `.env` files you only learn *that* they change. That is intended.
- **Docker on raspi is Debian `docker.io` 26.1.5** until the docker role is rolled out there; don't drop
  compatibility settings that depend on it (e.g. `DOCKER_API_VERSION`).
- The stack is deployed by the `stack` role (#238); `deploy.sh` is only a fallback until it is deleted. New
  deployment logic goes into Ansible, never into the scripts.
- **The compose project must stay `infrastructure`** (`stack_project_name`). deploy.sh's containers belong to it;
  another name would start a second stack whose fixed container names collide.
- **Stack config changes don't restart containers** except nginx (reload after `nginx -t`). resticprofile reads
  its config per run, but a changed schedule needs `docker restart resticprofile`.

## Reporting back

End with: what changed (files, PR link), what you ran and its result, what CI showed on the head commit,
and the rollout commands the user still has to run, testbed before raspi.
