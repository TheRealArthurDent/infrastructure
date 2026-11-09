# infrastructure

[![Spell Check](https://github.com/TheRealArthurDent/infrastructure/actions/workflows/spell-check.yaml/badge.svg)](https://github.com/TheRealArthurDent/infrastructure/actions/workflows/spell-check.yaml)
[![CI](https://github.com/TheRealArthurDent/infrastructure/actions/workflows/ci.yaml/badge.svg)](https://github.com/TheRealArthurDent/infrastructure/actions/workflows/ci.yaml)

Infrastructure of the smart home hosts. Everything is deployed per host from one inventory:

| Host | What it is |
| --- | --- |
| `raspi` | Raspberry Pi 4, production smart home |
| `testbed` | Test Pi for real integration runs |
| `dungeon` | Raspberry Pi 5, private cloud and backup target (SSD at `/media/backup`) |

Moved from the private `raspberry` repo on 2026-11-08; older issues and PRs (`raspberry#…`) live there.
Everything, from the host to the Docker stack, is deployed with Ansible; see [ansible/README.md](ansible/README.md).

## Testing

Set up the tooling once:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

| What | Command (from the repo root) | Needs |
|---|---|---|
| Unit tests for the KeePass lookup plugin | `.venv/bin/pytest ansible/tests/unit` | nothing |
| Role tests in a Debian trixie container (converge, idempotence, verify) | `(cd ansible && ../.venv/bin/molecule test)` | Docker |
| Real integration test on the testbed Pi | `ansible-playbook bootstrap.yaml --limit testbed --check --diff`, then a real run, then a second run that must report `changed=0` | testbed |

Molecule covers the `base`, `docker` and `secrets` roles. The `ssh` role (port switching) and the certificate role
are only tested on the testbed Pi.

### Developing a role with Molecule

`molecule test` builds a fresh container, runs everything and throws the container away. While working on a role,
keep the container around instead (all commands from `ansible/`):

```sh
molecule converge      # create the container if needed and apply the roles; repeat after every change
molecule idempotence   # run again and fail if anything changes
molecule verify        # run the checks in molecule/default/verify.yaml
molecule login         # open a shell in the container to look around
molecule destroy       # remove the container when done
```

### Reports

- Molecule prints a summary of all steps at the end of each run.
- JUnit XML reports are written to `test-results/` (gitignored):
  - `test-results/molecule/<playbook>-<timestamp>.xml`, one per playbook run
    (`create`, `converge`, `verify`, `destroy`).
    The idempotence step shows up as a second `converge` file; Molecule itself fails that step if anything changed.
  - `pytest ... --junitxml=test-results/pytest.xml` for the unit tests.
- In GitHub Actions, the reports are published as check runs ("Unit test report", "Molecule test report") with a
  summary on the run page, and the raw XML files are attached as the `molecule-reports` artifact.
  The workflow can also be started by hand: Actions → CI → Run workflow.
