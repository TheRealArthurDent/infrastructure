# Ansible

Ansible sets up the Pi host: SSH port and hardening, sudoers, base packages, rsnapshot, Docker, secret `.env` files,
the nginx certificate, the Pi-hole DNS configuration, and the Docker stack itself (`stack` role). `deploy.sh` in
the repo root still works as a fallback until the stack role has proven itself on raspi (#238).

| Playbook | What it does |
| --- | --- |
| `bootstrap.yaml` | Roles `ssh`, `base`, `docker`, `secrets`, `stack`: SSH port and key-only login, packages, sysctl, sudoers, rsnapshot cron, Docker, `.env` files under `/etc/docker/`, nginx/resticprofile config, docker networks, `docker compose up` (tag `stack` for the stack alone) |
| `update.yaml` | `apt dist-upgrade` with autoremove; reports when the host needs a reboot. Run on purpose, `bootstrap.yaml` doesn't upgrade packages (#237) |
| `check-certificates.yaml` | Copies the root CA from KeePass to the host, renews `/etc/docker/infrastructure/nginx/<host>.pem` when it expires within 30 days, restarts nginx |
| `setup-pihole.yaml` | Syncs A records, CNAME records, block lists, groups, clients and domains from `configs/pihole.yaml` through the Pi-hole REST API |

## Hosts

Defined in `inventory/hosts.yaml`:

| Host | IP | SSH port | User |
|---|---|---|---|
| `raspi` | 192.168.1.2 | 1357 | `smarthome` (production) |
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

The paths depend on your machine, so they aren't in the repo. Set them once in a local file that git ignores:

```sh
cp inventory/group_vars/all/local.example.yaml inventory/group_vars/all/local.yaml
# then edit local_keepass_db and local_keepass_key
```

`KEEPASS_DB` / `KEEPASS_KEY` win over `local.yaml`. A run fails at the start if the files aren't found.

Keep the database on a path WSL sees without a mount of its own, i.e. under `/mnt/c`. A Google Drive
drive letter (`H:`, `/mnt/h` in WSL) needs its own mount, which breaks regularly. With Google Drive for
desktop, either mirror My Drive, or stream it into a folder on `C:` and make the KeePass folder
available offline (right click in Explorer).

The master password is asked for at the start of each run, unless `KEEPASS_PASSWORD` is set. On WSL,
`bin/with-keepass` sets it from Windows Credential Manager, so runs need no prompt:

```sh
bin/with-keepass --store     # once, and again after changing the master password
bin/with-keepass ansible-playbook bootstrap.yaml --limit testbed --check --diff
bin/with-keepass --forget    # remove it again
```

The password is stored under "Web Credentials" as `raspberry-keepass` (change with `KEEPASS_CREDENTIAL`) and
protected by your Windows login. Anyone who can run commands as you can read it, just like the database
itself. Without a stored password, `with-keepass` still runs the command and the playbook asks.

Groups in the lookup are paths (`group='smart-home/ioBroker'`); a group or entry that is missing or matches
more than once fails the run instead of picking one.

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

# Fallback until #238 is done (from the repo root)
./deploy.sh testbed 4242 testuser    # ./deploy.sh alone deploys to raspi
```

A second run of `bootstrap.yaml` should end with `changed=0`.

## Set up a host from scratch

### 1. Flash the SSD

In Raspberry Pi Imager:

1. Device: Raspberry Pi 4. OS: Raspberry Pi OS Lite (64-bit), Debian trixie. Storage: the SSD (USB adapter).
2. Edit settings (OS customisation):
   - Hostname: `raspi` or `testbed`
   - Username: `smarthome` (raspi) or `testuser` (testbed), with a password
   - Services: enable SSH, **public-key authentication only**, paste the content of `~/.ssh/id_ed25519.pub`
3. Write.

Booting from USB needs a bootloader with USB boot enabled. If the Pi does not boot from the SSD, flash
*Misc utility images → Bootloader → USB Boot* to an SD card once and boot from it.

### 2. First boot

1. Connect the SSD, boot the Pi.
2. Make sure it gets the IP from the inventory (e.g. a fixed DHCP lease in the router).
3. The new install has a new host key; remove the old ones for both ports:
   `ssh-keygen -R 192.168.1.7 && ssh-keygen -R '[192.168.1.7]:4242'`. Ansible accepts keys of unknown hosts
   but refuses changed ones (`StrictHostKeyChecking=accept-new` in `ansible.cfg`).
4. Check login works on port 22: `ssh testuser@192.168.1.7`

### 3. Bootstrap

```sh
cd ansible
ansible-playbook bootstrap.yaml --limit testbed --skip-tags stack
```

The `ssh` role finds SSH on port 22, moves it to the port from the inventory and continues there. It also turns
off password and root logins, so make sure key login works first (step 2). If `sudo` asks
for a password on the fresh install, add `--ask-become-pass` to this first run.

Reboot afterwards so the Docker group membership takes effect:
`ssh -p 4242 testuser@192.168.1.7 sudo reboot`

### 4. Backup access

On the Pi, authorise the restic key on the backup host `dungeon` once (the `secrets` role created the key and the
script in step 3):

```sh
sudo /etc/docker/infrastructure/init-restic.sh
```

### 5. Certificate, stack, DNS

The stack needs the nginx certificate, so it comes after `check-certificates.yaml` (the `stack` role stops
with a message if the certificate is missing). It also needs `dungeon` to resolve on the Pi: the role looks up
its IP at every run, like `run.sh` did.

```sh
ansible-playbook check-certificates.yaml --limit testbed
ansible-playbook bootstrap.yaml --limit testbed --tags stack
ansible-playbook setup-pihole.yaml --limit testbed
```

On a fresh host, the `stack` role restores `restore_items` (`inventory/group_vars/all/restore_items.yaml`) from
the latest snapshot in the host's restic repository before it starts the stack (#287), then writes
`/var/lib/restic-restore/infrastructure.done`. A host whose item folders already hold data only gets the marker.

- `-e skip_infrastructure_restore=true`: a new host with nothing to restore; only writes the marker.
- `-e infrastructure_restore_snapshot=<id>`: restore another snapshot than the latest.
- If `dungeon` refuses the key or has no snapshot for the host, the run stops and says what to do.

resticprofile backs up the whole host, so the restore helper `/usr/local/bin/restic-restore` is shared by all
stacks: each one lists its items in `/etc/restic-restore/stacks/<stack>.json` and runs
`restic-restore --runner oneoff clean-slate <stack>` before it starts. resticprofile doesn't schedule backups
before every stack in `stack_backup_stacks` (role default: this stack only) has its marker, so a new host never
backs up a half-restored state as the latest snapshot. Add a stack there once its deployment does its restore.

### Targeted restore

On a running host, the helper brings back single items or files (#286). It runs restic in the running
resticprofile container, and takes host paths:

```sh
sudo restic-restore snapshots                                   # add --from rsnapshot for the local copies
sudo restic-restore ls /etc/docker/infrastructure/portainer/data --snapshot <id>
sudo restic-restore find 'portainer.db*'                        # in the latest snapshot, or --snapshot <id>
sudo restic-restore stage infrastructure/portainer              # prints the staging dir under /var/restore
sudo restic-restore diff <staging dir>                          # what apply would change
sudo restic-restore apply <staging dir>                         # stops the item's containers meanwhile
sudo restic-restore clean                                       # staging dirs older than 7 days, or --all
```

- `stage ... --from rsnapshot [--snapshot daily.1]` takes the local rsnapshot copy under `/var/cache/backup/`
  (fast, no network). Without a local copy it falls back to the latest restic snapshot.
- `stage <host path>` stages any path in the backup for copying by hand; `apply` only puts items in place, since
  only for them it knows the containers to stop and the files to keep (`preserve`).

For anything else, see `basic-setup/setup.txt` (step 5).

## Tests

See the [main README](../README.md#testing) for unit tests, Molecule and lint.
