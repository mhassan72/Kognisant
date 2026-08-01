# PyPI Publishing Plan

Publish `kognisant` to PyPI so users can install with:

```bash
pip install kognisant
```

---

## 1. Package Name

Reserve `kognisant` on PyPI. The current `pyproject.toml` uses `cli-kognisant` — rename to `kognisant` for a cleaner install command.

```toml
[project]
name = "kognisant"
```

Users install: `pip install kognisant`
Command: `kognisant`

---

## 2. pyproject.toml Updates

```toml
[build-system]
requires = ["setuptools>=61.0.0"]
build-backend = "setuptools.build_meta"

[project]
name = "kognisant"
version = "0.1.0"
description = "Autonomous AI copilot with background job execution"
readme = "README.md"
requires-python = ">=3.10"
license = {text = "MIT"}
authors = [
    {name = "MA Hassan", email = "msugroo@gmail.com"}
]
keywords = ["ai", "copilot", "cli", "llm", "agents", "automation"]
classifiers = [
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "License :: OSI Approved :: MIT License",
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS",
    "Environment :: Console",
    "Topic :: Software Development :: Libraries :: Application Frameworks",
    "Intended Audience :: Developers",
]

[project.urls]
Homepage = "https://kognisant.xyz"
Repository = "https://github.com/mhassan72/Kognisant"
Documentation = "https://kognisant.xyz/docs"
Issues = "https://github.com/mhassan72/Kognisant/issues"

[project.scripts]
kognisant = "cli_kognisant.main:main"

[tool.setuptools.packages.find]
include = ["cli_kognisant*"]
exclude = ["tests*", "docs*"]
```

---

## 3. Version Management

Use a simple `__version__` in `cli_kognisant/__init__.py`:

```python
__version__ = "0.1.0"
```

And reference it in pyproject.toml via dynamic versioning, or keep it manually synced for now.

---

## 4. Files to Add/Update

| File | Purpose |
|------|---------|
| `pyproject.toml` | Rename package, add metadata, URLs, classifiers |
| `cli_kognisant/__init__.py` | Add `__version__` |
| `MANIFEST.in` | Ensure non-Python files are included in sdist |
| `.github/workflows/publish.yml` | CI/CD for automated PyPI publishing on tag |

### MANIFEST.in

```
include LICENSE
include README.md
recursive-include cli_kognisant *.py
```

---

## 5. PyPI Account Setup

1. Create account at https://pypi.org/account/register/
2. Enable 2FA (required for new projects)
3. Create API token at https://pypi.org/manage/account/token/
4. Store token as `PYPI_API_TOKEN` in GitHub repo secrets

---

## 6. Build & Publish (Manual)

```bash
# Install build tools
pip install build twine

# Build sdist and wheel
python -m build

# Check the distribution
twine check dist/*

# Upload to TestPyPI first
twine upload --repository testpypi dist/*

# Test install from TestPyPI
pip install --index-url https://test.pypi.org/simple/ kognisant

# Upload to real PyPI
twine upload dist/*
```

---

## 7. GitHub Actions (Automated)

```yaml
# .github/workflows/publish.yml
name: Publish to PyPI

on:
  push:
    tags:
      - 'v*'

jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write  # For trusted publishing
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install build tools
        run: pip install build

      - name: Build package
        run: python -m build

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
```

Workflow:
1. Push a git tag: `git tag v0.1.0 && git push origin v0.1.0`
2. GitHub Action builds and publishes automatically
3. Uses PyPI trusted publishing (no API token needed in secrets)

---

## 8. Install Methods After Publishing

```bash
# Standard install
pip install kognisant

# With venv (recommended)
python3 -m venv ~/.kognisant_venv
source ~/.kognisant_venv/bin/activate
pip install kognisant

# pipx (isolated, auto-venv)
pipx install kognisant

# From git (development)
pip install git+https://github.com/mhassan72/Kognisant.git

# install.sh (for users who don't know pip)
curl -fsSL https://kognisant.xyz/install.sh | sh
```

---

## 9. Update install.sh

Once on PyPI, the install script becomes simpler — no git clone needed:

```sh
# Instead of cloning the repo:
"$VENV_PIP" install --quiet kognisant
```

This eliminates the git dependency entirely for end users.

---

## 10. Checklist

| Step | Status |
|------|--------|
| Rename package to `kognisant` in pyproject.toml | ☐ |
| Add metadata (keywords, classifiers, URLs) | ☐ |
| Add `__version__` to `__init__.py` | ☐ |
| Create PyPI account + enable 2FA | ☐ |
| Set up trusted publishing on PyPI | ☐ |
| Test build locally (`python -m build`) | ☐ |
| Upload to TestPyPI and verify | ☐ |
| Upload to PyPI | ☐ |
| Add GitHub Actions workflow | ☐ |
| Update install.sh to use `pip install kognisant` | ☐ |
| Update README with `pip install kognisant` | ☐ |

---

## 11. Support

| Resource | Address |
|----------|---------|
| PyPI page | `pypi.org/project/kognisant/` |
| Support | `support@kognisant.xyz` |
| Issues | `github.com/mhassan72/Kognisant/issues` |
