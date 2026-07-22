# src/terraform/main – Serverless MLOps Infrastructure

This directory contains the **complete production‑grade Terraform (OpenTofu) configuration** for a serverless MLOps platform on Azure.  
It provisions all required resources, wires up the event‑driven training trigger, and exposes a secure serving endpoint – with a single command.

---

## Overview

| Capability | Technology |
|------------|------------|
| IaC tool | OpenTofu ≥ 1.12 (Terraform compatible) |
| Providers | `azurerm` 4.80, `azuread` 3.9, `azapi` 2.10 |
| Compute – training | Azure Container Apps Job (event‑driven, 0→1 scale) |
| Compute – serving | Azure Container Apps (HTTP scale, Entra ID auth) |
| Model registry / tracking | Azure Machine Learning workspace (no compute) |
| Data lake | ADLS Gen2 (HNS enabled) – raw / clean / models / logs |
| Event trigger | Blob upload → Event Grid → Storage Queue → Job |
| Observability | Log Analytics, App Insights, Workbook (dashboard) |
| Identity | System‑assigned Managed Identity everywhere, OIDC for CI/CD |
| Secrets | None – no storage keys, no SAS, no hardcoded credentials |

---

## Prerequisites

1. **Azure subscription** – any tier works (Azure for Students is throttled but functional).
2. **Azure CLI** – logged in (`az login`) with subscription Contributor.
3. **OpenTofu** – automatically installed by `run.sh` if missing.
4. **Bootstrap infrastructure** – a state storage account, container, and resource group created by `bootstrap.sh` in `src/terraform/bootstrap/`.

---

## Quick Start

```bash
# 1. Log into Azure
az login

# 2. Plan
cd src/terraform/main
bash run.sh --plan --env staging

# 3. Deploy everything
bash run.sh --create --env staging

# 4. Destroy everything (nuclear – removes all traces)
bash run.sh --destroy --env staging --yes-delete
```

The first deployment may take **15‑20 minutes** because of Container App Environment creation. Subsequent applies are much faster.

---

## Directory Structure

```
src/terraform/main/
├── run.sh                  # Single entrypoint for all operations
├── versions.tf             # Provider & Terraform version constraints
├── providers.tf            # azurerm / azuread configuration
├── backend.tf              # Remote state backend (azurerm)
├── locals.tf               # All resource names derived from env & subscription
├── variables.tf            # Root input variables
├── main.tf                 # Module composition
├── outputs.tf              # Top‑level outputs
├── environments/
│   ├── staging.tfvars      # Overrides for staging
│   └── prod.tfvars         # Overrides for production
└── modules/
    ├── state/              # Resource group, ADLS Gen2, ACR
    ├── observability/      # Log Analytics, App Insights, Workbook, action group
    ├── ml_workspace/       # Azure ML workspace + dedicated storage + Key Vault
    ├── aca/                # Container Apps Environment, serving app, training job
    └── eventing/           # Storage queue, Event Grid system topic, UAMI
```

---

## Modules – what each one owns

### `state`
- Resource group
- ADLS Gen2 storage account (`is_hns_enabled = true`) with containers: `raw`, `clean`, `models`, `logs`
- Azure Container Registry (`Basic` SKU, admin disabled)

### `observability`
- Log Analytics workspace (`PerGB2018`, 30 days)
- Application Insights (workspace‑based)
- Azure Monitor Workbook (shared dashboard)
- Action group with email receiver (no alert rules – see limitations)

### `ml_workspace`
- Azure Machine Learning workspace (no compute)
- Dedicated storage account (`is_hns_enabled = false`) – required by AML
- Key Vault (RBAC authorization, purge protection in prod only)
- Role assignments for the workspace’s managed identity

### `aca`
- Container Apps Environment (Consumption‑only)
- Serving Container App (public ingress, Entra ID auth, multiple revisions)
- Training Container App Job (event‑driven, scales from queue)
- System‑assigned managed identities + RBAC (ACR pull, storage, ML workspace)

### `eventing`
- Storage queue (Event Grid fan‑in)
- Event Grid system topic (blob events)
- User‑assigned managed identity for delivery (optional – not used by default)

---

## Naming Convention

All resource names are **derived** in `locals.tf` – no hardcoded names.  
The pattern is:

```
<abbr>-<resource-type>-<env-abbr><subscription-suffix>
```

