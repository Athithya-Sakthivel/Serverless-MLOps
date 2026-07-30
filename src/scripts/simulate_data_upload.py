#!/usr/bin/env python3
"""
Simulate data upload and maintain a balanced CI sample.

- Load a slice of the Hugging Face ACS dataset and upload it to Azure Blob Storage.
- The local CI sample (src/ci-samples/data.parquet) is **always** generated
  from a synthetic balanced dataset, so CI tests never fail because of a
  single-class label set.  The real-data upload path is unchanged.

Environment variables:
  ARTIFACTS_STORAGE_ACC_NAME   Required. Azure storage account name.
  RAW_CONTAINER_NAME           Optional. Default: raw
  RAW_BLOB_PREFIX              Optional. Default: monthly/
  HF_DATASET                   Optional. Default: birkhoffg/folktables-acs-income
  HF_SPLIT                     Optional. Default: train
  MAX_DATASET_ROWS             Optional. Default: 100_000  (min 1_000, max 1_000_000)
  HF_TOKEN                     Optional. Hugging Face token
"""

from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from datasets import load_dataset

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

DEFAULT_DATASET = "birkhoffg/folktables-acs-income"
DEFAULT_SPLIT = "train"
DEFAULT_RAW_CONTAINER = "raw"
DEFAULT_BLOB_PREFIX = "monthly/"

STORAGE_ACCOUNT_RE = re.compile(r"^[a-z0-9]{3,24}$")
PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"
LOCAL_SAMPLE_PATH = Path("src/ci-samples/data.parquet")


