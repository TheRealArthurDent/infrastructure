# Testing Framework Documentation

## Overview

This project uses a comprehensive testing framework to ensure Ansible playbook quality and correctness. The framework consists of three layers:

1. **Syntax & Lint Validation** - Code quality checks
2. **Integration Testing** - Functional validation in isolated environments
3. **CI/CD Automation** - Automated testing on push/pull requests

## Architecture

### Layer 1: Syntax & Lint (ansible-lint)

- **Tool**: [ansible-lint](https://ansible-lint.readthedocs.io/)
- **Purpose**: Validates Ansible YAML syntax and enforces best practices
- **Configuration**: `.ansible-lint`
- **Pre-commit**: Automatically runs before commits (if configured)
- **CI**: Runs on every push/PR via GitHub Actions

**Run locally:**
```bash
ansible-lint --strict bootstrap/
```

### Layer 2: Integration Testing (Molecule + Docker)

- **Tool**: [Molecule](https://molecule.readthedocs.io/)
- **Driver**: Docker (lightweight, fast, no VMs needed)
- **Purpose**: Tests playbook functionality in realistic environments
- **Scenarios**: 
  - `debian-13`: Tests on Debian Bookworm (primary target)
  
**Key files:**
- `molecule/debian-13/molecule.yml` - Scenario configuration
- `molecule/debian-13/converge.yml` - Applies the playbook
- `molecule/debian-13/verify.yml` - Post-run validation
- `molecule/debian-13/prepare.yml` - Test environment setup
- `molecule/debian-13/Dockerfile` - Custom test image

**Run locally:**
```bash
# From the project root, molecule will find the default scenario
molecule test                       # Full test cycle (uses default -> debian-13)
molecule create                     # Create containers only
molecule converge                   # Apply playbook
molecule verify                     # Post-run validation
molecule destroy                    # Clean up

# Or explicitly specify the scenario
molecule test -s debian-13          # Full test cycle for debian-13
molecule converge -s debian-13      # Apply playbook to debian-13
```

### Layer 3: CI/CD Automation (GitHub Actions)

- **Linting Workflow**: `.github/workflows/ansible-lint.yaml`
  - Triggers: Push to main/develop, PRs
  - Paths: Changes to `bootstrap/`, ansible config
  - Action: Runs ansible-lint in strict mode

- **Molecule Workflow**: `.github/workflows/molecule.yaml`
  - Triggers: Push to main/develop, PRs
  - Paths: Changes to `bootstrap/`, `molecule/`, requirements
  - Action: Runs `molecule test` for each scenario

## Testing Workflow

### Local Development

1. **Install dependencies:**
   ```bash
   make install
   # or
   pip install -r requirements.txt
   ```

2. **Create/update Ansible code** in `bootstrap/`

3. **Run linting (optional):**
   ```bash
   make lint
   # or
   ansible-lint --strict bootstrap/
   ```

4. **Run integration tests:**
   ```bash
   make test
   # or
   molecule test                      # Uses default scenario (debian-13)
   molecule test -s debian-13         # Explicitly specify scenario
   ```

5. **Commit and push** - GitHub Actions will run full test suite automatically

### Continuous Integration

When you push to `main` or `develop` (or open a PR):

1. ✅ **Ansible Lint** runs immediately (fast, ~10 seconds)
2. ✅ **Molecule Tests** run in parallel with linting (slower, ~2-3 minutes)
3. ✅ Both must pass before merging to main

## Understanding Molecule Phases

Molecule follows a specific test sequence:

```
dependency  → Install Galaxy requirements
cleanup     → Remove previous test artifacts
destroy     → Remove containers from previous runs
syntax      → Check Ansible syntax
create      → Build and start Docker containers
prepare     → Set up test environment (prepare.yml)
converge    → Apply the playbook (converge.yml)
idempotence → Apply again, verify no changes
side_effect → (Optional) Run side effect tests
verify      → Validate playbook results (verify.yml)
cleanup     → Remove test artifacts
destroy     → Remove containers
```

## Customizing Tests

### Adding a New Scenario (e.g., Debian 12)

1. **Copy existing scenario:**
   ```bash
   cp -r molecule/debian-13 molecule/debian-12
   ```

2. **Update `molecule.yml`:**
   - Change `scenario.name: debian-12`
   - Change `driver.platforms[0].name: test-debian-12`
   - Change `driver.platforms[0].image: debian:bookworm` → `debian:bookworm-slim` or similar

3. **Update CI workflow** (`.github/workflows/molecule.yaml`):
   ```yaml
   matrix:
     scenario:
       - debian-13
       - debian-12  # Add this line
   ```

### Adding Verification Tasks

Edit `molecule/debian-13/verify.yml` to add more test assertions:

```yaml
- name: Verify specific package
  apt:
    name: nginx
    state: present
  register: nginx_package

- name: Assert nginx was installed
  assert:
    that:
      - "'nginx' in ansible_facts.packages"
    fail_msg: "Nginx is not installed"
```

### Debugging Failed Tests

1. **Enable verbose output:**
   ```bash
   molecule test -s debian-13 -v
   ```

2. **Keep containers after failure** for debugging:
   ```bash
   molecule create -s debian-13
   molecule converge -s debian-13
   # Container still running, debug here...
   molecule destroy -s debian-13
   ```

3. **Shell into container:**
   ```bash
   docker exec -it test-debian-13 /bin/bash
   ```

4. **Check Ansible logs:**
   ```bash
   cd bootstrap
   ansible-playbook playbooks/bootstrap.yaml -i molecule/debian-13/inventory.yml -vv
   ```

## Best Practices

### Ansible Playbook

- ✅ Use meaningful task names (helps with debugging)
- ✅ Use modules instead of raw commands where possible
- ✅ Handle errors gracefully with `ignore_errors` or `failed_when`
- ✅ Use idempotent operations (safe to run multiple times)
- ❌ Avoid hardcoding values - use variables instead
- ❌ Don't ignore lint warnings - fix them!

### Molecule Tests

- ✅ Verify both happy path and edge cases
- ✅ Test as close to production as possible
- ✅ Clean up after tests (Docker images, containers)
- ✅ Keep test scenarios focused and independent
- ❌ Don't skip verification steps
- ❌ Don't use `ignore_errors: yes` in tests

### CI/CD

- ✅ Run linting before integration tests (fail fast)
- ✅ Test multiple scenarios/versions (matrix)
- ✅ Cache dependencies (pip, Docker layers)
- ✅ Use meaningful job names and step descriptions
- ❌ Don't skip checks for "quick fixes"
- ❌ Don't commit without passing tests

## Troubleshooting

### "Docker daemon is not running"
```bash
# On Linux:
sudo systemctl start docker

# On Mac/Windows with Docker Desktop:
# Open Docker Desktop application
```

### "Permission denied" when running Molecule
```bash
# Add your user to docker group:
sudo usermod -aG docker $USER
# Then log out and back in, or:
newgrp docker
```

### "Module not found" errors in tests
```bash
# Ensure Galaxy dependencies are installed:
cd bootstrap
ansible-galaxy install -r requirements.yml
```

### Tests pass locally but fail in CI
- Check Python version matches CI (3.12)
- Ensure all dependencies in `requirements.txt`
- Test on same OS as CI (Ubuntu 22.04 LTS)
- Check for hardcoded paths/hostnames

### Molecule hangs during test
```bash
# Kill it:
Ctrl+C

# Clean up:
molecule destroy -s debian-13
```

## Advanced Topics

### Custom Test Image (Dockerfile)

The `molecule/debian-13/Dockerfile` builds a custom test image with:
- Systemd support (for testing service management)
- Python 3 and necessary tools
- Test user with sudo privileges
- Proper signal handling

Customize for specific needs:
```dockerfile
RUN apt-get update && apt-get install -y \
    vim \
    curl \
    git
```

### Molecule Plugins

You can extend Molecule with plugins:

```yaml
provisioner:
  name: ansible
  plugins:
    callback:
      - name: profile_roles
```

### Performance Optimization

1. **Reuse containers between test runs:**
   ```bash
   # Keep containers for faster iteration
   molecule create -s debian-13
   molecule converge -s debian-13
   # Edit playbook...
   molecule converge -s debian-13  # Reuse container
   ```

2. **Parallel testing** (GitHub Actions only):
   ```yaml
   strategy:
     max-parallel: 2  # Run tests in parallel
   ```

## Resources

- [Ansible Documentation](https://docs.ansible.com/)
- [Ansible Lint Rules](https://ansible-lint.readthedocs.io/rules/)
- [Molecule Documentation](https://molecule.readthedocs.io/)
- [Molecule Docker Driver](https://molecule.readthedocs.io/configuration/#driver)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)

## Support

For issues or questions:
1. Check Ansible Lint rules: `ansible-lint -vv bootstrap/`
2. Enable verbose Molecule output: `molecule test -v`
3. Review logs in GitHub Actions: Check workflow runs in repository
