---
name: ansible-role
description: Conventions and checklist for writing, changing or reviewing Ansible roles, tasks and playbooks in this repo's bootstrap/ directory, including how to cover them with Molecule tests. Use this whenever you add or modify anything under bootstrap/ (roles, tasks, handlers, templates, playbooks, inventory), migrate a bash step from deploy.sh/setup.sh/run.sh into Ansible, fix an idempotency or ansible-lint problem, or add or extend a Molecule scenario or verify.yml, even if the user only says something like "add X to the pi setup" or "automate this step".
---

# Ansible roles in this repo

The goal of the Ansible code here is **idempotent, testable deployments of a Raspberry Pi smart home**. A run
against an already-configured Pi must report `changed=0`, and every role should be provable in a container
before it touches the testbed Pi, and the testbed before production (`raspi`). The conventions below all serve
that goal; when in doubt, ask "does this keep a second run at changed=0, and could Molecule verify it?"

## Where things go

```
bootstrap/
  <playbook>.yaml            # plays only: hosts, vars, roles. No long task lists.
  inventory/hosts.yaml       # hosts; per-host values belong in host_vars/ (see #239)
  roles/<role>/
    tasks/main.yaml
    handlers/main.yaml       # restarts/reloads, triggered via notify
    defaults/main.yaml       # every variable the role reads, with a sane default, prefixed <role>_
    files/...                # static files, referenced as src: etc/foo.conf
    templates/...            # Jinja templates (*.j2), referenced as src: etc/foo.conf.j2
  molecule/default/          # converge.yml, verify.yml, molecule.yml, Dockerfile.j2
  tests/unit/                # pytest for Python plugins
```

Reference role files by paths **relative to the role's own `files/` or `templates/` directory**
(`src: etc/sysctl.d/90-overcommit-memory.conf`). Paths like `templates/etc/...` that resolve relative to the
playbook directory only work from playbooks located directly in `bootstrap/` and break as soon as Molecule or
a new `site.yaml` calls the role (this happened once already).

## Task conventions

- **FQCN for every module**: `ansible.builtin.copy`, `ansible.builtin.set_fact`, `community.docker.docker_container`.
  ansible-lint in CI runs with `--strict`, and short names are ambiguous with collections installed.
- **Task names**: capitalized, describe the desired state ("Ensure Docker apt repository is present"), with any
  Jinja at the end of the name only.
- **Modules over shell.** Use `command`/`shell` only when no module exists; then make it idempotent with
  `creates:`/`removes:` or a `changed_when:` based on output. A `shell` task without either always reports
  "changed" and breaks the idempotence test.
- **No `force: true`**, no `ignore_errors: true` to paper over failures. If a failure is expected, use
  `failed_when:` with the precise condition. If a resource must be regenerated, decide that with a condition
  (e.g. `x509_certificate_info` + `valid_at`), not by forcing it on every run.
- **Restarts and reloads go into handlers** (`notify:`), never as unconditional tasks. Use `meta: flush_handlers`
  only when a later task depends on the restart (like the sshd port switch).
- **Apply what you write**: e.g. `ansible.posix.sysctl` instead of only copying a file into `/etc/sysctl.d/`.
- **Explicit file ownership and mode** on everything you create (`owner`, `group`, `mode` as a quoted string like
  `'0644'`).
- **Facts over hand-maintained inventory vars** where facts exist (architecture, distribution, release). The Pi
  is `aarch64` → Docker's `arm64`; CI and Molecule run on `x86_64` → `amd64`.
- **Don't run `apt upgrade` in a regular role.** It changes on every run whenever updates exist. Upgrades belong
  in a separate playbook or unattended-upgrades.

## Config files that can lock you out

Some files break access to the Pi if they're wrong. Always validate them before they're put in place:

```yaml
- name: Ensure sudoers entry for deploy user
  ansible.builtin.template:
    src: etc/sudoers.d/user.j2
    dest: "/etc/sudoers.d/{{ ansible_user }}"
    owner: root
    group: root
    mode: '0440'
    validate: 'visudo -cf %s'

- name: Ensure sshd listens on the configured port
  ansible.builtin.copy:
    content: "Port {{ configured_port }}\n"
    dest: /etc/ssh/sshd_config.d/custom-port.conf
    owner: root
    group: root
    mode: '0644'
    validate: 'sshd -t -f %s'
  notify: Restart sshd
```

The same applies to nginx config (`nginx -t` inside the container, via handler) and anything else whose
breakage takes the host or the reverse proxy down.

## Secrets

Secrets come from KeePass via the repo's own `keepass` lookup plugin (`bootstrap/lookup_plugins/keepass.py`),
never from files in the repo and not from ansible-vault.

