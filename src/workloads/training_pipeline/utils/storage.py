"""Azure Blob Storage helpers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import IO, Protocol

from azure.core.credentials import TokenCredential
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


# --------------- protocol that both real and fake clients satisfy ---------------


class _BlobDownloadStream(Protocol):
    def readall(self) -> bytes: ...


class _BlobClientProto(Protocol):
    def download_blob(self) -> _BlobDownloadStream: ...
    def upload_blob(
        self,
        data: bytes | IO[bytes],
        overwrite: bool = True,
        content_settings: object = None,
    ) -> None: ...


class _ContainerClientProto(Protocol):
    def get_blob_client(self, blob: str) -> _BlobClientProto: ...
    def upload_blob(
        self,
        name: str,
        data: bytes | IO[bytes],
        overwrite: bool = True,
        content_settings: object = None,
    ) -> None: ...


class StorageClient(Protocol):
    """A client that looks like BlobServiceClient to our code.

    Both ``azure.storage.blob.BlobServiceClient`` and our ``FakeBlobServiceClient``
    satisfy this protocol, so functions can accept either without type errors.
    """

    def get_container_client(self, container: str) -> _ContainerClientProto: ...


# ------------------------------------------------------------------------------


def build_blob_service_client(
    storage_account_name: str,
    *,
    credential: TokenCredential | None = None,
) -> BlobServiceClient:
    """Return a BlobServiceClient authenticated via DefaultAzureCredential.

    A custom *credential* can be injected for testing.
    """
    if not storage_account_name:
        raise ValueError("storage_account_name is required")
    if credential is None:
        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return BlobServiceClient(
        account_url=f"https://{storage_account_name}.blob.core.windows.net",
        credential=credential,
    )


def ensure_container(service_client: StorageClient, container_name: str) -> None:
    """Create container if it does not already exist."""
    if not container_name:
        raise ValueError("container_name is required")
    try:
        service_client.get_container_client(container_name).create_container()  # type: ignore[attr-defined]
    except ResourceExistsError:
        pass


def blob_exists(service_client: StorageClient, container_name: str, blob_name: str) -> bool:
    """Check whether a blob exists."""
    try:
        return service_client.get_blob_client(container=container_name, blob=blob_name).exists()  # type: ignore[attr-defined]
    except HttpResponseError:
        return False


def download_blob_to_tempfile(
    service_client: StorageClient,
    *,
    container_name: str,
    blob_name: str,
    suffix: str = "",
) -> Path:
    """Download a blob to a temporary file on disk."""
    if not container_name:
        raise ValueError("container_name is required")
    if not blob_name:
        raise ValueError("blob_name is required")

    container_client = service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_name)
    fd, temp_path_str = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    temp_path = Path(temp_path_str)

    try:
        downloader = blob_client.download_blob()
        with temp_path.open("wb") as handle:
            downloader.readinto(handle)  # type: ignore[attr-defined]
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
    service_client: StorageClient,
    *,
    container_name: str,
    blob_name: str,
    file_path: Path,
    content_type: str,
    overwrite: bool = True,
) -> None:
    """Upload a local file to blob storage."""
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
    service_client: StorageClient,
    *,
    container_name: str,
    blob_name: str,
    data: bytes,
    content_type: str,
    overwrite: bool = True,
) -> None:
    """Upload bytes to blob storage."""
    ensure_container(service_client, container_name)
    container_client = service_client.get_container_client(container_name)
    container_client.upload_blob(
        name=blob_name,
        data=data,
        overwrite=overwrite,
        content_settings=ContentSettings(content_type=content_type),
    )
