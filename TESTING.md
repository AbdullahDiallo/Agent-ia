# Testing

## Prerequisites
- Python 3.11+

## Install test dependencies
```bash
python3 -m pip install -r requirements-dev.txt
```

## One-liner (local)
```bash
python3 -m pytest -q --cov=app --cov-report=term-missing --cov-report=xml --cov-fail-under=10
```

## Reports
- Terminal coverage summary: printed at the end of `pytest`.
- XML report: `coverage.xml` at project root.

## CI
- Workflow: `.github/workflows/ci.yml`
- Executes:
  - `pytest -q --cov=app --cov-report=term-missing --cov-report=xml --cov-fail-under=10`
  - `python -m compileall app tests`
