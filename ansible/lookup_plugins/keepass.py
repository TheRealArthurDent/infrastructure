from ansible.errors import AnsibleError
from ansible.plugins.lookup import LookupBase
from pykeepass import PyKeePass
import hashlib
import os

# Opened databases, so the key derivation runs once per process instead of on every lookup (raspberry#235).
# Ansible creates a new LookupModule for each lookup call, so this lives at module level.
# The key covers the file's state and the credentials: a changed file or a wrong password opens anew.
_databases = {}


def _open(db, password, key):
  stat = os.stat(db)
  cache_key = (
    os.path.realpath(db), stat.st_mtime_ns, stat.st_size,
    os.path.realpath(key) if key else None,
    hashlib.sha256((password or '').encode()).hexdigest(),
  )
  if cache_key not in _databases:
    _databases[cache_key] = PyKeePass(db, password=password, keyfile=key)
  return _databases[cache_key]


def _find_group(kp, group_name):
  """Find the one group whose path ends with group_name ("a/b" matches .../a/b)."""
  segments = group_name.split('/')
  matches = [g for g in kp.groups if g.path[-len(segments):] == segments]
  if not matches:
    raise AnsibleError(f'Group "{group_name}" not found.')
  if len(matches) > 1:
    paths = ['/'.join(g.path) for g in matches]
    raise AnsibleError(f'Group "{group_name}" is ambiguous, use the full path: {paths}')
  return matches[0]


class LookupModule(LookupBase):

  def run(self, terms, variables=None, **kwargs):
    db = kwargs.get("db")
    password = kwargs.get("password")
    key = kwargs.get("key")

    kp = _open(db, password, key)

    group_name = kwargs.get('group')
    title = kwargs.get('title')
    field = kwargs.get('field')
    attachment_name = kwargs.get('attachment')
    output_path = kwargs.get('output_path')

    group = _find_group(kp, group_name) if group_name else kp.root_group
    entries = kp.find_entries(title=title, group=group)

    if not entries:
      raise AnsibleError(f'Entry "{group_name}/{title}" not found.')
    if len(entries) > 1:
      paths = ['/'.join(e.path) for e in entries]
      raise AnsibleError(f'Entry "{group_name}/{title}" is ambiguous: {paths}')
    entry = entries[0]

    # If just querying attachments, list them
    if attachment_name and attachment_name == 'list':
      if entry.attachments:
        att_names = [f"{a.filename} ({len(a.data)} bytes)" for a in entry.attachments]
        return att_names
      else:
        return ["No attachments found"]

    # Handle attachment export
    if attachment_name:
      if not entry.attachments:
        raise AnsibleError(f'No attachments found in entry "{group_name}/{title}".')

      attachment = None
      for att in entry.attachments:
        if att.filename == attachment_name:
          attachment = att
          break

      if not attachment:
        raise AnsibleError(f'Attachment "{attachment_name}" not found in entry "{group_name}/{title}". Available: {[a.filename for a in entry.attachments]}')

      # Without output_path, return the content so secrets never touch the disk (raspberry#229)
      if not output_path:
        return [attachment.data.decode('utf-8')]

      # Create directory if it doesn't exist
      output_dir = os.path.dirname(output_path)
      if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, mode=0o700)

      # Write attachment to file
      try:
        with open(output_path, 'wb') as f:
          # Try different attributes
          data = None
          if hasattr(attachment, 'data'):
            data = attachment.data
          elif hasattr(attachment, 'content'):
            data = attachment.content
          else:
            # Maybe it's the binary stream itself
            data = bytes(attachment)

          if data is None:
            raise AnsibleError(f'Could not extract data from attachment "{attachment_name}"')

          f.write(data)
      except Exception as e:
        raise AnsibleError(f'Failed to write attachment to {output_path}: {str(e)}')

      # Return the path where file was written
      results = [output_path]
    else:
      # Original field extraction logic
      results = []
      if field is None or field == 'password':
        results.append(entry.password)
      elif field == 'username' or field == 'user':
        results.append(entry.username)
      else:
        # Custom field
        custom_value = entry.custom_properties.get(field, '')
        results.append(custom_value)

    return results
