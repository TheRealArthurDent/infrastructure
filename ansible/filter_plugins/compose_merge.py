"""Filter compose_merge: merge the Docker Compose fragments of the stack's components into one definition."""
from ansible.errors import AnsibleFilterError

# Top-level keys whose entries are merged by name; any other top-level key is rejected.
MERGED_KEYS = ('services', 'volumes', 'networks', 'secrets')


def compose_merge(fragments):
  merged = {}
  origin = {}  # (section, name) -> index of the fragment that defined it
  for index, fragment in enumerate(fragments):
    for section, entries in fragment.items():
      if section not in MERGED_KEYS:
        raise AnsibleFilterError(
          f'compose_merge: unsupported top-level key "{section}" in fragment {index} '
          f'(allowed: {", ".join(MERGED_KEYS)})')
      target = merged.setdefault(section, {})
      for name, definition in (entries or {}).items():
        if name in target:
          raise AnsibleFilterError(
            f'compose_merge: {section} entry "{name}" is defined in fragment '
            f'{origin[(section, name)]} and in fragment {index}')
        target[name] = definition
        origin[(section, name)] = index
  return merged


class FilterModule:
  def filters(self):
    return {'compose_merge': compose_merge}
