# Azure Pipelines – Serverless MLOps CI/CD

Continuous integration and delivery for the **Serverless‑MLOps** system.  
Pipelines are organised by workload: data engineering (ELT), model training, infrastructure as code, and deployment.

---

## Directory structure

```
azure-pipelines/
├── ci/                          # Continuous integration pipelines
│   ├── ci-elt.yaml              # ELT function app – lint, test, validate bindings
│   ├── ci-ml-training.yaml      # Model training – test, train, register in MLflow
│   ├── ci-terraform.yaml        # Infrastructure – fmt, validate, plan
│   └── full_repo_security_scan.yaml
├── cd/                          # Continuous delivery pipelines
│   ├── cd-deploy.yaml           # Deploy ELT + model serving (parameterised)
│   └── cd-terraform.yaml        # Apply infrastructure plan
├── templates/                   # Reusable job templates
│   ├── python-ci.yaml           # Shared Python lint/test steps (optional Docker build)
│   ├── elt-deploy.yaml          # Deploy Python Azure Function
│   └── aca-deploy.yaml          # Canary deploy to Azure Container Apps with k6
└── README.md
```

---

## Pipeline inventory

### CI pipelines

| Pipeline | Trigger | Purpose |
|----------|---------|---------|
| `ci-elt.yaml` | Push / PR to `src/workloads/elt/**` | Ruff lint, mypy type‑check, pytest (unit + data quality + integration), validate function bindings |
| `ci-ml-training.yaml` | Push / PR to `src/workloads/train/**` | Ruff lint, pytest (model tests), train LightGBM, log experiment to MLflow, register model |
| `ci-terraform.yaml` | Push / PR to `src/terraform/main/**` | `terraform fmt`, `validate`, deterministic plan (plan artifact published) |
| `full_repo_security_scan.yaml` | Push to `main` (batched) | OpenGrep SAST, Gitleaks full‑history secrets detection, Trivy filesystem vulnerability scan |

### CD pipelines

| Pipeline | Trigger | Purpose |
|----------|---------|---------|
| `cd-deploy.yaml` | Automatic on CI success (main) | Parameterised deployment: ELT Function App and/or ML serving container. Stages: staging → production (with approval) |
| `cd-terraform.yaml` | Manual only | Apply the exact plan artifact produced by `ci-terraform` to production |

The single `cd-deploy.yaml` uses **runtime parameters** (`deployELT`, `deployML`, `modelVersion`) and conditional stages.  
When only ELT code changes, CI‑ELT triggers CD with `deployELT: true`. When a new model is registered, CI‑ML triggers CD with `deployML: true`. Both can run together without conflicts.

---

## Templates

| Template | Used by | Purpose |
|----------|---------|---------|
| `python-ci.yaml` | `ci-elt.yaml`, `ci-ml-training.yaml` | Ruff lint, mypy, pytest with coverage, Trivy FS scan, optional Docker build & push |
| `elt-deploy.yaml` | `cd-deploy.yaml` | Deploy Python Function App via `func azure functionapp publish --build remote` |
| `aca-deploy.yaml` | `cd-deploy.yaml` | Canary deployment to Azure Container Apps: new revision at 0% → k6 load test (10%) → k6 load test (50%) → promote 100% or auto‑rollback |

---

## Agent pool

All pipelines run on **Microsoft‑hosted** `ubuntu-24.04` agents.  
No private network or self‑hosted infrastructure is required – tool downloads and Azure communication use public endpoints.

---

## Key design decisions

