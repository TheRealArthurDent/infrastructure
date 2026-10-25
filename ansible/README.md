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

On a fresh host, the `stack` role restores the state that isn't IaC from the backup before it starts the stack;
see [Clean slate](#clean-slate-reinstall-or-crash) below.

## Restore

resticprofile backs up `/home`, `/etc` and `/var/lib/docker/volumes` every night at 03:00 to the host's own
repository `sftp:restic@dungeon:/media/backup/restic/<host>`. rsnapshot keeps local copies of the same trees
under `/var/cache/backup/` (7 daily, 4 weekly), which are gone after a crash.

Most of the host doesn't need a restore: Ansible renders the configs, certificates and `.env` files (secrets come
from KeePass), `setup-pihole.yaml` configures the Pi-hole, and the restic SSH key is created anew. What does
need one is listed as `restore_items` in `inventory/group_vars/all/restore_items.yaml` (for this stack: the
Portainer data and the Pi-hole folder). The backup excludes `.env` files, `*secret*`, private keys and
`pihole-FTL.db`, so a restore never brings back stale secrets.

Both scenarios use the helper `/usr/local/bin/restic-restore`, which the `stack` role installs. It runs restic in
the resticprofile container, so repository, passphrase and SSH key are the ones the backups use, and it takes
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

### Clean slate (reinstall or crash)

The host is rebuilt and its non-IaC state comes back from restic automatically, before the stack starts. Follow
[Set up a host from scratch](#set-up-a-host-from-scratch):

1. Flash, first boot, `bootstrap.yaml --skip-tags stack`, reboot (steps 1 to 3).
2. Authorise the new restic key on `dungeon` with `sudo /etc/docker/infrastructure/init-restic.sh` (step 4). The
   `secrets` role creates a new key per install; it reaches the existing repository of the host, since the
   repository path is the hostname and the passphrase comes from KeePass.
3. `check-certificates.yaml`, then `bootstrap.yaml --tags stack` (step 5). Before `docker compose up`, the role
   runs `restic-restore --runner oneoff clean-slate infrastructure`, which restores every item from the latest
   snapshot in a one-off resticprofile container and writes the marker `/var/lib/restic-restore/infrastructure.done`.
   The `--check --diff` run before it reports `would restore infrastructure: pihole, portainer from snapshot <id>`.
4. `setup-pihole.yaml` (step 5) puts the Pi-hole configuration from `configs/pihole.yaml` on top.
5. A second `bootstrap.yaml --tags stack` run ends with `changed=0`: the marker makes the restore a no-op.

What the role decides:

- Marker present: nothing to do. Every later run is a no-op, so a deliberate second restore of the whole stack
  goes through the targeted restore, not by deleting the marker.
- No marker, but an item folder already holds files (a host that existed before #287, like raspi): it only
  writes the marker, with the folder named in it.
- No marker, item folders empty or missing: it restores. A run that dies halfway left
  `infrastructure.done.restoring` and restores again on the next run.

Options and failures:

- `-e infrastructure_restore_snapshot=<id>`: restore another snapshot than the latest, e.g. when the latest
  one was taken after the data was already broken. Find the ID with `sudo restic-restore --runner oneoff snapshots`.
- `-e skip_infrastructure_restore=true`: a new host with nothing to restore; only writes the marker.
- The run stops with a hint if `dungeon` refuses the key (run `init-restic.sh`, then the playbook again) or the
  repository has no snapshot (a new host: run again with `skip_infrastructure_restore`).

Backups stay off until the restore is done: resticprofile schedules them only once every stack in
`stack_backup_stacks` (role default: this stack) has its marker, so a fresh host never backs up its empty or
half-restored state as the latest snapshot. `docker logs resticprofile` says which marker it waits for. On a
host that already has data, run `bootstrap.yaml` once (it writes the marker) before deploying with `deploy.sh`,
or backups pause there too.

### Other stacks

resticprofile backs up the whole host, so the helper is shared. Every stack lists its items in
`/etc/restic-restore/stacks/<stack>.json` (`{"items": {name: {paths, containers, preserve}}}`), runs
`restic-restore --runner oneoff clean-slate <stack>` before it starts, and is added to `stack_backup_stacks` once
its deployment does. Its items then work with `stage <stack>/<item>` and `apply` like this stack's. The
smart-home stack does since TheRealArthurDent/smart-home#1, so `inventory/group_vars/all/restore_items.yaml` adds
it to `stack_backup_stacks`: backups also wait for `/var/lib/restic-restore/smart-home.done`. `deploy.sh` still
renders the old list (only this stack).

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

The schedule and the number of parts are in `stack_restore_drill` (stack role defaults). On a host that is often
switched off on the 1st, set `stack_restore_drill_enabled: false` (testbed has it in `inventory/hosts.yaml`): a
missed drill would turn the healthcheck red. `sudo restic-restore drill` still works there by hand.

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

The full drill reflashes testbed and follows [Clean slate](#clean-slate-reinstall-or-crash); only it covers
`init-restic.sh` and the one-off container on a host without a running stack.

## Tests

See the [main README](../README.md#testing) for unit tests, Molecule and lint.
