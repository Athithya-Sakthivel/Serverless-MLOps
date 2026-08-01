from __future__ import annotations

import logging
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
    # Resource group and job names are URL encoded to avoid failures if
    # future naming conventions introduce reserved URL characters.
    encoded_rg = quote(settings.resource_group_name, safe="")
    encoded_job = quote(settings.job_name, safe="")
    encoded_api_version = quote(settings.job_api_version, safe="")

    return (
        "https://management.azure.com/subscriptions/"
        f"{settings.subscription_id}/resourceGroups/{encoded_rg}"
        f"/providers/Microsoft.App/jobs/{encoded_job}/start"
        f"?api-version={encoded_api_version}"
    )


def start_training_job(settings: Settings, logger: logging.Logger) -> None:
    # Acquire a fresh ARM access token. Token refresh is handled internally
    # by Azure Identity and typically does not require a network call if
    # a cached token is still valid.
    token = _CREDENTIAL.get_token(ARM_SCOPE)

    url = build_job_start_url(settings)

    headers = {
        "Authorization": f"Bearer {token.token}",
        # Explicitly request JSON responses from ARM.
        "Accept": "application/json",
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            # The start operation itself is asynchronous. This timeout only
            # limits how long we wait for ARM to acknowledge the request.
            timeout=settings.request_timeout_seconds,
        )
    except requests.RequestException:
        logger.exception("Failed to call Azure Resource Manager to start the ACA Job.")
        raise

    # ARM returns HTTP 202 because the job execution is queued
    # asynchronously. Some API versions may also return HTTP 200.
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
