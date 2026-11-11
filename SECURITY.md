# Security policy

This repository is part of a private home lab. It is public so others can learn from it, not as a supported
product, but reports are welcome.

## Reporting a vulnerability

Please **do not open a public issue or pull request** for a security problem. Report it privately instead:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability** (GitHub's private vulnerability reporting).

Describe what you found and, if you can, how to reproduce it. Only the maintainer sees the report. There is no
fixed response time; this is a spare-time project.

## Scope

In scope: the code and configuration in this repository, for example Ansible roles, templates, Compose files, and
scripts that could leak secrets or weaken the hosts they set up.

Out of scope: vulnerabilities in the upstream software this repository deploys (Docker images, Debian packages,
Ansible collections). Please report those to their projects.

## Secrets

The repository contains no real secrets. Values containing `LEAKCANARY` are dummies for the tests. If you still find
something that looks like a real credential, please report it as described above.
