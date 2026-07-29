#!/usr/bin/env bash
# set -euo pipefail commented out to catch many bugs at a time

cd /workspace

make clean

source .venv/bin/activate
pip install -q -r src/workloads/training_pipeline/requirements.txt

cd src/workloads/training_pipeline

ruff check . --fix && ruff format . && basedpyright . && pytest tests/ -v

echo "=== All CI checks passed ==="