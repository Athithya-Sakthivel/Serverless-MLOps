"""Session‑wide test fixtures – blocks all real Azure authentication."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True, scope="session")
def _block_azure_credential():
    """Replace DefaultAzureCredential with a MagicMock for the entire test run.

    This guarantees that no real Azure token request is ever made, regardless
    of which module creates a ``BlobServiceClient``.  The mock credential
    satisfies the ``BlobServiceClient`` constructor so that downstream
    tests only need to mock actual I/O (download / upload / checkpoint).
    """
    with patch("azure.identity.DefaultAzureCredential", return_value=MagicMock()):
        yield
