"""
ACA job start helper.

Uses the Azure Resource Manager REST API to start a Container Apps Job.
The Function App's system‑assigned managed identity is used for
authentication via DefaultAzureCredential.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import requests
from azure.identity import DefaultAzureCredential

from shared.config import Settings

# ARM management endpoint scope used to acquire an Azure AD access token
# for calling the Container Apps management REST API.
ARM_SCOPE = "https://management.azure.com/.default"

# DefaultAzureCredential internally caches tokens and automatically selects
# the appropriate authentication source (Managed Identity in Azure,
# Azure CLI/VS Code locally). Reusing one instance avoids unnecessary
# credential initialization on every function invocation.
_CREDENTIAL = DefaultAzureCredential()


def build_job_start_url(settings: Settings) -> str:
    """Build the ARM URL to start a Container Apps Job."""
    encoded_rg = quote(settings.resource_group_name, safe="")
    encoded_job = quote(settings.job_name, safe="")
    encoded_api_version = quote(settings.job_api_version, safe="")

    return (
        "https://management.azure.com/subscriptions/"
        f"{settings.subscription_id}/resourceGroups/{encoded_rg}"
        f"/providers/Microsoft.App/jobs/{encoded_job}/start"
        f"?api-version={encoded_api_version}"
    )


def start_training_job(
    settings: Settings,
    logger: logging.Logger,
    *,
    blob_name: str = "",
) -> None:
    """
    Start the ACA training job via the ARM REST API.

    If *blob_name* is provided, it is injected as the INPUT_BLOB_NAME
    environment variable override so the training pipeline knows which
    file to process.
    """
    token = _CREDENTIAL.get_token(ARM_SCOPE)
    url = build_job_start_url(settings)
    headers = {
        "Authorization": f"Bearer {token.token}",
        "Accept": "application/json",
    }

    # ARM allows overriding container environment variables at start time.
    # Pass the blob name so the training pipeline knows which file to process.
    body: dict[str, Any] = {}
    if blob_name:
        body = {
            "template": {
                "containers": [
                    {
                        "name": "train",
                        "env": [{"name": "INPUT_BLOB_NAME", "value": blob_name}],
                    }
                ]
            }
        }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=body,
            timeout=settings.request_timeout_seconds,
        )
    except requests.RequestException:
        logger.exception("Failed to call Azure Resource Manager to start the ACA Job.")
        raise

    if response.status_code not in (200, 202):
        logger.error(
            "Failed to start ACA job. status=%s body=%s",
            response.status_code,
            response.text,
        )
        response.raise_for_status()

    logger.info(
        "ACA job start accepted. status=%s body=%s",
        response.status_code,
        response.text.strip() or "<empty>",
    )
