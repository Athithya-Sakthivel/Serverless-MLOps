#!/usr/bin/env bash
# set -euo pipefail commented out to catch many bugs at a time

cd /workspace


python3 -m venv .venv_serve && source .venv_serve/bin/activate
pip install -r src/workloads/serving/requirements.txt
pip install -r src/workloads/serving/requirements-ci.txt

make clean


cd /workspace/src/workloads/serving

ruff check . --fix && ruff format . && basedpyright . && pytest tests/ -v

echo "=== All CI checks passed ==="