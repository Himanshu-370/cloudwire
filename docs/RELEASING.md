# Release Guide

How to build, test, and publish a new version of cloudwire to PyPI.

---

## Overview

The release pipeline works like this:

```
code changes → bump version → git tag → GitHub Actions → PyPI
```

GitHub Actions handles the build (frontend + wheel) and the upload automatically when you push a version tag. You never need to run `twine` locally unless you're doing a manual release.

---

## Prerequisites (first-time setup)

### 1. Configure PyPI trusted publishing

cloudwire uses OIDC trusted publishing — no API tokens to store or rotate.

1. Create an account on [pypi.org](https://pypi.org) if you don't have one
2. Go to **Account settings → Publishing**
3. Click **Add a new publisher** and fill in:
   - **PyPI project name:** `cloudwire`
   - **GitHub owner:** your GitHub username or org
   - **Repository name:** `cloudwire`
   - **Workflow filename:** `publish.yml`
   - **Environment name:** `pypi`
4. Save

### 2. Create the GitHub environment

1. Go to your repo on GitHub → **Settings → Environments**
2. Click **New environment**, name it `pypi`
3. Optionally add protection rules (e.g. require a review before publishing)

That's all — no secrets to add. The OIDC handshake between GitHub Actions and PyPI handles authentication.

---

## Standard release process

### Quick release (one command)

If you just want to bump, build, and publish in one step:

```bash
make release V=0.2.0
```

This will:
1. Update the version in `cloudwire/__init__.py` and `pyproject.toml`
2. Clean previous build artifacts
3. Build the React frontend into `cloudwire/static/`
4. Build the Python wheel and sdist
5. Upload to PyPI via `twine`

After the upload, tag and push:

```bash
git add cloudwire/__init__.py pyproject.toml
git commit -m "chore: bump version to 0.2.0"
git tag v0.2.0
git push && git push origin v0.2.0
```

> **Note:** `make release` requires `twine` and `build` (`pip install twine build`) and PyPI credentials configured via `~/.pypirc` or `TWINE_USERNAME`/`TWINE_PASSWORD` env vars.

### Step-by-step release

Use this if you prefer more control over each step.

#### Step 1 — Make your changes

Work on a branch or directly on `main`. All your code changes, bug fixes, and new features go here.

#### Step 2 — Bump the version

Version must be updated in exactly two places:

**`cloudwire/__init__.py`**
```python
__version__ = "0.2.0"   # was "0.1.2"
```

**`pyproject.toml`**
```toml
version = "0.2.0"   # was "0.1.2"
```

Both must match. The wheel filename, `cloudwire --version`, and the PyPI listing all read from these.

#### Step 3 — Commit the version bump

```bash
git add cloudwire/__init__.py pyproject.toml
git commit -m "chore: bump version to 0.2.0"
git push
```

#### Step 4 — Tag and push

```bash
git tag v0.2.0
git push origin v0.2.0
```

The tag must start with `v` followed by the version number (e.g. `v0.2.0`, `v1.0.0-rc1`).

### Step 5 — Watch the pipeline

Go to your repo → **Actions**. The `Publish to PyPI` workflow will:

1. Check out the code
2. Install Node.js and run `npm ci && npm run build` (builds the React frontend)
3. Install Python and run `python -m build` (builds the wheel)
4. Verify that `static/index.html` and JS assets are bundled inside the wheel
5. Publish to PyPI via trusted publishing

The whole pipeline takes about 2–3 minutes. When it's green, the new version is live on PyPI.

### Step 6 — Verify the release

```bash
pip install --upgrade cloudwire
cloudwire --version
# cloudwire, version 0.2.0
```

---

## Version numbering

Follow [Semantic Versioning](https://semver.org/):

```
MAJOR.MINOR.PATCH
```

| Increment | When |
|-----------|------|
| **PATCH** (`0.1.0 → 0.1.1`) | Bug fixes, scanner tweaks, dependency updates with no behaviour change |
| **MINOR** (`0.1.0 → 0.2.0`) | New services, new UI features, new CLI options, backwards-compatible changes |
| **MAJOR** (`0.1.0 → 1.0.0`) | Breaking CLI flags, major API changes, drop Python version support |

Pre-release versions:

```bash
git tag v1.0.0-rc1    # release candidate
git tag v1.0.0-beta1  # beta
```

PyPI accepts these as pre-releases — users only get them if they explicitly `pip install cloudwire==1.0.0rc1` or use `--pre`.

---

## Manual release (without GitHub Actions)

Use this if Actions is unavailable or you need to publish from your machine.

```bash
# 1. Build frontend
cd frontend && npm ci && npm run build && cd ..

# 2. Build the wheel
python -m build

# 3. Verify the wheel contains static assets
python - <<'EOF'
import zipfile
from pathlib import Path
whl = next(Path("dist").glob("*.whl"))
with zipfile.ZipFile(whl) as z:
    names = z.namelist()
assert any("static/index.html" in n for n in names), "index.html missing"
assert any("static/assets" in n and n.endswith(".js") for n in names), "JS bundle missing"
print(f"OK: {whl.name} looks good")
EOF

# 4. Upload
pip install twine
twine upload dist/*
```

Twine will prompt for your PyPI username and password (or API token if you prefer).

---

## Hotfix release

If a critical bug needs to be fixed on an already-released version:

```bash
# Create a hotfix branch from the release tag
git checkout -b hotfix/0.1.1 v0.1.0

# Fix the bug, then bump patch version
# ... edit files ...
git add -A
git commit -m "fix: <description>"

# Tag and push
git tag v0.1.1
git push origin hotfix/0.1.1 v0.1.1

# Merge fix back to main
git checkout main
git merge hotfix/0.1.1
git push
```

---

## What's in the wheel

The published wheel (`cloudwire-X.Y.Z-py3-none-any.whl`) contains:

```
cloudwire/
├── __init__.py
├── cli.py
├── app/
│   ├── main.py
│   ├── errors.py
│   ├── aws_clients.py
│   ├── models.py
│   ├── services.py
│   ├── scanner.py
│   ├── scan_jobs.py
│   ├── graph_store.py
│   ├── terraform_parser.py
│   ├── hcl_parser.py
│   ├── routes/
│   │   ├── scan.py
│   │   ├── tags.py
│   │   └── terraform.py
│   └── scanners/
│       ├── _utils.py
│       └── ...
└── static/
    ├── index.html
    └── assets/
        ├── index-<hash>.js
        └── index-<hash>.css
```

The wheel is `py3-none-any` — pure Python, platform-independent. Users on macOS, Linux, and Windows all install the same file.

---

## Makefile reference

```bash
make release V=X.Y.Z  # bump version, build everything, upload to PyPI
make build            # full build: npm run build + python -m build
make frontend         # frontend only: npm run build → cloudwire/static/
make package          # Python wheel only (run make frontend first)
make clean            # remove cloudwire/static/, dist/, build/, *.egg-info/
make install-dev      # pip install -e . (editable install for local development)
make dev              # start backend (:8000) and frontend dev server (:5173)
```

---

## Checklist before every release

- [ ] Version bumped in `cloudwire/__init__.py` and `pyproject.toml`
- [ ] Both version strings match the tag you're about to push
- [ ] `make build` completes without errors locally
- [ ] Installed the wheel locally and confirmed `cloudwire --version` is correct
- [ ] Did a quick scan against a real or test AWS account and the graph renders
- [ ] Committed and pushed all changes to `main`
- [ ] Tag pushed: `git push origin vX.Y.Z`
- [ ] GitHub Actions pipeline is green
- [ ] `pip install --upgrade cloudwire` on a clean machine shows the new version
