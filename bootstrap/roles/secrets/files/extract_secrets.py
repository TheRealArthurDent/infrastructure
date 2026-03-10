#!/usr/bin/env python3
import json
import sys
from pykeepass import PyKeePass

def extract_secrets(keepass_file, keepass_password, secrets_config):
    """Extract secrets from keepass file"""
    try:
        kp = PyKeePass(keepass_file, password=keepass_password)
        secrets = {}

        for file_config in secrets_config:
            for entry in file_config['entries']:
                keepass_path = entry['keepass_path']
                field = entry['field']

                keepass_entry = kp.find_entries_by_path(keepass_path)
                if keepass_entry:
                    ke = keepass_entry[0]
                    key = f"{keepass_path}_{field}"

                    if key not in secrets:
                        if field == 'password':
                            secrets[key] = ke.password
                        elif field == 'username':
                            secrets[key] = ke.username
                        else:
                            # Custom field
                            custom_value = ke.custom_properties.get(field, '')
                            secrets[key] = custom_value

        return secrets
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    keepass_file = sys.argv[1]
    keepass_password = sys.argv[2]
    secrets_config_json = sys.argv[3]

    secrets_config = json.loads(secrets_config_json)
    secrets = extract_secrets(keepass_file, keepass_password, secrets_config)

    print(json.dumps(secrets))
