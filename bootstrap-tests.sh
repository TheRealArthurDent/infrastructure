#!/bin/bash
# Bootstrap script to set up the testing framework
# Run this after cloning/updating the repo

set -e  # Exit on error

echo "🚀 Bootstrap Ansible Testing Framework"
echo "======================================"
echo ""

# Check if Python is installed
if ! command -v python &> /dev/null; then
    echo "❌ Python 3 is required but not found"
    exit 1
fi

echo "✓ Python found: $(python3 --version)"
echo ""

# Create virtual environment (optional but recommended)
if [ ! -d "venv" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv venv
    echo "✓ Virtual environment created"
    echo ""
    echo "📌 Activate with: source venv/bin/activate"
fi

# Install dependencies
echo "📥 Installing Python dependencies..."
pip install -r requirements.txt > /dev/null 2>&1
echo "✓ Dependencies installed"
echo ""

# Install pre-commit hooks (optional)
if command -v pre-commit &> /dev/null; then
    echo "🔧 Setting up pre-commit hooks..."
    pre-commit install > /dev/null 2>&1
    echo "✓ Pre-commit hooks installed"
    echo ""
fi

# Run basic checks
echo "🧪 Running basic checks..."
echo ""

echo "  • Ansible syntax check..."
ansible-playbook --syntax-check bootstrap/playbooks/bootstrap.yaml > /dev/null 2>&1 && echo "    ✓ Playbook syntax is valid" || echo "    ⚠ Playbook syntax check failed"

echo ""
echo "  • Docker is available..."
docker --version > /dev/null 2>&1 && echo "    ✓ Docker is available" || echo "    ⚠ Docker is not available (required for Molecule)"

echo ""
echo "  • Molecule is available..."
molecule --version > /dev/null 2>&1 && echo "    ✓ Molecule is available" || echo "    ⚠ Molecule is not available"

echo ""
echo "✅ Setup complete!"
echo ""
echo "📚 Next steps:"
echo "  1. Read QUICKSTART.md for common commands"
echo "  2. Run 'make test' to run the full test suite"
echo "  3. Read TESTING.md for detailed documentation"
echo ""
echo "🎯 Quick commands:"
echo "  make test              - Run full test suite (lint + molecule)"
echo "  make lint              - Run ansible-lint only"
echo "  make molecule-create   - Create test containers"
echo "  make molecule-converge - Apply playbook"
echo "  make molecule-verify   - Verify results"
echo "  make clean             - Clean up test artifacts"
echo ""
