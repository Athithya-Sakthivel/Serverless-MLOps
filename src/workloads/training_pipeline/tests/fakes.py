"""Fake Azure Blob Service client for unit tests.

Supports both downloads and uploads in memory, so no ``unittest.mock``
patches are needed for the ELT orchestration tests.
"""

from __future__ import annotations

from typing import IO

from azure.core.exceptions import ResourceNotFoundError


class FakeDownloadStream:
    """Simulates an Azure ``StorageStreamDownloader`` – returns bytes via ``readall()``
    and writes into a buffer via ``readinto()``."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def readall(self) -> bytes:
        return self._data

    def readinto(self, buf: bytearray | memoryview | object) -> int:
        """Write the full blob content into *buf* and return the number of bytes written.

        Handles both in‑memory buffers (``bytearray``, ``memoryview``) and
        file‑like objects (``BufferedWriter``, etc.) via slice assignment or ``write()``.
        """
        n = len(self._data)
        if isinstance(buf, (bytearray, memoryview)):
            buf[:n] = self._data
        else:
            buf.write(self._data)  # type: ignore[union-attr]
        return n


class FakeBlobClient:
    """Simulates a single blob – can download its bytes or upload new bytes."""

    def __init__(self, name: str, container: FakeContainerClient) -> None:
        self._name = name
        self._container = container

    def download_blob(self) -> FakeDownloadStream:
        if self._name not in self._container._blobs:
            raise ResourceNotFoundError(f"Blob {self._name} not found")
        data = self._container._blobs[self._name]
        return FakeDownloadStream(data)

    def upload_blob(
        self,
        data: bytes | IO[bytes],
        overwrite: bool = True,
        content_settings: object = None,
    ) -> None:
        if isinstance(data, bytes):
            self._container._blobs[self._name] = data
        else:
            self._container._blobs[self._name] = data.read()


class FakeContainerClient:
    """Simulates a container – holds a ``dict`` of blob names to bytes."""

    def __init__(self, name: str, service: FakeBlobServiceClient) -> None:
        self._name = name
        self._service = service

    @property
    def _blobs(self) -> dict[str, bytes]:
        return self._service._containers.setdefault(self._name, {})

    def get_blob_client(self, blob: str) -> FakeBlobClient:
        return FakeBlobClient(blob, self)

    def upload_blob(
        self,
        name: str,
        data: bytes | IO[bytes],
        overwrite: bool = True,
        content_settings: object = None,
    ) -> None:
        self.get_blob_client(name).upload_blob(data, overwrite=overwrite)

    def create_container(self) -> None:
        pass


class FakeBlobServiceClient:
    """In‑memory fake that stores blobs in nested dicts.

    Provide ``containers`` as ``{container_name: {blob_name: bytes}}``.
    The structure is mutated by uploads so tests can inspect final state.
    """

    def __init__(self, containers: dict[str, dict[str, bytes]] | None = None) -> None:
        self._containers: dict[str, dict[str, bytes]] = containers or {}

    def get_container_client(self, container: str) -> FakeContainerClient:
        return FakeContainerClient(container, self)
