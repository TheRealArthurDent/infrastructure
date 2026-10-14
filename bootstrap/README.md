# Ansible

Ansible sets up the Pi host: SSH port and hardening, sudoers, base packages, rsnapshot, Docker, secret `.env` files,
the nginx certificate and the Pi-hole DNS configuration. The Docker stack itself is still
deployed with `deploy.sh` from the repo root (see the [main README](../README.md) and `CLAUDE.md`).

| Playbook | What it does |
|---|---|
| `bootstrap.yaml` | Roles `ssh`, `base`, `docker`, `secrets`: SSH port and key-only login, apt dist-upgrade, packages, sysctl, sudoers, rsnapshot cron, Docker, `.env` files under `/etc/docker/` |
| `check-certificates.yaml` | Copies the root CA from KeePass to the host, renews `/etc/docker/infrastructure/nginx/<host>.pem` when it expires within 30 days, restarts nginx |
| `setup-pihole.yaml` | Syncs A records, CNAME records and block lists from `configs/pihole.yaml` through the Pi-hole REST API |

## Hosts

Defined in `inventory/hosts.yaml`:

| Host | IP | SSH port | User |
|---|---|---|---|
| `raspi` | 192.168.1.2 | 1357 | `smarthome` (production) |
| `testbed` | 192.168.1.7 | 4242 | `testuser` |

> All playbooks target `hosts: all` and refuse to run without `--limit <host>`, so a forgotten `--limit`
> never reaches `raspi` by accident.

## Control node setup (once)

Run everything from `bootstrap/`, so that `ansible.cfg` and the inventory are picked up.

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # in the repo root
source .venv/bin/activate
cd bootstrap
ansible-galaxy collection install -r requirements.yaml
```

You also need:

- the SSH key `~/.ssh/id_ed25519` (its public key goes onto the Pi),
- the KeePass database and key file. Their default paths are in `inventory/group_vars/all.yaml`
  (`keepass_db`, `keepass_key`); set `KEEPASS_DB` / `KEEPASS_KEY` to use other paths.
  The master password is asked for at the start of each run.
  Groups in the lookup are paths (`group='smart-home/ioBroker'`); a group or entry that is missing or
  matches more than once fails the run instead of picking one.

## Daily business

```sh
# Check a host is reachable
ansible testbed -m ping

# Dry run first, always
ansible-playbook bootstrap.yaml --limit testbed --check --diff

# Apply: OS updates (apt dist-upgrade), config, Docker, secrets
ansible-playbook bootstrap.yaml --limit testbed

# Check / renew the nginx certificate
ansible-playbook check-certificates.yaml --limit testbed

# Change DNS records or block lists: edit configs/pihole.yaml, then
ansible-playbook setup-pihole.yaml --limit testbed
# Pi-hole is reached at pihole.<host>; override with -e pihole_ip=192.168.1.7
# The KeePass password can come from the environment instead of the prompt:
KEEPASS_PASSWORD=... ansible-playbook setup-pihole.yaml --limit testbed

# Redeploy / update the Docker stack (from the repo root)
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
cd bootstrap
ansible-playbook bootstrap.yaml --limit testbed
```

The `ssh` role finds SSH on port 22, moves it to the port from the inventory and continues there. It also turns
off password and root logins, so make sure key login works first (step 2). If `sudo` asks
for a password on the fresh install, add `--ask-become-pass` to this first run.

Reboot afterwards so the Docker group membership and sysctl settings take effect:
`ssh -p 4242 testuser@192.168.1.7 sudo reboot`

### 4. Stack, certificate, DNS

```sh
./deploy.sh testbed 4242 testuser                           # from the repo root
ansible-playbook check-certificates.yaml --limit testbed    # from bootstrap/
ansible-playbook setup-pihole.yaml --limit testbed
```

### 5. Backups

On the Pi, authorise the restic key on the backup host `dungeon` once:

```sh
sudo /etc/docker/infrastructure/init-restic.sh
```

To restore data from the last backup, see `basic-setup/setup.txt` (step 5).

## Tests

See the [main README](../README.md#testing) for unit tests, Molecule and lint.
