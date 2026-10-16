# Ansible

Ansible sets up the Pi host: SSH port and hardening, sudoers, base packages, rsnapshot, Docker, secret `.env` files,
the nginx certificate, the Pi-hole DNS configuration, and the Docker stack itself (`stack` role).

| Playbook | What it does |
| --- | --- |
| `bootstrap.yaml` | Roles `ssh`, `base`, `docker`, `secrets`, `stack`: SSH port and key-only login, DNS fallback while Pi-hole is down, packages, sysctl, sudoers, rsnapshot cron, Docker, `.env` files under `/etc/docker/`, nginx/resticprofile config, docker networks, `docker compose up` (tag `stack` for the stack alone) |
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
```

A second run of `bootstrap.yaml` should end with `changed=0`.

## Set up a host (new install or after a crash)

The same steps for a new Pi and for one rebuilt after a crash or reinstall. On a host with a backup, the state that
isn't IaC comes back from restic by itself in steps 5 and 7, before the stacks start
([how it decides](#clean-slate-restore)). `<target>` is the inventory host (`raspi` or `testbed`); `<user>`,
`<ip>` and `<port>` are its values in `inventory/hosts.yaml`. Playbooks run in `ansible/` on the control node.

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

Connect the SSD, boot the Pi and make sure it gets the IP from the inventory (e.g. a fixed DHCP lease in the
router). The new install has a new host key, so remove the old ones for both ports; Ansible accepts keys of
unknown hosts but refuses changed ones (`StrictHostKeyChecking=accept-new` in `ansible.cfg`):

```sh
ssh-keygen -R <ip> && ssh-keygen -R '[<ip>]:<port>'
```

Check that key login works on port 22:

```sh
ssh <user>@<ip>
```

### 3. Bootstrap the host

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

The `secrets` role created a new restic key and the script in step 3. Authorise the key on the backup host
`dungeon`, on the Pi:

```sh
sudo /etc/docker/infrastructure/init-restic.sh
```

The new key reaches the host's existing repository: its path is the hostname, the passphrase comes from KeePass.

### 5. Certificate and stack (restores the infrastructure state)

The stack needs the nginx certificate (the `stack` role stops with a message if it is missing) and `dungeon` to
resolve on the Pi (the role looks up its IP at every run).

```sh
bin/with-keepass ansible-playbook check-certificates.yaml --limit <target>
bin/with-keepass ansible-playbook bootstrap.yaml --limit <target> --tags stack --check --diff
bin/with-keepass ansible-playbook bootstrap.yaml --limit <target> --tags stack
```

The dry run reports `would restore infrastructure: pihole, portainer from snapshot <id>`. Before
`docker compose up`, the real run restores every item from the latest snapshot in a one-off resticprofile
container and writes the marker `/var/lib/restic-restore/infrastructure.done`. A new host without any snapshot
stops here with a hint; skip the restore then:

```sh
bin/with-keepass ansible-playbook bootstrap.yaml --limit <target> --tags stack -e skip_infrastructure_restore=true
```

### 6. Pi-hole

Puts the Pi-hole configuration from `configs/pihole.yaml` on top of the restored state:

```sh
bin/with-keepass ansible-playbook setup-pihole.yaml --limit <target>
```

A second stack run ends with `changed=0`, since the marker makes the restore a no-op:

```sh
bin/with-keepass ansible-playbook bootstrap.yaml --limit <target> --tags stack
```

### 7. smart-home (restores its volumes)

Deploy the smart-home stack, which restores its own volumes the same way (TheRealArthurDent/smart-home#1).
Backups wait for its marker too (`stack_backup_stacks`), so don't skip this. In `ansible/` of the smart-home repo:

```sh
ansible-playbook deploy.yaml --limit <target> --check --diff
ansible-playbook deploy.yaml --limit <target>
```

On a new host without a snapshot, add `-e skip_smart_home_restore=true`. Then check that resticprofile no longer
waits for a marker and has scheduled the backups:

```sh
ssh -p <port> <user>@<ip> docker logs resticprofile
```

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

### Clean slate restore

What steps 5 and 7 of [Set up a host](#set-up-a-host-new-install-or-after-a-crash) do on their own: before
`docker compose up`, the `stack` role runs `restic-restore --runner oneoff clean-slate infrastructure` (the
smart-home role the same for `smart-home`).

What the role decides:

- Marker present: nothing to do. Every later run is a no-op, so a deliberate second restore of the whole stack
  goes through the targeted restore, not by deleting the marker.
- No marker, but an item folder already holds files (a host that existed before #287, like raspi): it only
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
- The run stops with a hint if `dungeon` refuses the key (run `init-restic.sh`, then the playbook again) or the
  repository has no snapshot (a new host: run again with `skip_infrastructure_restore`).

Backups stay off until the restore is done: resticprofile schedules them only once every stack in
`stack_backup_stacks` (this stack and smart-home) has its marker, so a fresh host never backs up its empty or
half-restored state as the latest snapshot. `docker logs resticprofile` says which marker it waits for.

### Other stacks

resticprofile backs up the whole host, so the helper is shared. Every stack lists its items in
`/etc/restic-restore/stacks/<stack>.json` (`{"items": {name: {paths, containers, preserve}}}`), runs
`restic-restore --runner oneoff clean-slate <stack>` before it starts, and is added to `stack_backup_stacks` once
its deployment does. Its items then work with `stage <stack>/<item>` and `apply` like this stack's. The
smart-home stack does since TheRealArthurDent/smart-home#1, so `inventory/group_vars/all/restore_items.yaml` adds
it to `stack_backup_stacks`: backups also wait for `/var/lib/restic-restore/smart-home.done`.

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
missed drill would turn the healthcheck red. `sudo restic-restore drill` still works there by hand, without
recording a result.

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
covers `init-restic.sh` and the one-off container on a host without a running stack.

## Tests

See the [main README](../README.md#testing) for unit tests, Molecule and lint.
