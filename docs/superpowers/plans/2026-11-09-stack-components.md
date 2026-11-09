# Stack components Implementation Plan (#14)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the `stack` role into one role per component, switched per host with `stack_components`, while raspi and testbed render exactly what they render today.

**Architecture:** Each component role owns its templates (laid out like their destination below `stack_dir`), its
tasks, handlers, defaults and a `docker-compose.yaml.j2` fragment. `stack` imports the enabled component roles
(`import_role` + `when`, so the `stack` tag reaches their tasks), merges the fragments with a filter plugin into one
definition, writes it, runs the clean-slate restore and `compose up` for project `infrastructure`. Golden files
generated from `main` pin the output for raspi and testbed.

**Tech Stack:** Ansible (ansible-core, community.docker), Jinja2, pytest, Molecule (Docker driver, debian:trixie).

**Spec:** `docs/superpowers/specs/2026-11-09-dungeon-components-design.md` (PR 1 part; dungeon itself is #15).

## Global Constraints

- Compose project name stays `infrastructure`; container names, network names/subnets, paths below
  `/etc/docker/infrastructure` and the restic-restore paths stay unchanged.
- raspi/testbed: every rendered config file byte-identical to `main`; the compose definition identical as parsed
  YAML; second run `changed=0`. The on-disk `compose/docker-compose.yaml` may change once in formatting (it is now
  dumped from the merged definition); containers are not recreated because the parsed service config is the same.
- Every component's compose fragment is named `docker-compose.yaml.j2` (Renovate's pattern
  `/(^|/)docker-compose\.yaml\.j2$/`), images stay pinned.
- Role variables are prefixed with their role name (ansible-lint `--strict`, `var-naming`); FQCN; `.yaml`; secrets
  tasks keep `no_log: true`.
- `bootstrap.yaml` keeps a single `stack` entry tagged `stack`; `--tags stack` must still deploy everything.
- Commits: conventional style, `Refs #14`, with the session's attribution lines.

## Decisions taken here (builder's calls)

- Secret files stay in the `secrets` role, gated per component (`'pihole' in stack_components` etc.). Moving them
  into the stack-tagged components would break the README's "Set up a host" order: step 3 runs
  `--skip-tags stack` and step 4 needs `init-restic.sh` from it. #15 revisits this when `init-restic.sh` goes.
- `stack_components` default lives in `roles/stack/defaults/main.yaml` (all seven), not in `group_vars`.
- nginx snippets: all of them are still rendered on every host with `reverse_proxy`; only the `server` blocks in
  `nginx.conf.j2` are conditional (`pihole`, `outbound`). Trimming snippets per host is #15's business.

## Component map

| Role | Compose services / volumes / secrets | Templates and files (dest below `stack_dir`) | Owns |
| --- | --- | --- | --- |
| `reverse_proxy` | `nginx`; network `nginx` | `nginx/nginx.conf.j2`, `nginx/snippets/*.j2` | nginx dirs, reload handler, certificate assert |
| `portainer` | `portainer` | – | `portainer/data` dir, restore item `portainer` |
| `unbound` | `unbound` | – | – |
| `pihole` | `pihole` | – | restore item `pihole` |
| `outbound` | `outbound`; volume `outbound_pihole` | – | – |
| `resticprofile` | `resticprofile`; secret `restic_passphrase` | `resticprofile/config/*.j2`, `resticprofile/ssh_config.j2`; `files/restic-restore` | backup host lookup, `tasks/restore.yaml`, drill cron |
| `healthmonitor` | `healthmonitor` | – | – |
| `stack` | networks `siz`, `outbound` (external) | – | python libs, `compose` dir, merge, write, `compose up` |

## Variable renames

| Old | New |
| --- | --- |
| `stack_dungeon_host`, `stack_dungeon_ip` | `resticprofile_backup_host`, `resticprofile_backup_host_ip` |
| `stack_restore_skip`, `_snapshot`, `_state_dir`, `_source_mounts`, `_staging`, `_rsnapshot`, `_runners`, `_drill`, `_drill_enabled` (`stack_skip_restore` → `resticprofile_restore_skip`) | `resticprofile_restore_*` |
| `stack_backup_stacks` (group_vars) | `resticprofile_backup_stacks` |
| `restore_items` (group_vars) | `portainer_restore_items`, `pihole_restore_items`; merged by `stack` into `stack_restore_items` |
| `stack_nginx_container` | `reverse_proxy_container` |
| `group_vars/all/restore_items.yaml` | `group_vars/all/backup.yaml` (only `resticprofile_backup_stacks`) |

`-e skip_infrastructure_restore` / `-e infrastructure_restore_snapshot` keep their names (README, muscle memory).

## Review Focus

1. **`--tags stack` run**: tasks of imported component roles must carry the `stack` tag; a test asserts every
   component is pulled in with `import_role` (not `include_role`).
2. **A host without `pihole`**: no `pihole` service, no `pihole.<host>` server block, no `.env` written, and the
   compose still validates (`depends_on` only points at enabled services). Unit test in Task 4.