- Every task that **receives, renders or sends** a secret gets `no_log: true`. That includes `copy`/`template`
  writing `.env` files, `uri` calls with passwords or session tokens in body/headers, and `set_fact` holding a
  secret. `no_log` also hides the `--diff` output, so no extra `diff: false` is needed. A `set_fact` holding a
  secret prints it at `-v`, so it needs `no_log` too.
- Molecule's `verify.yml` fails if any dummy secret (values containing `LEAKCANARY`) appears in the Ansible log of
  the test run, which runs at `-v` with `--diff`. When you add a role variable holding a secret, give its dummy
  value in `converge.yml` the `LEAKCANARY-` prefix so the leak test covers it.
- Secret files on the host: `owner: root`, `mode: '0600'`, below `/etc/docker/<stack>/`.
- Resolve each secret **once** (e.g. a single `set_fact` with `no_log: true` at the start of the play) instead of
  referencing a lazy `lookup()` var many times; each reference re-decrypts the KDBX (#235).
- Private keys of the root CA never go to the Pi. Sign on the control node (`delegate_to: localhost`) and copy
  only the leaf cert and key (#229).
- In Molecule, replace lookups with plain dummy values in `converge.yml` vars (e.g. `pihole_password: LEAKCANARY-pihole`),
  so tests never need the real KeePass DB.

## Testing every change

Each role change comes with a matching test change. That's what keeps the roles trustworthy as the migration
from bash continues.

1. **Add the role to Molecule** in `bootstrap/molecule/default/converge.yml` (`roles:` list), plus dummy values
   for any variables it needs that aren't in `defaults/`. If the role needs something a fresh Pi OS Lite has but
   `debian:trixie` lacks, install it in `molecule/default/Dockerfile.j2` with a comment (that's what `procps`
   for `/etc/sysctl.d` is). Never change the role just to suit the container.
2. **Add checks to `verify.yml`** that prove the *outcome*, not that the task ran: package installed (via
   `package_facts`), file exists with owner and mode, rendered content contains the expected line, config passes
   its validator (`visudo -cf`, `nginx -t`), user in group, cron entry present. Use `ansible.builtin.assert` with a
   clear `fail_msg`. Never `register:` into reserved names like `groups`.
3. **Run it**, from `bootstrap/`:
   ```sh
   ../.venv/bin/molecule converge    # repeat while developing
   ../.venv/bin/molecule verify
   ../.venv/bin/molecule test        # full sequence incl. idempotence before you call it done
   ```
   The idempotence step must pass. If it doesn't, fix the role; don't remove the step.

   If Molecule aborts with "Ansible requires blocking IO", run it with `</dev/null`. If another Molecule run
   of this repo may be active (a second checkout, a parallel agent), give yours its own state directory with
   `MOLECULE_EPHEMERAL_DIRECTORY=$(mktemp -d)`, because the default path is derived from the folder name and
   concurrent runs overwrite each other's inventory.
4. **Lint** the files you touched: `../.venv/bin/ansible-lint <paths>`. Don't add new violations, even though
   older code still has many (#240).
5. **Things a container can't test** (sshd port switching, real Docker daemon behavior, Pi hardware, Pi-hole API
   against a live instance) go to the testbed Pi: `ansible-playbook <playbook> --limit testbed --check --diff`,
   then a real run, then a second run that must report `changed=0`. Ask the user before running against
   any real host, and never target `raspi` without explicit instruction.

If Docker isn't available, say so and still do the static parts (syntax check with
`ANSIBLE_ROLES_PATH=roles ansible-playbook -i localhost, --syntax-check molecule/default/converge.yml`, lint),
instead of claiming the tests passed.

## Migrating a step from the bash scripts

When moving something from `deploy.sh`/`setup/setup.sh`/`run.sh` into Ansible:

- Replace `%HOSTNAME%` sed substitution with Jinja templates using `{{ inventory_hostname }}`.
- Replace `docker network create` with `community.docker.docker_network`, and `docker compose pull/up` with
  `community.docker.docker_compose_v2` (`pull: policy`).
- Replace "create if missing" shell checks (`if [ ! -f ... ]`) with the module that manages the resource
  (`community.crypto.openssl_dhparam`, `openssl_privatekey`, `file: state=directory`).
- Note in the commit or PR which bash lines are now obsolete; the scripts get deleted once everything has moved (#238).

## Before you finish

Mention in your answer:
- which roles/files changed and why,
- which Molecule checks were added,
- what you actually ran and the result (Molecule idempotence and verify, lint), or exactly what you couldn't run,
- anything that still needs a testbed run.
