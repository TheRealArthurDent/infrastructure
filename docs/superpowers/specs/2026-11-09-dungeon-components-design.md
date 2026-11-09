# dungeon joins infrastructure: stack components per host

Refs #5. Decided by Mika on 2026-11-08/09 in the project thread "dungeon ins Infra-Repo aufnehmen".

## Why

infrastructure becomes the infra repo for both Pis: `raspi` (Pi 4, production smart home) and `dungeon` (Pi 5,
private cloud, backup target). dungeon is deployed today by hand from the fork
`TheRealArthurDent/private-cloud-infrastructure` (bash `deploy.sh`, last change 2025-11-22), which drifts from this
repo. One Ansible code base for both hosts ends the drift; the fork can be archived afterwards. Apps for dungeon
(Nextcloud, ...) go into a separate repo, like smart-home for raspi.

## Goals

- dungeon is in the inventory and set up by the same `bootstrap.yaml` (one playbook, `--limit dungeon`).
- The `stack` role is split into components, switched on or off per host in `host_vars`.
- dungeon gets: reverse proxy (nginx), portainer, resticprofile, healthmonitor. Not: Pi-hole, outbound, unbound.
- dungeon backs up to itself, onto its external SSD at `/media/backup`, repository `/media/backup/restic/dungeon`.
- The backup never writes to the system disk when the SSD is missing.

## Non-goals

- No rest-server. raspi keeps backing up to dungeon over sftp (`sftp:restic@dungeon:/media/backup/restic/raspi`),
  with its own retention. An off-site copy (e.g. Hetzner Storage Box, sftp only) comes later and is not part of this.
- No dungeon apps in this repo. Nextcloud was never set up on dungeon; OpenCloud comes later from its own repo, with
  its data in a named volume (covered by the backup of `/var/lib/docker/volumes`). Its nginx vhost is added here
  then, as raspi's nginx carries smart-home's vhosts.
- No `site.yaml` / `playbooks/` restructuring and no `pihole_config` role from #5; those stay open there.

## Anti-goals (a failure even if it technically works)

- raspi changes in any way: different rendered files, a different compose definition, recreated containers, or a
  second run with `changed != 0`.
- The user has to run several playbooks or tag combinations to deploy a host.
- A missing SSD makes a backup or a restore silently use (or report on) the empty mount point directory.

## Constraints

- Compose project stays `infrastructure`, container names stay as they are (raspi's running containers, and
  dungeon's existing project from the fork uses the same name, paths under `/etc/docker/infrastructure`, container
  names `nginx`, `portainer`, `healthmonitor` and networks `siz`/`outbound`, so Ansible adopts it).
- Renovate must keep finding every pinned image: its docker-compose manager matches `docker-compose.yaml.j2`.
- Repo conventions: TDD, `.yaml`, FQCN, `no_log` for secrets, Molecule `verify.yaml` check per change,
  `ansible-lint --strict` clean, idempotent.

## dungeon facts

| | |
| --- | --- |
| IP | 192.168.1.5 |
| ssh | port 1359, user `dragon` (key login works for `dragon` and `restic`) |
| OS | Debian/Raspberry Pi OS trixie, arm64 |
| Today | Compose project `infrastructure` from the fork: nginx, portainer, healthmonitor (borgmatic/borg-server are gone), self-signed certificate, vhosts `portainer.dungeon`, `nextcloud.dungeon` |

## Approaches

- **A: one `stack` role with `if`/`when` per component.** Smallest diff and easy to keep raspi identical, but no
  real split; every component stays tangled in one compose template and one task file.
- **B: one role per component, `stack` assembles the project (picked by Mika).** Each component role owns its
  config files, its secrets file, its restore items and a compose fragment; `stack` creates the shared networks,
  merges the fragments of the enabled components into one definition and runs `compose up`. Matches the split Mika
  described and #5's direction.
- **C: one Compose project per component.** Dropped: renames the project and recreates every container on raspi.

## Design (B)

### Components and selection

- Component roles: `reverse_proxy` (nginx), `portainer`, `pihole` (pihole + unbound + outbound, they only make sense
  together), `resticprofile` (incl. the restic-restore helper and restore drill), `healthmonitor`.
- `stack_components` lists the enabled ones. Default in `group_vars/all`: all five (raspi, testbed unchanged).
  `host_vars/dungeon.yaml`: `[reverse_proxy, portainer, resticprofile, healthmonitor]`.
- `bootstrap.yaml` keeps one `stack` entry tagged `stack`; the `stack` role includes the enabled component roles
  itself, so the playbook and `--tags stack` work as before.
