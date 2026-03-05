.PHONY: help test lint molecule clean install

help:
	@echo "Available targets:"
	@echo "  make install          - Install Python dependencies"
	@echo "  make lint             - Run ansible-lint on bootstrap/"
	@echo "  make test             - Run full Molecule test suite"
	@echo "  make molecule-create  - Create test containers"
	@echo "  make molecule-converge- Apply playbook to test containers"
	@echo "  make molecule-verify  - Run verification tasks"
	@echo "  make molecule-destroy - Clean up test containers"
	@echo "  make clean            - Remove all test artifacts"
	@echo "  make pre-commit       - Run pre-commit hooks"

install:
	pip install -r requirements.txt

lint:
	ansible-lint --strict bootstrap/

test: lint
	molecule test -s debian-13

molecule-create:
	molecule create -s debian-13

molecule-converge:
	molecule converge -s debian-13

molecule-verify:
	molecule verify -s debian-13

molecule-destroy:
	molecule destroy -s debian-13

pre-commit:
	pre-commit run --all-files

clean:
	molecule destroy -s debian-13
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