3. **`pihole`/`outbound` without `unbound`**: an assert stops the run with a clear message (Task 4).
4. **Check mode on a host where only the compose file's formatting changes**: the restore dry run is skipped
   (`stack_compose_file is changed`), as today; must not fail. Covered by Molecule's idempotence run plus
   Mika's testbed `--check --diff`.
5. **Duplicate keys across fragments** (two components defining the same service, volume or secret): the merge
   fails loudly instead of silently overwriting (Task 2).

---

### Task 1: Golden files of today's output

**Files:**
- Create: `ansible/tests/unit/render.py` (shared render helper), `ansible/tests/unit/test_stack_golden.py`,
  `ansible/tests/unit/golden/{raspi,testbed}/…`
- Modify: `ansible/tests/unit/test_stack_templates.py` (use `render.py`)

**Interfaces:**
- Produces: `render.rendered_tree(tmp_path, host) -> dict[str, str]` (relative dest path → content), rendered
  through the real inventory (`-i inventory/`, `--limit host`) with `resticprofile`'s dummy
  `backup host ip = 192.0.2.10`, `dungeon_ssh_port = 1359`; includes `docker-compose.yaml`,
  `restic-restore/config.json`, `restic-restore/stacks/infrastructure.json`. Later tasks change only its insides.
- Produces: `GOLDEN_UPDATE=1 pytest …test_stack_golden.py` rewrites the golden files.

- [ ] **Step 1:** Write `test_stack_golden.py`: for `host in ["raspi", "testbed"]`, every golden file exists in the
  render and vice versa; files equal byte for byte, except `docker-compose.yaml`, which is compared with
  `yaml.safe_load` on both sides.
- [ ] **Step 2:** Run it: FAIL (no golden files).
- [ ] **Step 3:** Implement `render.py` for today's layout (move `rendered_tree` out of `test_stack_templates.py`; the
  two JSON files rendered with `copy content` exactly as `roles/stack/tasks/restore.yaml` builds them), then
  generate: `GOLDEN_UPDATE=1 ../.venv/bin/pytest tests/unit/test_stack_golden.py`.