| Component | Example (staging) |
|-----------|-------------------|
| Resource group | `rg-sm-artifacts-stg` |
| Data lake | `smstgartifactsf41930` |
| ACR | `acrsmstgf41930` |
| Log Analytics | `law-sm-stg` |
| App Insights | `appi-sm-stg` |
| ML workspace | `mlw-sm-stg-s2` |
| Key Vault | `kv-smstgmlf41930` |
| Container App Env | `acae-sm-stg` |
| Serving app | `aca-serve-stg` |
| Training job | `acaj-train-stg` |
| Storage queue | `smtrainqueue-stg` |
| Event Grid topic | `eg-sm-stg-storage` |

The subscription suffix (last 6 chars) ensures global uniqueness.

---

## Environment Configuration

Environments are configured via `environments/<env>.tfvars`.  
`subscription_id` and `tenant_id` are **never** in tfvars – they’re automatically fetched by `run.sh` or passed via `TF_VAR_*` in CI.

### Example `staging.tfvars`
```hcl
location    = "southindia"
environment = "staging"

storage_container_names   = ["raw", "clean", "models", "logs"]
shared_access_key_enabled = true
alert_email_address       = "alerts@example.com"

aca_training_image = "busybox:1.36.1@sha256:..."
aca_serving_image  = "mcr.microsoft.com/azuredocs/containerapps-helloworld@sha256:..."
aca_serve_port     = 80

event_raw_container_name = "raw"
event_raw_blob_prefix    = "monthly/"
```

---

## `run.sh` – the only command you need

| Command | What it does |
|---------|--------------|
| `--plan --env <env>` | Validates, formats, and creates a plan file |
| `--create --env <env>` | Plans, applies, and wires the Event Grid subscription |
| `--destroy --env <env> --yes-delete` | Nuclear destroy: deletes subscription, resource group, purges soft‑deleted Key Vault/ML workspace, deletes state blob |
| `--validate --env <env>` | Formats and validates the Terraform code |

### What happens during `--create`
1. `tofu plan` and `tofu apply` (with token refresh)
2. After apply, `create_event_subscription()`:
   - derives all resource names from environment & subscription ID (no dependency on `tofu output`)
   - creates the Event Grid subscription via Azure CLI (idempotent)
   - uses shared key delivery (no managed identity needed)

### What happens during `--destroy`
1. Deletes the Event Grid subscription (via CLI)
2. Deletes the resource group and **waits** for full deletion
3. Purges soft‑deleted Key Vault and ML workspace
4. Deletes the Terraform state blob (fresh start next time)

---

## Event Grid subscription – why outside Terraform

The `azurerm_eventgrid_system_topic_event_subscription` resource consistently returns an `Internal error` on student subscriptions (across all India regions). To make the deployment reliable for **any subscription tier**, the subscription is created via Azure CLI after Terraform has provisioned everything else.

The same naming derivation (`locals.tf`) is replicated in `run.sh`, so the CLI commands are always correct regardless of subscription.

---

## Design Decisions

| Decision | Reason |
|----------|--------|
| Two storage accounts | AML requires HNS‑disabled storage; data lake requires HNS for folder‑level RBAC |
| No AML compute | Training runs on Container Apps (cheaper, serverless) |
| System‑assigned managed identity | No secrets, automatic lifecycle |
| Public serving endpoint | Simplified networking; Entra ID auth protects access |
| Shared access key enabled on storage | Allows Event Grid delivery without complex managed identity setup; works on student subscriptions |
| Scheduled query rules removed | Azure for Students lacks `Microsoft.Insights/scheduledQueryRules/write` |
| Workbook in separate JSON template | Avoids provider rename bugs, keeps JSON clean |

---

## Limitations (Azure for Students)

- Container App Environment creation can take **8–15 minutes** due to throttling.
- Scheduled query alert rules cannot be created (subscription permission gap).
- Event Grid subscription must be created via CLI (provider bug on student tier).

All of these are handled automatically by `run.sh`. No manual intervention needed.

---

## Cleanup

Always use `cd cd src/terraform/main && run.sh --destroy --env <env> --yes-delete`.  
It guarantees that:

- All resources in the resource group are deleted
- Soft‑deleted Key Vault and ML workspace are purged (names can be reused)
- Terraform state is wiped (next `--create` starts from scratch)

---

