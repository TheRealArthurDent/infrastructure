from ansible.errors import AnsibleError
from ansible.plugins.lookup import LookupBase
from pykeepass import PyKeePass
import os

class LookupModule(LookupBase):

  def run(self, terms, variables=None, **kwargs):
    db = kwargs.get("db")
    password = kwargs.get("password")
    key = kwargs.get("key")

    kp = PyKeePass(db, password=password, keyfile=key)

    group_name = kwargs.get('group')
    title = kwargs.get('title')
    field = kwargs.get('field')
    attachment_name = kwargs.get('attachment')
    output_path = kwargs.get('output_path')

    group = kp.find_groups(name=group_name, first=True)
    entry = kp.find_entries(title=title, group=group, first=True)

    if entry is None:
      raise AnsibleError(f'Entry "{group_name}/{title}" not found.')

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

      # Without output_path, return the content so secrets never touch the disk (#229)
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
