import io
import logging
import os
from datetime import UTC, datetime

import polars as pl
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from datasets import load_dataset

# Keep only application-level messages.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("datasets").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

STORAGE_ACCOUNT_NAME = os.environ["ARTIFACTS_STORAGE_ACC_NAME"]
RAW_CONTAINER = os.getenv("RAW_CONTAINER_NAME", "raw")
HF_DATASET = os.getenv("HF_DATASET", "scikit-learn/adult-census-income")
HF_SPLIT = os.getenv("HF_SPLIT", "train")

DEFAULT_ROWS = 10_000
MAX_ROWS = 1_000_000

ROWS = min(
    int(os.getenv("ROWS", str(DEFAULT_ROWS))),
    MAX_ROWS,
)

PREVIEW_ROWS = min(
    int(os.getenv("PREVIEW_ROWS", "5")),
    20,
)

ACCOUNT_URL = f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net"


def load_sample() -> pl.DataFrame:
    logging.info("Loading dataset '%s' split='%s'...", HF_DATASET, HF_SPLIT)

    # Use a regular Dataset here so we can inspect schema, preview rows,
    # and take a deterministic slice without streaming overhead.
    dataset = load_dataset(HF_DATASET, split=HF_SPLIT)

    total_rows = len(dataset)
    sample_size = min(ROWS, total_rows)

    logging.info("Rows available : %d", total_rows)
    logging.info("Columns        : %d", len(dataset.column_names))

    # Ask Datasets to hand rows back as Polars objects.
    sample_df = dataset.with_format("polars")[:sample_size]

    if not isinstance(sample_df, pl.DataFrame):
        raise TypeError(f"Expected polars.DataFrame, got {type(sample_df)!r}")

    logging.info("Loaded sample  : %d rows × %d columns", sample_df.height, sample_df.width)

    logging.info("Schema:")
    for col_name, dtype in sample_df.schema.items():
        logging.info("  %-20s %s", col_name, dtype)

    logging.info(
        "First %d rows:\n%s", min(PREVIEW_ROWS, sample_df.height), sample_df.head(PREVIEW_ROWS)
    )

    return sample_df


def upload_parquet(df: pl.DataFrame) -> None:
    credential = DefaultAzureCredential()
    blob_service = BlobServiceClient(account_url=ACCOUNT_URL, credential=credential)

    # UTC keeps blob names sortable and avoids local-time ambiguity.
    blob_name = f"monthly/batch_{datetime.now(UTC):%Y%m%d_%H%M%S}.parquet"

    # Write Parquet to an in-memory buffer; Azure upload_blob accepts streams.
    buffer = io.BytesIO()
    df.write_parquet(buffer, compression="zstd")
    buffer.seek(0)

    blob_service.get_container_client(RAW_CONTAINER).upload_blob(
        name=blob_name,
        data=buffer,
        overwrite=True,
        content_settings=ContentSettings(content_type="application/vnd.apache.parquet"),
    )

    logging.info("Uploaded %d rows to %s/%s", df.height, RAW_CONTAINER, blob_name)


def main() -> None:
    df = load_sample()
    upload_parquet(df)
    logging.info("Done.")


if __name__ == "__main__":
    main()
