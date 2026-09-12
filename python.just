# Venv layout differs by OS: POSIX puts executables in .venv/bin, Windows in
# .venv/Scripts with .exe suffixes. Resolved here via just's os() builtin.

# PROJECT_ROOT defined by consumer before importing.
VENV := PROJECT_ROOT / ".venv"
PYBIN := if os() == "windows" { "Scripts" } else { "bin" }
EXE := if os() == "windows" { ".exe" } else { "" }
VENV_PYTHON := VENV / PYBIN / "python" + EXE

# Lazy: if the venv's python already exists this is a no-op; a half-built
# venv is repaired by deleting .venv and re-running.
[doc("create .venv with pytest/isort/black if its python is missing")]
venv:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ ! -x "{{VENV_PYTHON}}" ]]; then
        python3 -m venv "{{VENV}}"
        "{{VENV_PYTHON}}" -m pip install pip pytest isort black flake8 --upgrade
    fi