# ---------------------------------------------------------------------------
# Helpers (must be defined before the constants that call them)
# ---------------------------------------------------------------------------
def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    for name in (
        "datasets",
        "huggingface_hub",
        "httpx",
        "httpcore",
        "urllib3",
        "azure",
        "filelock",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")


def parse_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")
    if value > maximum:
        value = maximum
    return value


def normalize_blob_prefix(value: str) -> str:
    prefix = (value or DEFAULT_BLOB_PREFIX).strip().lstrip("/")
    if not prefix:
        prefix = DEFAULT_BLOB_PREFIX
    if not prefix.endswith("/"):
        prefix += "/"
    return prefix


# ---------------------------------------------------------------------------
# Fixed constants
# ---------------------------------------------------------------------------
_CI_SEED = 42
_CI_ROWS = 2_000
_PREVIEW_ROWS = 3
_THRESHOLD = 50_000
_STATES = ["NY", "CA", "TX", "FL", "IL", "PA", "OH", "GA", "NC", "MI"]
_UPLOAD_ROWS = parse_int_env("MAX_DATASET_ROWS", 100_000, minimum=1_000, maximum=1_000_000)


@dataclass(frozen=True)
class Config:
    storage_account_name: str
    container_name: str = DEFAULT_RAW_CONTAINER
    blob_prefix: str = DEFAULT_BLOB_PREFIX
    dataset_name: str = DEFAULT_DATASET
    split: str = DEFAULT_SPLIT
    upload_rows: int = _UPLOAD_ROWS
    hf_token: str | None = None

    @property
    def account_url(self) -> str:
        return f"https://{self.storage_account_name}.blob.core.windows.net"

    @classmethod
    def from_env(cls) -> Config:
        storage_account_name = os.environ.get("ARTIFACTS_STORAGE_ACC_NAME", "").strip()
        if not storage_account_name:
            raise ValueError(
                "ARTIFACTS_STORAGE_ACC_NAME is required. "
                "Set it to the exact Azure storage account name."
            )
        if not STORAGE_ACCOUNT_RE.fullmatch(storage_account_name):
            raise ValueError(
                f"Invalid storage account name {storage_account_name!r}. "
                "Use only lowercase letters and numbers, 3-24 characters."
            )

        container_name = os.getenv("RAW_CONTAINER_NAME", DEFAULT_RAW_CONTAINER).strip()
        if not container_name:
            raise ValueError("RAW_CONTAINER_NAME cannot be empty.")

        blob_prefix = normalize_blob_prefix(os.getenv("RAW_BLOB_PREFIX", DEFAULT_BLOB_PREFIX))
        dataset_name = os.getenv("HF_DATASET", DEFAULT_DATASET).strip()
        split = os.getenv("HF_SPLIT", DEFAULT_SPLIT).strip()
        hf_token = os.getenv("HF_TOKEN") or None

        upload_rows = parse_int_env("MAX_DATASET_ROWS", 100_000, minimum=1_000, maximum=1_000_000)

        return cls(
            storage_account_name=storage_account_name,
            container_name=container_name,
            blob_prefix=blob_prefix,
            dataset_name=dataset_name,
            split=split,
            upload_rows=upload_rows,
            hf_token=hf_token,
        )


def _build_synthetic_frame(n_rows: int, seed: int) -> pl.DataFrame:
    """Return a balanced synthetic DataFrame with the ACS schema."""
    rng = np.random.default_rng(seed)
    half = n_rows // 2

    agep = rng.integers(18, 80, n_rows).astype(float)
    cow = rng.integers(1, 8, n_rows).astype(float)
    schl = rng.integers(1, 24, n_rows).astype(float)
    mar = rng.integers(1, 5, n_rows).astype(float)
    occp = rng.integers(100, 5000, n_rows).astype(float)
    pobp = rng.integers(1, 100, n_rows).astype(float)
    relp = rng.integers(0, 10, n_rows).astype(float)
    wkhp = rng.integers(0, 60, n_rows).astype(float)
    sex = rng.integers(1, 2, n_rows).astype(float)
    rac1p = rng.integers(1, 9, n_rows).astype(float)
    year = rng.integers(2019, 2025, n_rows).astype(float)
    state = rng.choice(_STATES, n_rows)

    income = np.empty(n_rows, dtype=float)
    income[:half] = rng.integers(1_000, _THRESHOLD - 1, half).astype(float)
    income[half:] = rng.integers(_THRESHOLD, _THRESHOLD + 100_000, n_rows - half).astype(float)
    rng.shuffle(income)

    return pl.DataFrame(
        {
            "AGEP": agep,
            "COW": cow,
            "SCHL": schl,
            "MAR": mar,
            "OCCP": occp,
            "POBP": pobp,
            "RELP": relp,
            "WKHP": wkhp,
            "SEX": sex,
            "RAC1P": rac1p,
            "STATE": state,
            "YEAR": year,
            "PINCP": income,
        }
    )


def load_dataset_frame(cfg: Config) -> pl.DataFrame:
    load_rows = max(_CI_ROWS, cfg.upload_rows)
    split_spec = f"{cfg.split}[:{load_rows}]"

    logging.info("Loading dataset=%r split=%r", cfg.dataset_name, split_spec)
    dataset = load_dataset(cfg.dataset_name, split=split_spec, token=cfg.hf_token)

    df = dataset_to_polars(dataset)
    if df.height == 0:
        raise ValueError("Loaded dataset slice is empty.")

    logging.info("Loaded frame   : %d rows x %d columns", df.height, df.width)

    preview_count = min(_PREVIEW_ROWS, df.height)
    logging.info("Preview (%d rows):\n%s", preview_count, df.head(preview_count))

    return df


def dataset_to_polars(dataset) -> pl.DataFrame:
    try:
        formatted = dataset.with_format("polars")[:]
        if isinstance(formatted, pl.DataFrame):
            return formatted
        if isinstance(formatted, dict):
            return pl.DataFrame(formatted)
    except Exception as exc:
        logging.debug("Polars format conversion failed: %s", exc)

    raw = dataset[:]
    if isinstance(raw, pl.DataFrame):
        return raw
    if isinstance(raw, dict):
        return pl.DataFrame(raw)

    raise TypeError(f"Unsupported dataset conversion result: {type(raw)!r}")


# ---------------------------------------------------------------------------
# Local CI sample – always synthetic and balanced
# ---------------------------------------------------------------------------
def save_local_sample(_df: pl.DataFrame | None = None) -> None:
    frame = _build_synthetic_frame(_CI_ROWS, _CI_SEED)
    LOCAL_SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(LOCAL_SAMPLE_PATH, compression="zstd")
    logging.info("Saved CI sample: %s (%d rows)", LOCAL_SAMPLE_PATH, frame.height)


# ---------------------------------------------------------------------------
# Azure Blob Storage helpers
# ---------------------------------------------------------------------------
def get_blob_service(cfg: Config) -> BlobServiceClient:
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return BlobServiceClient(account_url=cfg.account_url, credential=credential)


def ensure_container(container_client) -> None:
    try:
        container_client.create_container()
        logging.info("Created container: %s", container_client.container_name)
    except ResourceExistsError:
        logging.info("Container already exists: %s", container_client.container_name)


def upload_blob_data(df: pl.DataFrame, cfg: Config) -> None:
    upload_frame = df.head(min(cfg.upload_rows, df.height))
    blob_name = f"{cfg.blob_prefix}batch_{datetime.now(UTC):%Y%m%d_%H%M%S}.parquet"

    buffer = io.BytesIO()
    upload_frame.write_parquet(buffer, compression="zstd")
    buffer.seek(0)

    try:
        with get_blob_service(cfg) as blob_service:
            container_client = blob_service.get_container_client(cfg.container_name)
            ensure_container(container_client)

            container_client.upload_blob(
                name=blob_name,
                data=buffer,
                overwrite=True,
                content_settings=ContentSettings(content_type=PARQUET_CONTENT_TYPE),
            )
    except ClientAuthenticationError as exc:
        raise RuntimeError(
            "Azure authentication failed. Check the identity used by DefaultAzureCredential."
        ) from exc
    except HttpResponseError as exc:
        error_code = str(getattr(exc, "error_code", "") or "")
        error_message = str(exc)
        if (
            "AuthorizationPermissionMismatch" in error_code
            or "AuthorizationPermissionMismatch" in error_message
        ):
            raise RuntimeError(
                "Upload was authenticated but not authorized. "
                "Assign Storage Blob Data Contributor on the storage account scope."
            ) from exc
        raise

    logging.info("Uploaded %d rows to %s/%s", upload_frame.height, cfg.container_name, blob_name)


def main() -> int:
    configure_logging()
    cfg = Config.from_env()

    logging.info("Storage account: %s", cfg.storage_account_name)
    logging.info("Account URL    : %s", cfg.account_url)
    logging.info("Container      : %s", cfg.container_name)
    logging.info("Blob prefix    : %s", cfg.blob_prefix)
    logging.info("Upload rows    : %d", cfg.upload_rows)

    # 1. Refresh CI sample (always synthetic)
    save_local_sample()

    # 2. Load real data and upload
    df = load_dataset_frame(cfg)
    upload_blob_data(df, cfg)

    logging.info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
