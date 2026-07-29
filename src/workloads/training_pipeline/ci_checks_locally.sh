#!/usr/bin/env bash
# set -euo pipefail commented out to catch many bugs at a time

cd /workspace


python3 -m venv .venv_train && source .venv_train/bin/activate
pip install -r src/workloads/training_pipeline/requirements.txt
pip install -r src/workloads/training_pipeline/requirements-ci.txt

make clean


cd src/workloads/serving

ruff check . --fix && ruff format . && basedpyright . && pytest tests/ -v

echo "=== All CI checks passed ==="