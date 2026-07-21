# Qwasda Makefile
# Common development tasks

.PHONY: help install test lint typecheck format build clean run

# Default target
help:
	@echo "Qwasda Development Commands"
	@echo ""
	@echo "  make install      - Install package in editable mode with dev dependencies"
	@echo "  make test         - Run pytest with coverage"
	@echo "  make lint         - Run ruff linter"
	@echo "  make typecheck    - Run mypy type checker"
	@echo "  make format       - Format code with ruff/black"
	@echo "  make build        - Build PyInstaller executable"
	@echo "  make clean        - Remove build artifacts"
	@echo "  make run          - Run Qwasda from source"
	@echo "  make pre-commit   - Install pre-commit hooks"

# Install in development mode
install:
	pip install -e ".[dev]"
	pre-commit install

# Run tests
test:
	pytest -v --tb=short

# Run linter
lint:
	ruff check .

# Run type checker
typecheck:
	mypy qwasda

# Format code
format:
	ruff format .
	ruff check --fix .

# Build executable with PyInstaller
build:
	pyinstaller Qwasda.spec --clean --noconfirm

# Clean build artifacts
clean:
	rm -rf build dist *.egg-info __pycache__ .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Run from source
run:
	python -m qwasda

# Install pre-commit hooks
pre-commit:
	pre-commit install
	pre-commit run --all-files

# Full CI pipeline locally
ci: lint typecheck test
	@echo "All checks passed!"