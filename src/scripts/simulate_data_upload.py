#!/usr/bin/env python3
"""
Simulate data upload.

Behavior:
- Load a slice of a Hugging Face dataset.
- Save a fixed 10,000-row local Parquet copy for CI tests.
- Upload a configurable number of rows to Azure Blob Storage.

Environment variables:
  ARTIFACTS_STORAGE_ACC_NAME   Required. Azure storage account name.
  RAW_CONTAINER_NAME           Optional. Default: raw
  RAW_BLOB_PREFIX              Optional. Default: monthly/
  HF_DATASET                   Optional. Default: birkhoffg/folktables-acs-income
  HF_SPLIT                     Optional. Default: train
  ROWS                         Optional. Default: 9700000
  PREVIEW_ROWS                 Optional. Default: 5
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

LOCAL_SAMPLE_ROWS = 10_000
DEFAULT_BLOB_ROWS = 9_700_000
DEFAULT_PREVIEW_ROWS = 5
MAX_BLOB_ROWS = 9_700_000

STORAGE_ACCOUNT_RE = re.compile(r"^[a-z0-9]{3,24}$")
PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"
LOCAL_SAMPLE_PATH = Path("src/ci-samples/data.parquet")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)

    # Keep third-party noise down.
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

    # Hide Hugging Face progress bars in CI.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")


@dataclass(frozen=True)
class Config:
    storage_account_name: str
    container_name: str = DEFAULT_RAW_CONTAINER
    blob_prefix: str = DEFAULT_BLOB_PREFIX
    dataset_name: str = DEFAULT_DATASET
    split: str = DEFAULT_SPLIT
    blob_rows: int = DEFAULT_BLOB_ROWS
    preview_rows: int = DEFAULT_PREVIEW_ROWS
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

        blob_rows = parse_int_env(
            "ROWS",
            DEFAULT_BLOB_ROWS,
            minimum=1,
            maximum=MAX_BLOB_ROWS,
        )
        preview_rows = parse_int_env(
            "PREVIEW_ROWS",
            DEFAULT_PREVIEW_ROWS,
            minimum=1,
            maximum=20,
        )

        return cls(
            storage_account_name=storage_account_name,
            container_name=container_name,
            blob_prefix=blob_prefix,
            dataset_name=dataset_name,
            split=split,
            blob_rows=blob_rows,
            preview_rows=preview_rows,
            hf_token=hf_token,
        )


def normalize_blob_prefix(value: str) -> str:
    prefix = (value or DEFAULT_BLOB_PREFIX).strip().lstrip("/")
    if not prefix:
        prefix = DEFAULT_BLOB_PREFIX
    if not prefix.endswith("/"):
        prefix += "/"
    return prefix


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


def load_dataset_frame(cfg: Config) -> pl.DataFrame:
    """
    Load enough rows for both outputs:
    - local sample: fixed 10,000 rows
    - blob upload: configurable ROWS, default 9.7M
    """
    load_rows = max(LOCAL_SAMPLE_ROWS, cfg.blob_rows)
    split_spec = f"{cfg.split}[:{load_rows}]"

    logging.info("Loading dataset=%r split=%r", cfg.dataset_name, split_spec)
    dataset = load_dataset(
        cfg.dataset_name,
        split=split_spec,
        token=cfg.hf_token,
    )

    df = dataset_to_polars(dataset)
    if df.height == 0:
        raise ValueError("Loaded dataset slice is empty.")

    logging.info("Loaded frame   : %d rows x %d columns", df.height, df.width)
    print_schema(df)

    preview_count = min(cfg.preview_rows, df.height)
    logging.info("Preview (%d rows):\n%s", preview_count, df.head(preview_count))

    return df


def dataset_to_polars(dataset) -> pl.DataFrame:
    """
    Convert a Hugging Face Dataset to Polars with a safe fallback.
    """
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


def print_schema(df: pl.DataFrame) -> None:
    logging.info("Schema:")
    for column_name, dtype in df.schema.items():
        logging.info("  %-20s %s", column_name, dtype)


def save_local_sample(df: pl.DataFrame) -> None:
    local_sample = df.head(min(LOCAL_SAMPLE_ROWS, df.height))
    LOCAL_SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    local_sample.write_parquet(LOCAL_SAMPLE_PATH, compression="zstd")
    logging.info("Saved local sample: %s (%d rows)", LOCAL_SAMPLE_PATH, local_sample.height)


def get_blob_service(cfg: Config) -> BlobServiceClient:
    # DefaultAzureCredential is the normal chained credential for local CLI + Azure runtime use.
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return BlobServiceClient(account_url=cfg.account_url, credential=credential)


def ensure_container(container_client) -> None:
    """
    Idempotent container creation.
    Avoids pre-checks that generate noisy 404 logs.
    """
    try:
        container_client.create_container()
        logging.info("Created container: %s", container_client.container_name)
    except ResourceExistsError:
        logging.info("Container already exists: %s", container_client.container_name)


def upload_blob_data(df: pl.DataFrame, cfg: Config) -> None:
    upload_frame = df.head(min(cfg.blob_rows, df.height))
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
    logging.info("Blob rows      : %d", cfg.blob_rows)
    logging.info("Local rows     : %d", LOCAL_SAMPLE_ROWS)

    df = load_dataset_frame(cfg)
    save_local_sample(df)
    upload_blob_data(df, cfg)

    logging.info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
