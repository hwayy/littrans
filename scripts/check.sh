#!/usr/bin/env bash
# LitTrans release checks on a POSIX host: the twin of scripts/check.ps1.
#
#   bash scripts/check.sh            # uses python3 from PATH
#   PYTHON=python3.13 bash scripts/check.sh
#
# Creates the repository virtual environment (.venv) on first use, installs the plugin
# with its development dependencies, validates the release metadata, then runs ruff,
# mypy, the test suite and `littrans doctor` exactly as the Windows script does.
set -euo pipefail

PYTHON="${PYTHON:-python3}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_ROOT="$REPO_ROOT/plugins/literature-translation"
VENV_ROOT="$REPO_ROOT/.venv"
VENV_PYTHON="$VENV_ROOT/bin/python"
PYTEST_TEMP="$REPO_ROOT/tmp/release-checks/pytest"
SOURCE_PATH="$PLUGIN_ROOT/src"

mkdir -p "$(dirname "$PYTEST_TEMP")"

if [ ! -x "$VENV_PYTHON" ]; then
    "$PYTHON" -m venv "$VENV_ROOT"
fi

if ! "$VENV_PYTHON" -c "import hatchling" >/dev/null 2>&1; then
    "$VENV_PYTHON" -m pip install --quiet --disable-pip-version-check "hatchling>=1.25"
fi

"$VENV_PYTHON" -m pip install --quiet --disable-pip-version-check --no-build-isolation "$PLUGIN_ROOT[dev]"

"$VENV_PYTHON" "$REPO_ROOT/scripts/validate_release.py"

export PYTHONPATH="$SOURCE_PATH${PYTHONPATH:+:$PYTHONPATH}"

cd "$PLUGIN_ROOT"
"$VENV_PYTHON" -m ruff check .
"$VENV_PYTHON" -m mypy src/littrans
"$VENV_PYTHON" -m pytest --basetemp "$PYTEST_TEMP"
"$VENV_PYTHON" scripts/littrans.py doctor

echo "LitTrans release checks passed."
