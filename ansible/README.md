# Ansible

Ansible sets up the Pi hosts: SSH port and hardening, sudoers, base packages, rsnapshot, Docker, secret `.env` files,
the nginx certificate, the Pi-hole DNS configuration, the backup SSD of `dungeon`, and the Docker stack itself (`stack`
role plus its component roles, see [Stack components](#stack-components)).

Contents:

- [Hosts](#hosts)
- [Control node setup (once)](#control-node-setup-once)
  - [KeePass](#keepass)
- [Daily business](#daily-business)
- [Stack components](#stack-components)
- [Set up a host (new install or after a crash)](#set-up-a-host-new-install-or-after-a-crash)
  - [1. Flash the SSD](#1-flash-the-ssd)
  - [2. First boot](#2-first-boot)
  - [3. Bootstrap the host](#3-bootstrap-the-host)
  - [4. Backup access](#4-backup-access)
  - [5. Certificate and stack (restores the infrastructure state)](#5-certificate-and-stack-restores-the-infrastructure-state)
  - [6. Pi-hole](#6-pi-hole)
  - [7. smart-home (restores its volumes)](#7-smart-home-restores-its-volumes)
- [dungeon](#dungeon)
  - [Takeover of dungeon](#takeover-of-dungeon)
- [Restore](#restore)
  - [Targeted restore (host running)](#targeted-restore-host-running)
  - [Clean slate restore](#clean-slate-restore)
  - [Other stacks](#other-stacks)
  - [Monthly drill (every host)](#monthly-drill-every-host)
  - [Restore drill (testbed only)](#restore-drill-testbed-only)
- [Tests](#tests)

| Playbook | What it does |
| --- | --- |
| `bootstrap.yaml` | Roles `ssh`, `base`, `docker`, `secrets`, `argon_fan` (raspi only), `backup_target` (dungeon only), `stack`: SSH port and key-only login, DNS fallback while Pi-hole is down, memory cgroup on the kernel command line, packages, sysctl, sudoers, rsnapshot cron, Docker, fan control of the Argon ONE V2 case, `.env` files under `/etc/docker/`, nginx/resticprofile config, docker networks, `docker compose up` (tag `stack` for the stack alone) |
| `update.yaml` | `apt dist-upgrade` with autoremove; reports when the host needs a reboot. Run on purpose, `bootstrap.yaml` doesn't upgrade packages (raspberry#237) |
| `check-certificates.yaml` | Copies the root CA from KeePass to the host, renews `/etc/docker/infrastructure/nginx/<host>.pem` when it expires within 30 days, restarts nginx |
| `setup-pihole.yaml` | Syncs A records, CNAME records, block lists, groups, clients and domains from `configs/pihole.yaml` through the Pi-hole REST API |

## Hosts

Defined in `inventory/hosts.yaml`, which only says how to reach each host. Settings shared by all hosts (SSH key,
`become`, KeePass paths) are in `inventory/group_vars/all/`, per-host values (`dhparameter_bits`) in
`inventory/host_vars/<host>.yaml`:

| Host | IP | SSH port | User |
| --- | --- | --- | --- |
| `raspi` | 192.168.1.2 | 1357 | `smarthome` (production) |
| `dungeon` | 192.168.1.5 | 1359 | `dragon` (production: private cloud and backup target) |
| `testbed` | 192.168.1.7 | 4242 | `testuser` |

> All playbooks target `hosts: all` and refuse to run without `--limit <host>`, so a forgotten `--limit`
> never reaches `raspi` by accident.

## Control node setup (once)

Run everything from `ansible/`, so that `ansible.cfg` and the inventory are picked up.

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # in the repo root
source .venv/bin/activate
cd ansible
ansible-galaxy collection install -r requirements.yaml
```

You also need:

- the SSH key `~/.ssh/id_ed25519` (its public key goes onto the Pi),
- the KeePass database and key file (see below).

### KeePass

Every host reads its secrets from its own KeePass database (host variable `keepass_database`: `raspberry` by default,
`dungeon` for dungeon). raspi and testbed share the `raspberry` database. The root CA for `check-certificates.yaml`
always comes from `keepass_ca_database` (`raspberry`), so `check-certificates.yaml --limit dungeon` and
`setup-pihole.yaml --limit dungeon` (which also needs the root CA) open raspi's database as well: both master passwords
are needed (`bin/with-keepass --store` stores one per database). The CA stays there until it gets a database of its
own (#10); then only `keepass_ca_database` changes.

The paths depend on your machine, so they aren't in the repo. Set them once in a local file that git ignores, one
entry per database in `local_keepass_databases`:

```sh
cp inventory/group_vars/all/local.example.yaml inventory/group_vars/all/local.yaml
# then edit the db and key paths of raspberry and dungeon in local_keepass_databases
```

Database names are lowercase letters, digits and `_` (`my-db` fails with a message). `KEEPASS_DB` / `KEEPASS_KEY` win
over `local.yaml` for `raspberry`, and the old `local_keepass_db` / `local_keepass_key` still work for it. A run fails
at the start if the files aren't found.

Keep the databases on a path WSL sees without a mount of its own, i.e. under `/mnt/c`. A Google Drive
drive letter (`H:`, `/mnt/h` in WSL) needs its own mount, which breaks regularly. With Google Drive for
desktop, either mirror My Drive, or stream it into a folder on `C:` and make the KeePass folder
available offline (right click in Explorer).

The master password of each database is asked for at the start of each run, unless it is set in the environment:
`KEEPASS_PASSWORD` for `raspberry`, `KEEPASS_PASSWORD_<NAME>` for the others (`KEEPASS_PASSWORD_DUNGEON`). On WSL,
`bin/with-keepass` sets them from Windows Credential Manager, so runs need no prompt. Variables that are already set
stay as they are.

```sh
bin/with-keepass --store            # raspberry; once, and again after changing the master password
bin/with-keepass --store dungeon    # the dungeon database
bin/with-keepass ansible-playbook bootstrap.yaml --limit testbed --check --diff
bin/with-keepass --forget dungeon   # remove one again (default: raspberry)
```

Each password is stored under "Web Credentials" as `<name>-keepass` (`raspberry-keepass`, `dungeon-keepass`; user
`keepass`) and protected by your Windows login. Anyone who can run commands as you can read it, just like the database
itself. Without a stored password, `with-keepass` still runs the command and the playbook asks, naming the database.

Groups in the lookup are paths (`group='smart-home/ioBroker'`); a group or entry that is missing or matches
more than once fails the run instead of picking one.

#### Entries each database needs

Group/title, with the fields the roles read:

| Database | Entry | Content |
| --- | --- | --- |
| `dungeon` | `Backup/Resticprofile` | password: the passphrase of dungeon's own restic repository |
| `dungeon` | `Discord/Docker Webhook` | fields `id` and `token` (a webhook of its own, so Discord shows which host wrote) |
| `dungeon` | `OpenCloud/admin` | password: the initial password of OpenCloud's user `admin` (private-cloud) |
| `dungeon` | `Pihole/outbound` | password of dungeon's outbound Pi-hole |
| `raspberry` | `Backup/Resticprofile SSH` | attachments `id_ed25519` and `id_ed25519.pub`: raspi's restic key pair |
| `raspberry` | `Backup/Resticprofile SSH testbed` | the same for testbed |
| `raspberry` | `Discord/Docker Webhook testbed` | fields `id` and `token` of testbed's webhook |
| `raspberry` | `OpenCloud/admin testbed` | the same for testbed's OpenCloud |

| `raspberry` | `Backup/Resticprofile` | password: the passphrase of raspi's restic repository |
| `raspberry` | `Backup/Resticprofile testbed` | password: the passphrase of testbed's restic repository (throwaway) |
| `raspberry` | `Pihole/pihole`, `Pihole/outbound` | passwords of raspi's and testbed's Pi-holes |

raspi's other entries (`Discord/Docker Webhook`, `smart-home/...` for the smart-home stack, `Backup/Dungeon` with the
field `ssh-port`) are the existing ones. dungeon's database holds nothing of the smart-home stack. The entry titles
are host variables (`discord_docker_webhook_title`, `resticprofile_ssh_key_title`, `resticprofile_passphrase_title`,
`opencloud_admin_title`; testbed sets its own in `host_vars/testbed.yaml`). The OpenCloud entries are only read on hosts
with `secrets_opencloud_env` (dungeon, testbed).

Passwords may hold any printable character (`$`, `{{ }}`, quotes, backslashes, `#`, blanks, umlauts): the secrets role
quotes every `.env` value for Compose (`dotenv_quote`). It refuses, before writing anything, line breaks and other
control characters in any entry, `'` and `\` in the InfluxDB passwords (`smart-home/Influx DB`; the influxdb image's
init script can't create the user then) and `\` in the deCONZ VNC password (deCONZ' start script changes it).

#### Put the restic key pairs into KeePass

The sftp clients (raspi, testbed) back up to dungeon with a key pair from KeePass, which the `secrets` role writes to
the host. A rebuilt host has the same key, so dungeon's `authorized_keys` (`backup_target_clients` in
`host_vars/dungeon.yaml`) never changes. Nothing is generated on the host any more (`init-restic.sh` is gone).

- raspi: keep the key that dungeon already accepts. Copy `id_ed25519` and `id_ed25519.pub` from
  `/etc/docker/infrastructure/resticprofile/.ssh/` on raspi to your machine (they are root-owned, so copy them with
  `sudo`; the private key is mode 0600), attach both to the entry `Backup/Resticprofile SSH` in the `raspberry`
  database and delete the copies. Check that the public key equals `backup_target_clients.raspi`.
- testbed: keep its existing key as well. Copy `id_ed25519` and `id_ed25519.pub` from
  `/etc/docker/infrastructure/resticprofile/.ssh/` on testbed exactly as for raspi, attach both to
  `Backup/Resticprofile SSH testbed` and check that the public key equals `backup_target_clients.testbed`. A new pair
  (`ssh-keygen -t ed25519 -N '' -C resticprofile@testbed -f ./id_ed25519`) needs its public key in
  `host_vars/dungeon.yaml` and a dungeon run first. Delete the copies after attaching them.

The `secrets` role compares the public key of the entry with `backup_target_clients.<host>` before it writes the key
files and stops with the next step if they differ, so a wrong attachment never replaces a working key. The comparison
uses public keys only.

## Daily business

```sh
# Check a host is reachable
ansible testbed -m ping

# Dry run first, always
ansible-playbook bootstrap.yaml --limit testbed --check --diff

# Apply: config, Docker, secrets
ansible-playbook bootstrap.yaml --limit testbed

# OS updates (apt dist-upgrade); tells you when a reboot is needed
ansible-playbook update.yaml --limit testbed

# Check / renew the nginx certificate
ansible-playbook check-certificates.yaml --limit testbed

# Change DNS records, block lists, groups, clients or domains of a Pi-hole (pihole or outbound):
# edit configs/pihole.yaml, then
ansible-playbook setup-pihole.yaml --limit testbed
# Each instance is reached at <name>.<host> (pihole.testbed, outbound.testbed); set host in its config entry to override
# Prefix any playbook with bin/with-keepass to skip the KeePass prompt (see KeePass above)
bin/with-keepass ansible-playbook setup-pihole.yaml --limit testbed

# Redeploy / update only the Docker stack (config, networks, compose up; reloads nginx on config changes)
ansible-playbook bootstrap.yaml --limit testbed --tags stack
```

A second run of `bootstrap.yaml` should end with `changed=0`.

When an image tag changed (a Renovate update), the dry run can't pull the new image, so Compose can't simulate
recreating its containers. "Ensure the stack is up" then reports `changed`, and "Note the image that check mode
couldn't pull" names the image; the real run pulls it. Any other Compose error still fails the dry run.

## Stack components

The `stack` role assembles the Docker stack from component roles: `reverse_proxy` (nginx), `portainer`, `unbound`,
`pihole`, `outbound`, `resticprofile` (backups, restore helper) and `healthmonitor`. Each brings its compose fragment
and config; `stack` merges the fragments, runs the clean-slate restore and `docker compose up`. `stack_components`
lists the enabled ones and defaults to all seven. To run fewer on a host, set it in `inventory/host_vars/<host>.yaml`
(components must be listed with the dependencies they need, e.g. `reverse_proxy` for the vhosts).

| Host | Components |
| --- | --- |
| `raspi`, `testbed` | all seven |
| `dungeon` | `reverse_proxy`, `portainer`, `unbound`, `outbound`, `resticprofile`, `healthmonitor` (no LAN Pi-hole) |

dungeon also has the host role `backup_target` (SSD mount, `restic` user), which is not part of the `stack` tag. Its
nginx serves the vhosts `portainer` and `outbound`, and its resticprofile backs up to a local repository on the SSD
(see [dungeon](#dungeon)).

## Set up a host (new install or after a crash)

The same steps for a new Pi and for one rebuilt after a crash or reinstall. On a host with a backup, the state that
isn't IaC comes back from restic by itself in steps 5 and 7, before the stacks start
([how it decides](#clean-slate-restore)). `<target>` is the inventory host (`raspi`, `dungeon` or `testbed`); `<user>`,
`<ip>` and `<port>` are its values in `inventory/hosts.yaml`. Playbooks run in `ansible/` on the control node. Where
dungeon differs, the step says so; [dungeon](#dungeon) has the details.

### 1. Flash the SSD

In Raspberry Pi Imager:

1. Device: Raspberry Pi 4 (dungeon: Raspberry Pi 5). OS: Raspberry Pi OS Lite (64-bit), Debian trixie. Storage: the
   SSD (USB adapter); on dungeon the NVMe drive. **dungeon: unplug the backup SSD first**, so it can't be picked as
   the target.
2. Edit settings (OS customisation):
   - Hostname: `raspi`, `dungeon` or `testbed`
   - Username: `smarthome` (raspi), `dragon` (dungeon) or `testuser` (testbed), with a password
   - Services: enable SSH, **public-key authentication only**, paste the content of `~/.ssh/id_ed25519.pub`
3. Write.

Booting from USB needs a bootloader with USB boot enabled. If the Pi does not boot from the SSD, flash
*Misc utility images → Bootloader → USB Boot* to an SD card once and boot from it. (The Pi 5 of dungeon boots from
its NVMe drive.)

### 2. First boot

Connect the SSD, boot the Pi and make sure it gets the IP from the inventory (e.g. a fixed DHCP lease in the
router). **dungeon: boot without the backup SSD** (it has no boot partition, but there is no reason to risk it). The new
install has a new host key, so remove the old ones for both ports; Ansible accepts keys of unknown hosts but refuses
changed ones (`StrictHostKeyChecking=accept-new` in `ansible.cfg`):

```sh
ssh-keygen -R <ip> && ssh-keygen -R '[<ip>]:<port>'
```

Check that key login works on port 22:

```sh
ssh <user>@<ip>
```

### 3. Bootstrap the host

**dungeon: plug in the backup SSD now.** The `backup_target` role is a host role, so it runs here (not under the
`stack` tag): it mounts the SSD by UUID (`nofail`), checks `mountpoint -q /media/backup`, creates the `restic` user
(UID/GID 1002, so the files on the SSD keep their owner), writes its `authorized_keys` and the marker
`/media/backup/.backup-ssd`. Without the SSD it stops with `plug in the backup SSD (UUID ...) and run again` and writes
nothing below the empty mount point. Plug it in and run again. From here raspi's nightly backup works again.

```sh
bin/with-keepass ansible-playbook bootstrap.yaml --limit <target> --skip-tags stack --ask-become-pass
```

The `ssh` role finds SSH on port 22, moves it to the port from the inventory and continues there. It also turns
off password and root logins, so make sure key login works first (step 2). `sudo` on the fresh install asks for
the password set in the Imager (`Missing sudo password` without `--ask-become-pass`); the `base` role's sudoers
entry drops that for later runs.

Reboot, so the Docker group membership takes effect:

```sh
ssh -p <port> <user>@<ip> sudo reboot
```

### 4. Backup access

raspi and testbed: the `secrets` role wrote the host's restic key pair from its KeePass entry
(`Backup/Resticprofile SSH`, attachments `id_ed25519` and `id_ed25519.pub`; `resticprofile_ssh_key_title` names another
entry, see [KeePass](#put-the-restic-key-pairs-into-keepass)). The backup host `dungeon` must accept the public key: it
is in `backup_target_clients` in `host_vars/dungeon.yaml`, applied by dungeon's playbook.

The key reaches the host's existing repository: its path is the hostname, the passphrase comes from KeePass.

dungeon: nothing to do, its repository is local on the SSD.

### 5. Certificate and stack (restores the infrastructure state)

The stack needs the nginx certificate (the `reverse_proxy` role stops with a message if it is missing). On raspi and
testbed it also needs `dungeon` to resolve on the Pi (the role looks up its IP at every run); dungeon resolves
through raspi's Pi-hole, so raspi must be up. `--tags stack` also runs the `secrets` role, so a changed KeePass
entry reaches its file; a running container only sees it after a restart (`docker restart resticprofile` for the
restic passphrase).

```sh
bin/with-keepass ansible-playbook check-certificates.yaml --limit <target>
bin/with-keepass ansible-playbook bootstrap.yaml --limit <target> --tags stack --check --diff
bin/with-keepass ansible-playbook bootstrap.yaml --limit <target> --tags stack
```

The dry run reports `would restore infrastructure: pihole, portainer from snapshot <id>` (dungeon: `portainer`, from
the repository on its SSD). Before `docker compose up`, the real run restores every item from the latest snapshot in a
one-off resticprofile container and writes the marker `/var/lib/restic-restore/infrastructure.done`. On dungeon the
restore asserts the SSD marker first, so a missing SSD stops the run instead of reading as "no snapshot, new host". A
new host without any snapshot stops here with a hint; skip the restore then:

```sh
bin/with-keepass ansible-playbook bootstrap.yaml --limit <target> --tags stack -e skip_infrastructure_restore=true
```

### 6. Pi-hole

Puts the Pi-hole configuration from `configs/pihole.yaml` on top of the restored state. dungeon has no LAN Pi-hole;
this provisions its `outbound` instance (`https://outbound.dungeon`). The CNAME `outbound.dungeon` lives on raspi's
Pi-hole, so run `setup-pihole.yaml --limit raspi` (the user's job) before dungeon's first run.

```sh
bin/with-keepass ansible-playbook setup-pihole.yaml --limit <target>
```

A second stack run ends with `changed=0`, since the marker makes the restore a no-op:

```sh
bin/with-keepass ansible-playbook bootstrap.yaml --limit <target> --tags stack
```

### 7. smart-home (restores its volumes)

raspi and testbed only; dungeon has no smart-home stack and no apps in this repo (OpenCloud and the like come from
their own repo later).

Deploy the smart-home stack, which restores its own volumes the same way (TheRealArthurDent/smart-home#1).
Backups wait for its marker too (`resticprofile_backup_stacks`), so don't skip this. In `ansible/` of the
smart-home repo:

```sh
ansible-playbook deploy.yaml --limit <target> --check --diff
ansible-playbook deploy.yaml --limit <target>
```

On a new host without a snapshot, add `-e skip_smart_home_restore=true`. Then check that resticprofile no longer
waits for a marker and has scheduled the backups:

```sh
ssh -p <port> <user>@<ip> docker logs resticprofile
```

## dungeon

The Pi 5 in the Argon NEO 5 case: private cloud and the backup target of raspi and testbed. It is production like
raspi: the user runs everything against it, and Claude's guard hook blocks it once the patch from #15 is applied
(treat it as blocked already).

- **Backup SSD**: ext4, UUID `eeeb6563-1a18-4941-a7bd-72ec86622fa7`, mounted at `/media/backup`
  (`defaults,noatime,nofail`), marker `/media/backup/.backup-ssd`. raspi and testbed back up to
  `/media/backup/restic/<host>` over sftp as the `restic` user (a locked account with a shell for sftp, UID/GID 1002,
  `/media/backup/restic` is `0700`). dungeon's own repository is `/media/backup/restic/dungeon`, seen from the
  container as `local:/backup/restic/dungeon` (`/media/backup` is bind-mounted as `/backup` with propagation
  `rslave`, so a later mount reaches a running container).
- **SSD missing** never writes to the system disk: `backup_target` stops the bootstrap before it creates anything,
  resticprofile's `run-before` fails the backup when the marker is missing (healthmonitor reports it), the clean-slate
  restore stops on the missing marker, and raspi's sftp backup fails because the `restic` user can't create a
  repository in the empty, root-owned mount point.
- **Differences from raspi**: DNS through raspi's Pi-hole (`base_dns_servers`, `docker_daemon_dns` are
  `192.168.1.2`), rsnapshot off (the hand-made one on the SSD stays untouched), no `argon_fan` (the Pi 5 fan header
  is controlled by the kernel), `dhparameter_bits: 4096`, no smart-home `.env`, `resticprofile_backup_stacks` is only
  `infrastructure`.
- **Lost SSD**: dungeon's and raspi's backups are gone with it; restoring dungeon would need an off-site copy, which
  doesn't exist yet.

### Takeover of dungeon

dungeon runs a Compose project `infrastructure` from the fork `private-cloud-infrastructure` (nginx, portainer,
healthmonitor, same paths under `/etc/docker/infrastructure`, same container names and the networks `siz` and
`outbound`), which Ansible adopts. Do this once, from the control node, with the SSD plugged in and the dungeon
entries in KeePass ([Entries each database needs](#entries-each-database-needs)); `local.yaml` has the `dungeon`
database and `bin/with-keepass --store dungeon` has stored its master password. Raspi's Pi-hole needs the CNAME
`outbound.dungeon` first (`setup-pihole.yaml --limit raspi`).

Order after merging this change, because the new key and DNS entries are prerequisites of the later steps:

1. KeePass: the entries for all three hosts, including the restic key pairs of raspi and testbed (see
   [Put the restic key pairs into KeePass](#put-the-restic-key-pairs-into-keepass)). Every raspi run needs
   `Backup/Resticprofile SSH` first, `--tags stack` included, and testbed needs its two new entries.
2. `setup-pihole.yaml --limit raspi` (the CNAME `outbound.dungeon`).
3. raspi: `bootstrap.yaml --limit raspi --check --diff`. Expect `init-restic.sh` to be removed and the key files
   unchanged. Then the real run.
4. The takeover of dungeon (below).
5. testbed.

```sh
# 1. The CA-signed certificate replaces the self-signed one (opens raspi's database for the root CA)
bin/with-keepass ansible-playbook check-certificates.yaml --limit dungeon
# 2. Dry run, read the diff
bin/with-keepass ansible-playbook bootstrap.yaml --limit dungeon --check --diff
```

The dry run tells you in a debug line that the marker `/media/backup/.backup-ssd` is created by the real run (and, on a
freshly flashed dungeon, that the SSD is mounted by it). Before step 3, back up portainer's data by hand on dungeon: the
image upgrade migrates its database, and dungeon has no restic snapshot yet.

```sh
sudo cp -a /etc/docker/infrastructure/portainer/data /root/portainer-data.pre-takeover
```

```sh
# 3. The real run
bin/with-keepass ansible-playbook bootstrap.yaml --limit dungeon
# 4. A second run must end with changed=0
bin/with-keepass ansible-playbook bootstrap.yaml --limit dungeon
# 5. Outbound Pi-hole
bin/with-keepass ansible-playbook setup-pihole.yaml --limit dungeon
```

What to expect: the images move to the pinned versions (nginx 1.29 to 1.31, portainer 2.35 to 2.45). The first real run
finds portainer's data in `/etc/docker/infrastructure/portainer/data`, so the clean-slate restore only writes the
marker `infrastructure.done` and restores nothing; backups start after that. The leftovers of the fork
(`~/dev/infrastructure`, `/etc/docker/infrastructure/borgmatic`, `/home/borg`) stay untouched, remove them by hand. On
the SSD, `borg/`, `kopia/` and `rsnapshot/` stay as they are. `restic`'s `authorized_keys` is replaced by exactly the
keys in `backup_target_clients`: any other key there, for example a personal one, is removed.

## Restore

resticprofile backs up `/home`, `/etc` and `/var/lib/docker/volumes` every night at 03:00 to the host's own
repository `sftp:restic@dungeon:/media/backup/restic/<host>` (dungeon: its local repository
`local:/backup/restic/dungeon` on the backup SSD). rsnapshot keeps local copies of the same trees
under `/var/cache/backup/` (7 daily, 4 weekly), which are gone after a crash; dungeon has none.

Most of the host doesn't need a restore: Ansible renders the configs, certificates and `.env` files (secrets come
from KeePass), `setup-pihole.yaml` configures the Pi-hole, and the restic SSH key comes from KeePass. What does
need one is listed in the `<component>_restore_items` of the component roles' defaults (`portainer_restore_items`,
`pihole_restore_items`; the `stack` role merges them into `stack_restore_items`; for this stack: the Portainer data
and the Pi-hole folder). The backup excludes `.env` files, `*secret*`, private keys and
`pihole-FTL.db`, so a restore never brings back stale secrets.

Both scenarios use the helper `/usr/local/bin/restic-restore`, which the `resticprofile` role installs. It runs
restic in the resticprofile container, so repository, passphrase and SSH key are the ones the backups use, and it takes
host paths. `sudo restic-restore --help` lists all commands. A restore always goes into a staging directory
under `/var/restore/` first and only `apply` puts it in place.

### Targeted restore (host running)

Something was changed or deleted by accident and Ansible can't bring it back, e.g. a Portainer setting. On the
Pi:

1. Pick a snapshot. restic snapshots are taken at 03:00; the local rsnapshot copies (`daily.0` = last night,
   `daily.1`, ..., `weekly.0`, ...) need no network.

   ```sh
   sudo restic-restore snapshots                    # restic, from dungeon
   sudo restic-restore snapshots --from rsnapshot   # local copies
   ```

2. Optionally check that the snapshot has what you're missing:

   ```sh
   sudo restic-restore ls /etc/docker/infrastructure/portainer/data --snapshot <id>
   sudo restic-restore find 'portainer.db*' --snapshot <id>     # without --snapshot: the latest
   ```

3. Stage the item. This prints the staging directory and changes nothing else:

   ```sh
   sudo restic-restore stage infrastructure/portainer --snapshot <id>
   sudo restic-restore stage infrastructure/portainer --from rsnapshot --snapshot daily.1
   ```

   Without `--snapshot` it takes the latest snapshot (the newest local copy with `--from rsnapshot`). Without a
   local copy, `--from rsnapshot` falls back to the latest restic snapshot.

4. See what `apply` would change, then apply it. `apply` stops the item's containers, mirrors the staged files
   into place (files listed under `preserve`, e.g. `pihole-FTL.db`, are kept) and starts the containers again:

   ```sh
   sudo restic-restore diff <staging dir>
   sudo restic-restore apply <staging dir>
   ```

5. Check the service (e.g. log in to Portainer), then remove the staging directory:

   ```sh
   sudo restic-restore clean          # staging dirs older than 7 days; --all for all of them
   ```

Anything that isn't an item, e.g. a single file of another stack, can be staged by its host path and copied by
hand; `apply` refuses it, since it doesn't know which containers to stop:

```sh
sudo restic-restore stage /etc/docker/smart-home/some/file --snapshot <id>
# the file lies under its snapshot path in the staging dir (<staging dir>/source/etc/docker/smart-home/...):
# stop the container that uses it, copy the file into place with cp -a, start the container
```

### Clean slate restore

What steps 5 and 7 of [Set up a host](#set-up-a-host-new-install-or-after-a-crash) do on their own: before
`docker compose up`, the `resticprofile` role (`tasks/restore.yaml`, run by `stack`) runs
`restic-restore --runner oneoff clean-slate infrastructure` (the smart-home role the same for
`smart-home`).

What the role decides:

- Marker present: nothing to do. Every later run is a no-op, so a deliberate second restore of the whole stack
  goes through the targeted restore, not by deleting the marker.
- No marker, but an item folder already holds files (a host that existed before raspberry#287, like raspi): it only
  writes the marker, with the folder named in it.
- No marker, item folders empty or missing: it restores. A run that dies halfway left
  `infrastructure.done.restoring` and restores again on the next run.

Options and failures:

- `-e infrastructure_restore_snapshot=<id>`: restore another snapshot than the latest, e.g. when the latest
  one was taken after the data was already broken. Find the ID on the Pi with:

  ```sh
  sudo restic-restore --runner oneoff snapshots
  ```

  ```sh
  bin/with-keepass ansible-playbook bootstrap.yaml --limit <target> --tags stack -e infrastructure_restore_snapshot=<id>
  ```

- `-e skip_infrastructure_restore=true`: a new host with nothing to restore; only writes the marker (step 5).
- The run stops with a hint if `dungeon` refuses the key (add the public key to `backup_target_clients`,
  run dungeon's playbook, then this one again) or the
  repository has no snapshot (a new host: run again with `skip_infrastructure_restore`).

Backups stay off until the restore is done: resticprofile schedules them only once every stack in
`resticprofile_backup_stacks` (this stack and smart-home) has its marker, so a fresh host never backs up its empty or
half-restored state as the latest snapshot. `docker logs resticprofile` says which marker it waits for.

### Other stacks

resticprofile backs up the whole host, so the helper is shared. Every stack lists its items in
`/etc/restic-restore/stacks/<stack>.json` (`{"items": {name: {paths, containers, preserve}}}`), runs
`restic-restore --runner oneoff clean-slate <stack>` before it starts, and is added to
`resticprofile_backup_stacks` once its deployment does. Its items then work with `stage <stack>/<item>` and `apply`
like this stack's. The smart-home stack does since TheRealArthurDent/smart-home#1, so
`inventory/group_vars/all/backup.yaml` adds
it to `resticprofile_backup_stacks`: backups also wait for `/var/lib/restic-restore/smart-home.done`.

### Monthly drill (every host)

On the 1st of each month at 12:00, root's crontab runs `restic-restore drill`, which only reads: `restic check`
reads one twelfth of the repository's data (a different part each month, all of it over a year), then every item
of every stack is staged from the latest snapshot and removed again. Nothing is put in place, so it also runs on
raspi. The result goes to `/var/lib/resticprofile/metrics/restore_drill.prom`; the resticprofile healthcheck
turns unhealthy when the last drill failed or its last success is older than 35 days, and healthmonitor alerts.

```sh
journalctl -t restic-restore-drill                         # what the last drill did
sudo restic-restore drill                                  # run it now, e.g. after fixing a failure
sudo restic-restore drill --read-data-subset 1/1           # read all of the data (slow)
```

The schedule and the number of parts are in `resticprofile_restore_drill` (resticprofile role defaults). On a host
that is often switched off on the 1st, set `resticprofile_restore_drill_enabled: false` (testbed has it in
`inventory/host_vars/testbed.yaml`): a missed drill would turn the healthcheck red. `sudo restic-restore drill`
still works there by hand, without recording a result.

### Restore drill (testbed only)

Run the drill on testbed against testbed's own repository, never on raspi, e.g. after changes to the helper or
the restore tasks, and now and then to know the backup restores. It needs at least one testbed snapshot
(`sudo restic-restore snapshots`).

Targeted, on the Pi:

```sh
sudo restic-restore stage infrastructure/portainer
sudo restic-restore diff <staging dir>
sudo restic-restore apply <staging dir>    # Portainer is down for a moment, then log in
sudo restic-restore clean --all
```

Clean slate without reflashing: stop the item containers, put the item folders and the marker aside, and let the
`stack` role restore them.

```sh
# On the Pi. The containers must stay stopped: a started Pi-hole fills its empty folder and the role then
# takes it for existing data.
sudo docker stop portainer pihole
sudo mkdir /var/tmp/drill
sudo mv /etc/docker/infrastructure/portainer/data /var/tmp/drill/portainer-data
sudo mv /etc/docker/infrastructure/pihole/pihole /var/tmp/drill/pihole
sudo mv /var/lib/restic-restore/infrastructure.done /var/tmp/drill/

# On the control node, in ansible/
ansible-playbook bootstrap.yaml --limit testbed --tags stack --check --diff   # "would restore ..."
ansible-playbook bootstrap.yaml --limit testbed --tags stack                  # "restored ..."
ansible-playbook bootstrap.yaml --limit testbed --tags stack                  # changed=0

# On the Pi: log in to Portainer and the Pi-hole, check the marker, compare with what was there before (files
# changed since the last backup and pihole-FTL.db differ), then clean up
sudo cat /var/lib/restic-restore/infrastructure.done
sudo diff -rq /var/tmp/drill/portainer-data /etc/docker/infrastructure/portainer/data
sudo rm -rf /var/tmp/drill
```

The full drill reflashes testbed and follows [Set up a host](#set-up-a-host-new-install-or-after-a-crash); only it
covers the restic key pair from KeePass and the one-off container on a host without a running stack.

## Tests

See the [main README](../README.md#testing) for unit tests, Molecule and lint.
