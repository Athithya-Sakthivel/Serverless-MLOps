#!/usr/bin/env python3
"""Local development runner – runs ELT, training, or both."""

from __future__ import annotations

import argparse

from elt.extract import resolve_input_blob_name
from main import _run_elt
from train.orchestrator import run_training_pipeline
from utils.config import AppConfig
from utils.logging import configure_logging, get_logger

LOG = get_logger(__name__)


def main() -> int:
    configure_logging()
    config = AppConfig.from_env()

    parser = argparse.ArgumentParser(description="Run training pipeline stages locally")
    parser.add_argument("--elt", action="store_true", help="Run ELT phase")
    parser.add_argument("--train", action="store_true", help="Run training phase")
    parser.add_argument("--full", action="store_true", help="Run both ELT and training (default)")
    parser.add_argument(
        "--raw-blob",
        default=None,
        help="Raw blob name (default: read from INPUT_BLOB_NAME env var)",
    )
    args = parser.parse_args()

    # If no specific phase given, default to --full
    if not args.elt and not args.train and not args.full:
        args.full = True

    raw_blob_name = args.raw_blob or resolve_input_blob_name()

    if args.elt or args.full:
        LOG.info("Starting ELT for blob %s", raw_blob_name)
        clean_blob = _run_elt(config, raw_blob_name)
        LOG.info("ELT finished. Clean blob: %s", clean_blob)
    else:
        # When training alone, we still need the clean blob name.
        # The clean blob name is deterministic; derive it.
        from elt.load import clean_blob_name

        clean_blob = clean_blob_name(raw_blob_name)

    if args.train or args.full:
        LOG.info("Starting training for raw blob %s, clean blob %s", raw_blob_name, clean_blob)
        result = run_training_pipeline(
            config=config,
            raw_blob_name=raw_blob_name,
            clean_blob_name=clean_blob,
        )
        LOG.info(
            "Training completed. Run ID: %s, ONNX SHA: %s",
            result.mlflow_run_id,
            result.onnx_sha256,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
