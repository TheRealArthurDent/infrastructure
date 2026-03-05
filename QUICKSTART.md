# Quick Start - Testing Framework

## One-time Setup

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. (Optional) Install pre-commit hooks
pre-commit install
```

## Common Commands

```bash
# Run full test suite (lint + molecule tests)
make test

# Just run linting
make lint

# Run molecule tests only
make test

# Create test containers without running playbook
make molecule-create

# Apply playbook to containers
make molecule-converge

# Verify playbook results
make molecule-verify

# Destroy containers
make molecule-destroy

# Cleanup all artifacts
make clean

# Run pre-commit hooks manually
make pre-commit

# Molecule commands (direct - uses default scenario)
molecule check                    # Syntax check
molecule test                     # Full test suite
molecule test -s debian-13        # Test specific scenario
molecule list                     # List available scenarios
```

## Typical Workflow

1. **Create feature branch:**
   ```bash
   git checkout -b my-feature
   ```

2. **Make changes to `bootstrap/` playbook or roles**

3. **Test locally:**
   ```bash
   make test          # Full test suite
   ```

4. **If tests fail:**
   ```bash
   ansible-lint --strict bootstrap/    # See what linting issues exist
   cd bootstrap && molecule test -v    # Verbose molecule output
   ```

5. **Commit and push:**
   ```bash
   git commit -am "Add new feature"
   git push origin my-feature
   ```

6. **GitHub Actions will automatically test your PR** ✅

## Testing Troubleshooting

**"molecule: command not found"**
```bash
pip install -r requirements.txt
```

**"Docker daemon is not running"**
```bash
# Ensure Docker Desktop is running (Mac/Windows)
# Or start Docker service on Linux: sudo systemctl start docker
```

**Tests pass locally but fail in CI**
- Check you're using Python 3.12: `python --version`
- Ensure dependencies match: `pip install -r requirements.txt`

**Want to keep debugging/iterating?**
```bash
cd bootstrap
molecule create -s debian-13    # Create container
molecule converge -s debian-13  # Apply playbook
# Edit files and re-run...
molecule converge -s debian-13  # Reapply (reuses container)
molecule verify -s debian-13    # Check results
molecule destroy -s debian-13   # Cleanup when done
```

## File Structure

```
bootstrap/
  playbooks/bootstrap.yaml     ← Main playbook (target the roles)
  roles/
    base/tasks/main.yaml       ← System updates
    docker/tasks/main.yaml     ← Docker installation
  inventory/hosts.yaml         ← Host definitions
  requirements.yml             ← Galaxy dependencies (optional)

molecule/debian-13/            ← Test scenario
  molecule.yml                 ← Molecule config
  converge.yml                 ← Playbook application
  prepare.yml                  ← Test environment setup
  verify.yml                   ← Validation checks
  Dockerfile                   ← Custom test image
```

## Next Steps

- **Add more roles:** Create new directories in `bootstrap/roles/`
- **Add more test scenarios:** Copy `molecule/debian-13` → `molecule/debian-12`, etc.
- **Customize verify tasks:** Edit `molecule/debian-13/verify.yml` to add more checks
- **Read full docs:** See `TESTING.md` for comprehensive documentation
