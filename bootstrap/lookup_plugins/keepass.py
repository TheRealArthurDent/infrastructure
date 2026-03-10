from ansible.plugins.lookup import LookupBase
from pykeepass import PyKeePass

class LookupModule(LookupBase):

  def run(self, terms, variables=None, **kwargs):
    db = kwargs.get("db")
    password = kwargs.get("password")

    kp = PyKeePass(db, password=password)

    group_name = kwargs.get('group')
    title = kwargs.get('title')
    field = kwargs.get('field')

    group = kp.find_groups(name=group_name, first=True)
    #print(group)
    entry = kp.find_entries(title=title, group=group, first=True)
    #print(entry)
    #print(f'Field: {field}')
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
