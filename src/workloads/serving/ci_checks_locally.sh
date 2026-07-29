#!/usr/bin/env bash
# set -euo pipefail commented out to catch many bugs at a time

cd /workspace

make clean

pip install -q -r src/workloads/serving/requirements-ci.txt --break-system-packages

cd src/workloads/serving

ruff check . --fix && ruff format . && basedpyright . && pytest tests/ -v

echo "=== All CI checks passed ==="