- Each component role has `templates/docker-compose.yaml.j2` with its services (plus its volumes/secrets). `stack`
  renders the enabled ones, merges them (services, volumes, networks, secrets), adds the networks and writes one
  `compose/docker-compose.yaml` and passes the same definition to `docker_compose_v2`, as today.
- Restore items move from `group_vars/all/restore_items.yaml` into the owning component (portainer, pihole); the
  helper's stack file lists the items of the enabled components. `stack_backup_stacks` becomes per host:
  raspi/testbed `[infrastructure, smart-home]`, dungeon `[infrastructure]`.

### Proving raspi is unchanged

First commit, before any refactoring: golden files of everything raspi and testbed render today (compose file,
nginx, resticprofile, restic-restore config/stack file, `.env` contents with dummy secrets), produced from `main`.
A pytest renders the new roles for raspi/testbed and compares: config files byte for byte, the compose definition
as parsed YAML. The golden files only change in a commit that says why.

### reverse_proxy

- `nginx.conf.j2` keeps today's server blocks, each wrapped in a condition on `reverse_proxy_vhosts` (default: all
  of today's, raspi byte-identical). dungeon: `[portainer]` plus the host vhost and the health endpoint.
- The `pihole`/`outbound` vhosts only render with the `pihole` component.
- `certificate_check_subdomains` for dungeon in `host_vars`: `portainer`.

### pihole

- Owns the `.env`/`.outbound.env` files (moved out of `secrets`), the `outbound_pihole` volume and its restore item.
- `healthmonitor` uses the outbound Pi-hole as DNS only when this component is enabled; otherwise the daemon DNS.

### resticprofile

- Repository per host: `resticprofile_repository`, default today's sftp URL; dungeon:
  `local:/backup/restic/dungeon` with `/media/backup` mounted into the container as `/backup` (bind propagation
  `rslave`, so a later mount reaches a running container). The ssh key, `ssh_config`, `init-restic.sh`, the
  `dungeon` lookup and `extra_hosts` only exist with an sftp repository.
- SSD: mounted by `backup_target` (below). A marker `/media/backup/.backup-ssd` is created only when `mountpoint -q /media/backup`
  holds on the host. A `mountpoint` check inside the container doesn't work (a bind mount is always a mount point),
  so resticprofile's `run-before` fails the backup when the marker is missing; healthmonitor then reports it.
