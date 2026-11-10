"""dotenv_quote: a value for a .env file that Docker Compose reads back unchanged.

Compose expands $VAR and ${VAR} in unquoted and double-quoted .env values, cuts an unquoted value at " #", trims
its blanks and strips quotes around it, so a KeePass password like tes$abc$bla{xyz}blub reached the container as
tes{xyz}blub. Single quotes can't hold a ' or a trailing backslash. In double quotes, Compose unescapes \\ and \"
and turns $$ into $, so escaping those three makes every printable value safe, for env_file: and for the project's
.env alike (tests/unit/test_dotenv_quote.py checks this against docker compose itself).

Control characters (newlines above all) are refused: they can't be part of a single .env line, and none of the
consumers expects them in a password. The error message never contains the value.
"""
from ansible.errors import AnsibleFilterError


def dotenv_quote(value):
    """Return value as a double-quoted .env value, escaped for Docker Compose."""
    text = str(value)
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
        raise AnsibleFilterError("dotenv_quote: the value contains a control character (e.g. a line break)")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$") + '"'


class FilterModule:  # pylint: disable=too-few-public-methods
    def filters(self):
        return {"dotenv_quote": dotenv_quote}
