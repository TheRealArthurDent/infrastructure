"""Filter compose_merge: merge the Docker Compose fragments of the stack's components into one definition."""
from ansible.errors import AnsibleFilterError

# Top-level keys whose entries are merged by name; any other top-level key is rejected.
MERGED_KEYS = ('services', 'volumes', 'networks', 'secrets')


def compose_merge(fragments):
  merged = {}
  origin = {}  # (section, name) -> index of the fragment that defined it
  for index, fragment in enumerate(fragments):
    if fragment is None:  # a template that rendered empty
      continue
    if not isinstance(fragment, dict):
      raise AnsibleFilterError(
        f'compose_merge: fragment {index} is a {type(fragment).__name__}, expected a mapping')
    for section, entries in fragment.items():
      if section not in MERGED_KEYS:
        raise AnsibleFilterError(
          f'compose_merge: unsupported top-level key "{section}" in fragment {index} '
          f'(allowed: {", ".join(MERGED_KEYS)})')
      if entries is not None and not isinstance(entries, dict):
        raise AnsibleFilterError(
          f'compose_merge: section "{section}" in fragment {index} is a {type(entries).__name__}, '
          f'expected a mapping')
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
