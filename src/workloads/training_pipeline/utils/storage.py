"""Azure Blob Storage helpers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
)
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"
JSON_CONTENT_TYPE = "application/json"


def build_blob_service_client(storage_account_name: str) -> BlobServiceClient:
    if not storage_account_name:
        raise ValueError("storage_account_name is required")
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return BlobServiceClient(
        account_url=f"https://{storage_account_name}.blob.core.windows.net",
        credential=credential,
    )


def ensure_container(service_client: BlobServiceClient, container_name: str) -> None:
    if not container_name:
        raise ValueError("container_name is required")
    try:
        service_client.get_container_client(container_name).create_container()
    except ResourceExistsError:
        pass


def blob_exists(service_client: BlobServiceClient, container_name: str, blob_name: str) -> bool:
    try:
        return service_client.get_blob_client(container=container_name, blob=blob_name).exists()
    except HttpResponseError:
        return False


def download_blob_to_tempfile(
    service_client: BlobServiceClient,
    *,
    container_name: str,
    blob_name: str,
    suffix: str = "",
) -> Path:
    if not container_name:
        raise ValueError("container_name is required")
    if not blob_name:
        raise ValueError("blob_name is required")

    blob_client = service_client.get_blob_client(container=container_name, blob=blob_name)
    fd, temp_path_str = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    temp_path = Path(temp_path_str)

    try:
        downloader = blob_client.download_blob()
        with temp_path.open("wb") as handle:
            downloader.readinto(handle)
    except ResourceNotFoundError as exc:
        temp_path.unlink(missing_ok=True)
        raise FileNotFoundError(f"Blob not found: {container_name}/{blob_name}") from exc
    except ClientAuthenticationError as exc:
        temp_path.unlink(missing_ok=True)
        raise RuntimeError("Azure authentication failed while downloading blob") from exc
    except HttpResponseError as exc:
        temp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to download blob {container_name}/{blob_name}: {exc}") from exc
    return temp_path


def upload_file_to_blob(
    service_client: BlobServiceClient,
    *,
    container_name: str,
    blob_name: str,
    file_path: Path,
    content_type: str,
    overwrite: bool = True,
) -> None:
    if not file_path.exists():
        raise FileNotFoundError(str(file_path))
    ensure_container(service_client, container_name)
    container_client = service_client.get_container_client(container_name)
    with file_path.open("rb") as handle:
        container_client.upload_blob(
            name=blob_name,
            data=handle,
            overwrite=overwrite,
            content_settings=ContentSettings(content_type=content_type),
        )


def upload_bytes_to_blob(
    service_client: BlobServiceClient,
    *,
    container_name: str,
    blob_name: str,
    data: bytes,
    content_type: str,
    overwrite: bool = True,
) -> None:
    ensure_container(service_client, container_name)
    container_client = service_client.get_container_client(container_name)
    container_client.upload_blob(
        name=blob_name,
        data=data,
        overwrite=overwrite,
        content_settings=ContentSettings(content_type=content_type),
    )
