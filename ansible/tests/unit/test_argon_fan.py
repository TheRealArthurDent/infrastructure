"""The fan daemon of the argon_fan role (#325): Argon ONE V2 case fan, driven over I²C like Argon's own argononed."""

import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
DAEMON = ANSIBLE_DIR / "roles" / "argon_fan" / "files" / "argon-fan"
DEFAULTS = ANSIBLE_DIR / "roles" / "argon_fan" / "defaults" / "main.yaml"
# What raspi's /etc/argononed.conf held when the role replaced Argon's script (Argon's defaults)
RASPI_THRESHOLDS = {55: 10, 60: 55, 65: 100}


def load_daemon():
    loader = importlib.machinery.SourceFileLoader("argon_fan", str(DAEMON))
    spec = importlib.util.spec_from_loader("argon_fan", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def fan():
    return load_daemon()


class FakeBus:
    """The case's controller at 0x1a. With register support, the duty cycle register 0x80 keeps what was written."""

    def __init__(self, registers=True):
        self.registers = registers
        self.duty_cycle = 0
        self.writes = []

    def read_byte_data(self, address, register):
        assert (address, register) == (0x1a, 0x80)
        return self.duty_cycle if self.registers else 0

    def write_byte_data(self, address, register, value):
        assert (address, register) == (0x1a, 0x80)
        self.writes.append(("register", value))
        if self.registers:
            self.duty_cycle = value

    def write_byte(self, address, value):
        assert address == 0x1a
        self.writes.append(("byte", value))


THRESHOLDS = [(55, 10), (60, 55), (65, 100)]


@pytest.mark.parametrize("temperature, speed", [
    (20.0, 0), (54.9, 0), (55.0, 10), (59.9, 10), (60.0, 55), (64.9, 55), (65.0, 100), (85.0, 100),
])
def test_speed_follows_the_highest_threshold_reached(fan, temperature, speed):
    assert fan.target_speed(temperature, THRESHOLDS) == speed


def test_thresholds_need_not_be_sorted(fan):
    assert fan.target_speed(61.0, [(65, 100), (55, 10), (60, 55)]) == 55


def test_reads_the_temperature_in_millidegrees(fan, tmp_path):
    temp = tmp_path / "temp"
    temp.write_text("57312\n")
    assert fan.read_temperature(temp) == pytest.approx(57.312)


def test_sets_the_duty_cycle_register_when_the_firmware_has_it(fan):
    bus = FakeBus(registers=True)
    controller = fan.FanController(bus)

    controller.set_speed(55)

    assert bus.duty_cycle == 55
    assert bus.writes[-1] == ("register", 55)
    assert ("byte", 55) not in bus.writes


def test_falls_back_to_a_plain_byte_on_old_firmware(fan):
    bus = FakeBus(registers=False)
    controller = fan.FanController(bus)

    controller.set_speed(55)

    assert bus.writes[-1] == ("byte", 55)


def test_clamps_the_speed(fan):
    bus = FakeBus()
    controller = fan.FanController(bus)
    controller.set_speed(150)
    assert bus.duty_cycle == 100
    controller.set_speed(-5)
    assert bus.duty_cycle == 0


def test_speeds_up_at_once_and_slows_down_one_check_later(fan):
    # Like argononed: a lower speed waits for the next check, so the fan doesn't flap around a threshold
    bus = FakeBus()
    daemon = fan.Daemon(fan.FanController(bus), THRESHOLDS)

    assert daemon.check(61.0) == 55
    assert daemon.check(66.0) == 100
    assert daemon.check(58.0) == 100
    assert daemon.check(58.0) == 10
    assert daemon.check(40.0) == 10
    assert daemon.check(61.0) == 55
    assert bus.duty_cycle == 55


def test_writes_only_when_the_speed_changes(fan):
    bus = FakeBus()
    daemon = fan.Daemon(fan.FanController(bus), THRESHOLDS)
    daemon.check(61.0)
    writes = len(bus.writes)

    daemon.check(62.0)
    daemon.check(63.0)

    assert len(bus.writes) == writes


def test_reads_thresholds_from_the_config(fan, tmp_path):
    config = tmp_path / "argon-fan.json"
    config.write_text(json.dumps({"thresholds": {"55": 10, "60": 55}, "interval": 30, "bus": 1,
                                  "temperature_file": "/sys/class/thermal/thermal_zone0/temp"}))
    loaded = fan.load_config(config)
    assert loaded["thresholds"] == [(55.0, 10), (60.0, 55)]
    assert loaded["interval"] == 30


def test_role_defaults_keep_raspis_thresholds():
    defaults = yaml.safe_load(DEFAULTS.read_text())
    assert {int(k): v for k, v in defaults["argon_fan_thresholds"].items()} == RASPI_THRESHOLDS


def test_check_mode_doesnt_start_a_service_whose_unit_isnt_written_yet():
    # In check mode the unit file isn't written, so on a host without it systemd can't find the service (raspi did)
    tasks = yaml.safe_load((ANSIBLE_DIR / "roles" / "argon_fan" / "tasks" / "main.yaml").read_text())
    unit = next(t for t in tasks if "argon-fan.service" in str(t.get("ansible.builtin.template", {}).get("dest")))
    block = next(t for t in tasks if "block" in t)
    start = next(t for t in block["block"] if (t.get("ansible.builtin.systemd_service") or {}).get("name") == "argon-fan")
    assert unit.get("register"), "the unit file task isn't registered"
    condition = str(start.get("when", ""))
    assert "ansible_check_mode" in condition and f"{unit['register']} is changed" in condition


def test_check_mode_doesnt_restart_a_service_whose_unit_isnt_written_yet():
    handlers = yaml.safe_load((ANSIBLE_DIR / "roles" / "argon_fan" / "handlers" / "main.yaml").read_text())
    restart = next(h for h in handlers if (h.get("ansible.builtin.systemd_service") or {}).get("name") == "argon-fan")
    condition = str(restart.get("when", ""))
    assert "ansible_check_mode" in condition and "argon_fan_unit is changed" in condition