- The clean-slate restore asserts the marker before it runs, so a missing SSD isn't read as "no snapshot, new host".
- Retention and restore drill as on raspi, on dungeon for dungeon's own repository.
- raspi's sftp backups land on the same SSD (`/media/backup/restic/raspi`), through the `restic` user of the
  `backup_target` component. With the SSD missing, the bare mount point directory is root-owned and empty, so the
  `restic` user can't create a repository there and raspi's backup fails instead of writing to the system disk
  (builder's call; Mika checks once that nothing lies below the unmounted mount point).

### healthmonitor

- Owns `.healthmonitor.env`. On dungeon it reaches the internet through the `outbound` network, which there is a
  plain bridge without the Pi-hole egress filter.

### Host roles on dungeon

- `ssh`: as is (port 1359, key-only, no root login).
- `base`: `base_dns_servers: [192.168.1.2]` (raspi's Pi-hole) plus the Fritzbox fallback; rsnapshot switched off
  (`base_rsnapshot_enabled: false`, decided by Mika); DH params and the memory cgroup as on raspi.
- `docker`: `docker_daemon_dns: [192.168.1.2]`.
- Secrets per host (Mika, 2026-11-09): dungeon gets its own restic passphrase; dungeon and testbed get their own
  Discord webhook, so a message in Discord shows which host sent it. The KeePass titles are host variables
  (default: today's entries, so raspi is unchanged); Mika creates the new entries.
- `secrets`: smart-home `.env` only where `secrets_smart_home_env` is true (default true, false on dungeon); the
  playbook skips the KeePass lookups of disabled parts.

### Networks

`siz` (internal, 172.30.2.0/24) and `outbound` (172.30.1.128/25, iprange .192/26) are created by the fork's
`run.sh` with exactly raspi's settings, so the shared network tasks in `stack` don't recreate them on dungeon.

### KeePass per host

dungeon has its own KeePass database with its own master password and key file (Mika, 2026-11-09). The root CA
lives only in raspi's database.

- `local.yaml` lists the databases by name (`local_keepass_databases: {raspberry: {db, key}, dungeon: {db, key}}`).
  A host's secrets come from its `keepass_database` (default `raspberry`, dungeon `dungeon`); the root CA comes from
  `keepass_ca_database` (`raspberry`), so `check-certificates.yaml --limit dungeon` opens raspi's database only.
  The old `local_keepass_db`/`local_keepass_key` keep working as `raspberry`, so an existing `local.yaml` doesn't
  break. `KEEPASS_DB`/`KEEPASS_KEY` still override `raspberry`.
- Master passwords per database: `KEEPASS_PASSWORD_<NAME>` (e.g. `KEEPASS_PASSWORD_DUNGEON`), `KEEPASS_PASSWORD`
  stays the one for `raspberry`; a missing one is prompted for, naming the database. `bin/with-keepass` exports
  every stored one: Windows credential `<name>-keepass` (`raspberry-keepass` as today). `--store`/`--forget` take
  the database name (`bin/with-keepass --store dungeon`), default `raspberry`. No `--limit` parsing needed.
- dungeon's database uses the same group/title names as raspi's. It needs: `Backup/Resticprofile` (field
  `passphrase`) and `Discord/Docker Webhook` (fields `id`, `token`). testbed's own webhook is a new entry in raspi's
  database (`Discord/Docker Webhook testbed`), selected by a host variable.
- Moving the root CA into a database of its own later only changes `keepass_ca_database`.

### Backup target (dungeon)

A host-level component `backup_target`, on for dungeon: everything a fresh dungeon needs to receive backups.

- The SSD mount (UUID, `nofail`) and the marker `.backup-ssd`, moved here from resticprofile (dungeon's own
  resticprofile only checks the marker).
- The `restic` user (Raspberry Pi Imager only creates `dragon`): fixed UID/GID matching today's owner of
  `/media/backup/restic` (the files survive a reinstall on the SSD), no password, shell for sftp, home on the
  root disk. `authorized_keys` from `backup_target_clients` in `host_vars/dungeon.yaml` (client name -> public
  key, e.g. raspi's `resticprofile` key), each with `restrict`. Public keys only, so they can live in the repo.
- `/media/backup/restic` owned by `restic`, 0700, created only below the marker check.
- `init-restic.sh` on raspi (ssh-copy-id, needs a password login that the ssh role turns off) is replaced: the
  raspi run prints the public key to put into `backup_target_clients` when dungeon doesn't accept it yet.

### Takeover of dungeon (PR 2, by Mika)

Run `check-certificates.yaml --limit dungeon` first (CA-signed certificate replaces the self-signed one), then
`bootstrap.yaml --limit dungeon --check --diff`, the real run, and a second run with `changed=0`. Images move to
the pinned versions (nginx 1.29 → 1.31, portainer 2.35 → 2.45). Leftovers of the fork (`~/dev/infrastructure`,
`/etc/docker/infrastructure/borgmatic`, `/home/borg`) stay untouched; Mika removes them by hand.

### Delivery

1. **PR 1, split:** golden files, component roles, `stack` as assembler, Molecule unchanged in what it checks.
   No dungeon yet.
2. **PR 2, dungeon:** inventory + `host_vars/dungeon.yaml`, the per-host switches in base/docker/secrets, the SSD
   mount and marker, the `backup_target` component (restic user), KeePass per host incl. `bin/with-keepass`, the
   local repository, a second Molecule platform `dungeon-trixie` with dungeon's
   components, README/CLAUDE.md.

## Left to the builder

Variable names beyond the ones above, the merge mechanics (filter plugin or Jinja `combine`), how `stack`
includes the components, test layout, commit slicing inside each PR.

## Facts from dungeon (2026-11-09)

- Backup SSD: `/dev/sda1`, ext4, UUID `eeeb6563-1a18-4941-a7bd-72ec86622fa7`, 1.8 TB (44 GB used), mounted at
  `/media/backup`. `/etc/fstab` mounts it by PARTUUID with `defaults,noatime` and **without `nofail`**, so a missing
  SSD stops the boot today. The role replaces that line with a UUID mount, `defaults,noatime,nofail`.
- On the SSD besides `restic/` (owner `restic`, 0700): `borg/`, `kopia/`, `rsnapshot/` (root, last written
  2026-11-08 02:00, a hand-made rsnapshot setup on dungeon) and `lost+found`. All of them are left alone; the base
  role's rsnapshot stays off on dungeon, so it doesn't replace that setup.
- Root disk: NVMe, 1.7 TB.
- Docker 29.7.2 (`/usr/bin/docker`), i.e. `docker-ce` from Docker's repository (Debian ships 26.x), so the `docker`
  role installs nothing new.
- Below the mount point on the root disk: root-owned `/media/backup` (0755) with a stale `rsnapshot/` from
  2026-03-13 (Mika's hand-made rsnapshot once ran without the SSD). `restic` can't write there.
- Still needed: `id restic` on dungeon (UID/GID to pin).
