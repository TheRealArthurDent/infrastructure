"""compose_env: quote a value for an env file that Docker Compose reads (env_file:, --env-file).

Compose interpolates unquoted values (`$XXX` becomes empty) and cuts them at ` #`. Inside double quotes it
unescapes `\\`, `"` and `$` and keeps everything else, so escaping those three gives back the value verbatim
(tests/unit/test_compose_env.py checks it against docker compose).
"""


def compose_env(value):
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{text}"'


class FilterModule:
    def filters(self):
        return {"compose_env": compose_env}
