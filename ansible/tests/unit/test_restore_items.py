"""Every restore_items path is in the backup, and the files a restore must keep are known (#285).

restore_items lists the state that doesn't come from IaC and gets restored from the resticprofile backup. A
path the backup doesn't cover (no /source mount, or matched by an exclude pattern) can't be restored, and a
file inside an item that the backup excludes would be deleted by a restore that mirrors the snapshot, unless
the item lists it under preserve.
"""
import fnmatch
from pathlib import PurePosixPath

import pytest
import yaml

ANSIBLE_DIR = PurePosixPath(__file__).parents[2]
RESTORE_ITEMS = ANSIBLE_DIR / "inventory" / "group_vars" / "all" / "restore_items.yaml"
TEMPLATES = ANSIBLE_DIR / "roles" / "stack" / "templates"
EXCLUDES = TEMPLATES / "resticprofile" / "config" / "excludes.j2"
COMPOSE = TEMPLATES / "docker-compose.yaml.j2"


def read(path):
    with open(path) as f:
        return f.read()


def restore_items():
    return yaml.safe_load(read(RESTORE_ITEMS))["restore_items"]


def exclude_patterns():
    lines = (line.strip() for line in read(EXCLUDES).splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def source_mounts():
    """Host path -> path inside the resticprofile container, for the mounts under /source/."""
    service = yaml.safe_load(read(COMPOSE))["services"]["resticprofile"]
    mounts = {}
    for volume in service["volumes"]:
        host, container = volume.split(":")[:2]
        if container.startswith("/source/"):
            mounts[host] = container
    return mounts


def to_source(host_path):
    """Where restic sees host_path, or None if no mount covers it."""
    path = PurePosixPath(host_path)
    for host, container in sorted(source_mounts().items(), key=lambda m: -len(m[0])):
        if path == PurePosixPath(host) or PurePosixPath(host) in path.parents:
            return PurePosixPath(container) / path.relative_to(host)
    return None


def match(pattern, parts):
    """restic's pattern match: components are globs, ** matches any number of components."""
    if not pattern:
        return not parts
    if pattern[0] == "**":
        return any(match(pattern[1:], parts[i:]) for i in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatchcase(parts[0], pattern[0]) and match(pattern[1:], parts[1:])


def split(pattern):
    # Like restic, a pattern without a leading / may match anywhere in the path.
    if pattern.startswith("/"):
        return pattern[1:].split("/")
    return ["**", *pattern.split("/")]


def excluded(path):
    """True if restic skips path: the pattern matches it or one of its parent directories."""
    parts = PurePosixPath(path).parts[1:]
    return any(match(split(p), parts[:n]) for p in exclude_patterns() for n in range(1, len(parts) + 1))


def literal_prefix(pattern):
    """The leading components of an anchored pattern that contain no wildcard."""
    prefix = []
    for part in pattern[1:].split("/"):
        if any(c in part for c in "*?["):
            break
        prefix.append(part)
    return PurePosixPath("/", *prefix)


ITEMS = restore_items()


def test_restore_items_are_defined():
    assert set(ITEMS) >= {"portainer", "pihole"}


def test_outbound_is_not_restored():
    """setup-pihole.yaml provisions outbound, so its state comes from Ansible, not the backup (#293)."""
    assert "pihole_outbound" not in ITEMS


def test_outbound_keeps_its_runtime_data_in_a_named_volume():
    compose = yaml.safe_load(read(COMPOSE))

    assert compose["services"]["outbound"]["volumes"] == ["outbound_pihole:/etc/pihole"]
    assert compose["volumes"]["outbound_pihole"] == {"name": "outbound_pihole"}


def test_cache_volumes_are_not_backed_up():
    """A volume that is only a cache would just bloat the backup; losing it costs one Ansible run (#293)."""
    assert excluded(to_source("/var/lib/docker/volumes/outbound_pihole/_data/gravity.db"))


@pytest.mark.parametrize("name", sorted(ITEMS))
def test_item_paths_are_backed_up(name):
    for path in ITEMS[name]["paths"]:
        source = to_source(path)
        assert source is not None, f"{path} is not mounted into resticprofile under /source/"
        assert not excluded(source), f"{path} is matched by an exclude pattern in excludes.j2"


@pytest.mark.parametrize("name", sorted(ITEMS))
def test_preserved_files_are_excluded_from_the_backup(name):
    item = ITEMS[name]
    for pattern in (item.get("preserve") or []):
        assert any(excluded(to_source(path) / pattern.replace("*", "")) for path in item["paths"]), (
            f"{name}: preserve entry {pattern} is not excluded from the backup, so it does not belong under preserve"
        )


@pytest.mark.parametrize("name", sorted(ITEMS))
def test_excludes_inside_an_item_are_preserved(name):
    item = ITEMS[name]
    for path in item["paths"]:
        source = to_source(path)
        for pattern in exclude_patterns():
            prefix = literal_prefix(pattern) if pattern.startswith("/") else None
            if prefix is None or not (prefix == source or source in prefix.parents):
                continue
            relative = str(PurePosixPath(pattern).relative_to(source))
            assert relative in (item.get("preserve") or []), (
                f"{name}: {pattern} excludes files inside {path}; list {relative} under preserve"
            )


def test_matcher_follows_restic():
    assert excluded(PurePosixPath("/source/etc/ssl/private/rootCA.key"))
    assert excluded(PurePosixPath("/source/home/pi/.ssh/id_ed25519"))
    assert not excluded(PurePosixPath("/source/home/pi/.ssh/known_hosts"))
    assert excluded(PurePosixPath("/source/etc/docker/infrastructure/.env"))
    assert not excluded(PurePosixPath("/source/etc/docker/infrastructure/pihole"))


def test_backups_wait_for_the_smart_home_restore_too():
    """smart-home restores its volumes with clean-slate before it starts (smart-home#1), and both hosts run it.

    resticprofile schedules backups only once every stack in stack_backup_stacks has its marker.
    """
    group_vars = yaml.safe_load(read(RESTORE_ITEMS))
    assert group_vars["stack_backup_stacks"] == ["{{ stack_project_name }}", "smart-home"]