- **Separate CI and CD** – CI is workload‑specific with path filters for fast feedback. CD is unified but conditionally executes based on parameters, avoiding duplicate pipeline logic.
- **Decoupled data and ML** – The ELT pipeline is triggered by blob events. A storage queue message from ELT can trigger ML training. Their CI/CD remains independent, reflecting real‑world team ownership.
- **Plan‑apply separation** – `ci-terraform` validates and publishes a plan artifact; `cd-terraform` applies **that exact artifact** with no re‑plan, reducing risk.
- **Trunk‑based development** – Only `main` and short‑lived `feat/*` branches exist. Environment differences are managed through `.tfvars` files, not long‑lived branches.
- **Secrets never in pipelines** – Authentication uses OIDC federation to Azure. Code uses `DefaultAzureCredential` (managed identity / workload identity federation). No connection strings or keys are stored in variables.
- **Immutable deployments** – ELT Function App publishes with `--build remote` (code‑based, no container). ML serving containers are tagged with the Git commit SHA (`$(Build.SourceVersion)`), never `latest`.
- **Serverless cost model** – Azure Functions and Container Apps scale to zero when idle. The pipelines themselves have zero infrastructure cost outside of execution minutes.

---

## Conventions

- File extension `.yaml` (not `.yml`).
- Template references use the `azure-pipelines/templates/` path from the repository root.
- All pipelines declare `pool: vmImage: ubuntu-24.04` at the top level.
- Path triggers include the pipeline definition itself and any shared templates to ensure consistency.
- Service connections use OIDC and are named `azdo-oidc-ci` (CI) and `azdo-oidc-cd` (CD).
- Storage URIs are injected via environment variables (`ELT_STORAGE__blobServiceUri`, `ELT_STORAGE__queueServiceUri`) – never connection strings.

---

## Variable groups

Each pipeline expects a corresponding variable group in Azure DevOps:

| Variable group | Contains |
|----------------|----------|
| `elt-ci-vars` | `ELT_STORAGE__blobServiceUri`, `ELT_STORAGE__queueServiceUri`, `azureServiceConnection` |
| `ml-ci-vars` | `MLFLOW_TRACKING_URI`, `containerRegistry`, `azureServiceConnection` |
| `terraform-ci-vars` | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` |
| `terraform-cd-vars` | Same as `terraform-ci-vars` |
| `deployment-vars` | `containerRegistry`, `azureServiceConnection`, `FUNCTION_APP_NAME`, `CONTAINER_APP_NAME`, staging/production resource groups |

---

## How to run

1. **Push to `src/workloads/elt/**`** → `ci-elt` runs automatically: lint → type‑check → tests → bindings validation.  
2. **Push to `src/workloads/train/**`** → `ci-ml-training` runs: lint → model tests → training → MLflow experiment logging → model registration.  
3. **Push to `src/terraform/main/**`** → `ci-terraform` runs: format check, validate, plan (plan artifact published).  
4. **Merge to `main`** – the same CI pipelines run again. On success, they trigger `cd-deploy` with appropriate parameters:  
   - ELT changes deploy to staging, then production.  
   - ML changes build a Docker image, deploy to Container Apps with canary rollout (10% → k6 test → 50% → k6 test → 100% or rollback).  
5. **Infrastructure changes** – a human with appropriate permissions manually triggers `cd-terraform` to apply the reviewed plan artifact.

---

## Security scanning

The `full_repo_security_scan.yaml` pipeline runs on every push to `main` and uses:

- **OpenGrep** – multi‑language SAST with OWASP Top Ten and Docker rulesets.
- **Gitleaks** – full git‑history secrets detection (respects `.gitleaks.toml` if present).
- **Trivy** – filesystem vulnerability and misconfiguration scan (CRITICAL severity, respects `.trivyignore`).

Tool binaries are downloaded with pinned SHAs and verified at runtime. The scan runs on a clean ephemeral agent with full repository history.

---

## Adding a new workload

1. Create a new `ci-<workload>.yaml` in `ci/` following the existing pattern (use `python-ci.yaml` template for Python workloads).  
2. In `cd-deploy.yaml`, add new parameters (e.g., `deployNewWorkload`) and a new conditional stage that uses the appropriate deployment template.  
3. Create a corresponding variable group in Azure DevOps and link it to the pipeline.  
4. Ensure the workload adheres to the project conventions:  
   - Uses `DefaultAzureCredential` for all Azure access.  
   - Configuration via environment variables, never hard‑coded secrets.  
   - If serverless, respects scale‑to‑zero design.