- [ ] **Step 4:** `../.venv/bin/pytest tests/unit` — all green.
- [ ] **Step 5:** Commit `test: golden files of the stack's rendered output` (Refs #14).

### Task 2: `compose_merge` filter

**Files:**
- Create: `ansible/filter_plugins/compose_merge.py`, `ansible/tests/unit/test_compose_merge.py`

**Interfaces:**
- Produces: filter `compose_merge(fragments: list[dict]) -> dict`. Merges the top-level keys `services`, `volumes`,
  `networks`, `secrets` of all fragments in order; any other top-level key, or the same name under one of those
  keys in two fragments, raises `AnsibleFilterError` naming the key and both fragment indexes.

- [ ] **Step 1:** Tests: two fragments merge (`services` from both, order kept); duplicate service →
  error mentioning the service name; duplicate network → error; unknown top-level key (`x-foo`) → error;
  empty list → `{}`.
- [ ] **Step 2:** Run: FAIL (module missing).
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run: PASS; `ansible-lint --strict` clean.
- [ ] **Step 5:** Commit `feat: compose_merge filter for the stack's compose fragments` (Refs #14).

### Task 3: Component roles, `stack` assembles (all components on)

**Files:**
- Create: `ansible/roles/{reverse_proxy,portainer,unbound,pihole,outbound,resticprofile,healthmonitor}/` with
  `tasks/main.yaml`, `defaults/main.yaml`, `templates/docker-compose.yaml.j2`, plus per the component map
  (`handlers/main.yaml` for reverse_proxy; `tasks/restore.yaml`, `files/restic-restore` for resticprofile)
- Modify: `ansible/roles/stack/{tasks/main.yaml,defaults/main.yaml,handlers/main.yaml}`,
  `ansible/inventory/group_vars/all/restore_items.yaml` → `backup.yaml`, `ansible/inventory/host_vars/testbed.yaml`,
  `ansible/molecule/default/converge.yaml`, `ansible/tests/unit/render.py` and every test that points into
  `roles/stack/` (`test_restore_items.py`, `test_dns_fallback.py`, `test_restic_restore.py`,
  `test_pihole_listening.py`, `test_renovate.py`, `test_stack_templates.py`, `test_inventory.py`)
- Delete: `ansible/roles/stack/templates/`, `ansible/roles/stack/files/`, `ansible/roles/stack/tasks/restore.yaml`

**Interfaces:**
- Consumes: `compose_merge` (Task 2); `render.rendered_tree` (Task 1).
- Produces: `stack_components: list[str]` (default all seven, order as in the component map);
  `stack_restore_items: dict` (merged `<component>_restore_items` of enabled components); `stack_compose_definition`
  (fact, the merged dict); handler `Reload reverse proxy nginx`. `stack` order: python libs → `import_role` each
  component (`tasks_from: main`, `when: '<c>' in stack_components`) → compose dir → render + merge + write compose →
  `import_role resticprofile tasks_from: restore` → networks → compose up.

- [ ] **Step 1:** Tests first: `test_compose_images_are_pinned` iterates over
  `roles/*/templates/docker-compose.yaml.j2`; `test_renovate` asserts every such fragment matches the pattern; a new
  `test_stack_imports_components` asserts `stack/tasks/main.yaml` uses `ansible.builtin.import_role` for every
  component and no `include_role`; repoint path constants in the other tests to the new roles; `test_inventory`
  expects `resticprofile_restore_drill_enabled: false` for testbed.
- [ ] **Step 2:** Run `pytest tests/unit`: the repointed tests FAIL with missing files (golden test still green).
- [ ] **Step 3:** Move templates/files with `git mv` (history), split `docker-compose.yaml.j2` into the seven fragments
  plus the `networks` block in `stack`, apply the variable renames, move tasks/handlers. `render.py` now renders each
  enabled component's templates (all `*.j2` except `docker-compose.yaml.j2`, dest = path relative to `templates/`)
  and the merged compose via the filter.
- [ ] **Step 4:** `pytest tests/unit` green, golden unchanged (`git status` shows no change under `golden/`);
  `ansible-lint --strict` 0 findings.
- [ ] **Step 5:** Molecule: `(cd ansible && ../.venv/bin/molecule test)` green, idempotence `changed=0`.
  `verify.yaml` keeps its checks; only variable names in `converge.yaml` change.
- [ ] **Step 6:** Commit `refactor: split the stack role into component roles` (Refs #14).

### Task 4: Per-host switching

**Files:**
- Modify: `ansible/roles/stack/tasks/main.yaml` (assert), `ansible/roles/reverse_proxy/templates/nginx/nginx.conf.j2`
  (`{% if 'pihole' in stack_components %}` / `outbound` around their server blocks),
  `ansible/roles/secrets/tasks/main.yaml` (`.env` only with `pihole`, `.outbound.env` with `outbound`,
  `.healthmonitor.env` with `healthmonitor`, restic passphrase/key/`init-restic.sh` with `resticprofile`),
  `ansible/roles/pihole/templates/docker-compose.yaml.j2`, `…/outbound/…` (no change expected: they depend on `unbound`,
  which the assert guarantees)
- Test: `ansible/tests/unit/test_stack_components.py`, `ansible/molecule/default/verify.yaml` (unchanged set)

**Interfaces:**
- Consumes: `render.rendered_tree(tmp_path, host, extra_vars=None)` — add the `extra_vars` parameter (passed as
  `-e @file`), used to override `stack_components`.

- [ ] **Step 1:** Tests in `test_stack_components.py`, rendering raspi with
  `stack_components = [reverse_proxy, portainer, unbound, outbound, resticprofile, healthmonitor]`: compose services
  are exactly `nginx, portainer, unbound, outbound, resticprofile, healthmonitor`; `nginx.conf` has no
  `server_name pihole.` but `server_name outbound.`; `stacks/infrastructure.json` items == `["portainer"]`; the
  merged compose passes `docker compose config --quiet` when Docker is available (skip otherwise). A second test:
  `[pihole]` without `unbound` makes the stack's assert fail with "pihole and outbound need unbound" (run the assert
  task file on its own). A third: the `secrets` tasks writing `.env`/`.outbound.env`/`.healthmonitor.env`/restic
  files each carry a `when` on their component (YAML inspection).
- [ ] **Step 2:** Run: FAIL (pihole block still rendered, no assert).
- [ ] **Step 3:** Implement the conditions and the assert.
- [ ] **Step 4:** `pytest tests/unit` green incl. golden; `ansible-lint --strict`; Molecule green.
- [ ] **Step 5:** Commit `feat: switch stack components per host` (Refs #14).

### Task 5: Docs and PR

**Files:**
- Modify: `ansible/README.md` (role list, variable names in the restore sections, `stack_components`),
  `CLAUDE.md` (layout: component roles), `.claude/skills/working-with-ansible/SKILL.md` (references to the stack
  role's files/vars), `docs/superpowers/specs/…` only if a decision above contradicts it.

- [ ] **Step 1:** `grep -rn "stack_dungeon\|stack_restore\|stack_backup_stacks\|restore_items\|roles/stack/templates" .`
  returns only intended hits (golden files, history notes).
- [ ] **Step 2:** Full check: `pytest -n auto ansible/tests/unit`, `ansible-lint --strict ansible/`,
  `pre-commit run --all-files`, Molecule.
- [ ] **Step 3:** Commit `docs: stack components` (Refs #14), push, open the PR "Split the stack role into
  components" (Closes #14, Refs #5) with the attribution block, assign and request review from Mika, subscribe to
  PR activity. PR body lists the commands for Mika on testbed: `bootstrap.yaml --limit testbed --tags stack --check
  --diff` (expected: only `compose/docker-compose.yaml` formatting), real run, second run `changed=0`.
