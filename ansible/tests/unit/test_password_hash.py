"""password_hash on the control node (the secrets role's metrics.htpasswd, #39).

ansible-core hashes through the system's libcrypt when it finds one, and through passlib otherwise. A control node
without a libcrypt ansible-core can load must still hash, so passlib is in requirements.txt.
"""
import subprocess

import pytest
from ansible.plugins.filter.core import get_encrypted_password
from ansible.utils import encrypt

SALT = "abcdefgh12345678"


@pytest.fixture
def without_libcrypt(monkeypatch):
    monkeypatch.setattr(encrypt, "HAS_CRYPT", False)


def test_the_writers_hash_needs_no_libcrypt(without_libcrypt):
    hashed = get_encrypted_password("writer $x", "sha512", salt=SALT, rounds=5000)
    assert hashed.startswith(f"$6${SALT}$")


def test_passlib_hashes_as_crypt_does(without_libcrypt):
    """nginx checks the hash with crypt(); openssl passwd -6 computes the same."""
    expected = subprocess.run(["openssl", "passwd", "-6", "-salt", SALT, "-stdin"], input="writer $x",
                              capture_output=True, text=True, check=True).stdout.strip()
    assert get_encrypted_password("writer $x", "sha512", salt=SALT, rounds=5000) == expected
