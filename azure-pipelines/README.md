# Azure Pipelines – Serverless MLOps CI/CD

Continuous integration and delivery for the **Serverless‑MLOps** system.  
Pipelines are organised by code‑change domain within a single training container: data transformation (ELT) and model training. Both run inside the same Azure Container App Job, but CI triggers are split by source path for fast feedback and independent validation.

---

## Directory structure

```
azure-pipelines/
├── ci/                          # Continuous integration pipelines
│   ├── ci-elt.yaml              # ELT code – lint, test transform logic
│   ├── ci-ml-training.yaml      # Training code – lint, test training logic
│   ├── ci-container.yaml        # Build & push container (triggered on any change)
│   ├── ci-terraform.yaml        # Infrastructure – fmt, validate, plan
│   └── full_repo_security_scan.yaml
├── cd/                          # Continuous delivery pipelines
│   ├── cd-deploy.yaml           # Deploy training container (parameterised)
│   └── cd-terraform.yaml        # Apply infrastructure plan
├── templates/                   # Reusable job templates
│   ├── python-ci.yaml           # Shared Python lint/test steps
│   └── aca-deploy.yaml          # Deploy to Azure Container Apps Job
└── README.md
```

---

## Pipeline inventory

### CI pipelines

| Pipeline | Trigger (paths) | Purpose |
|----------|-----------------|---------|
| `ci-elt.yaml` | `src/workloads/training_pipeline/elt/**`<br>`src/workloads/training_pipeline/utils/**` | Ruff lint, mypy, pytest for ELT (transform, schema validation) |
| `ci-ml-training.yaml` | `src/workloads/training_pipeline/train/**`<br>`src/workloads/training_pipeline/utils/**` | Ruff lint, mypy, pytest for training (model, evaluation) |
| `ci-container.yaml` | `src/workloads/training_pipeline/**` (any change) | Build Docker image, push to ACR with commit SHA tag |
| `ci-terraform.yaml` | `src/terraform/main/**` | `terraform fmt`, `validate`, generate plan artifact |
| `full_repo_security_scan.yaml` | Push to `main` (batched) | OpenGrep SAST, Gitleaks, Trivy vulnerability scan |

### CD pipelines

| Pipeline | Trigger | Purpose |
|----------|---------|---------|
| `cd-deploy.yaml` | Automatic on CI success (main) | Deploy training container to staging → production. Parameterised to allow separate ELT/training deployments if needed. |
| `cd-terraform.yaml` | Manual only | Apply the exact plan artifact from `ci-terraform` |

The single `cd-deploy.yaml` uses **runtime parameters** (`deployContainer`, `environment`) and conditional stages.  
On any successful CI (ELT, training, or container build), CD is triggered with `deployContainer: true`, updating the ACA Job image for the specified environment.

---

## Templates

| Template | Used by | Purpose |
|----------|---------|---------|
| `python-ci.yaml` | `ci-elt.yaml`, `ci-ml-training.yaml` | Ruff lint, mypy, pytest with coverage, Trivy FS scan |
| `aca-deploy.yaml` | `cd-deploy.yaml` | Update Azure Container App Job image; optionally trigger a run and validate MLflow metrics |

Note: No separate ELT deployment template – ELT runs inside the same container as training.

---

## Agent pool

All pipelines run on **Microsoft‑hosted** `ubuntu-24.04` agents.  
No private network or self‑hosted infrastructure is required – tool downloads and Azure communication use public endpoints.

---

## Key design decisions

- **Separate CI by domain, unified CD** – ELT and training code share a container, but CI triggers are split to validate each domain independently. CD always deploys the whole container image.
- **Container built once** – `ci-container` is triggered by any change and produces a single immutable image tagged with `$(Build.SourceVersion)`. It runs after both domain‑specific CI pipelines pass (but can be triggered independently).
- **Decoupled validation** – ELT tests run on sample data; training tests run with synthetic data and mock MLflow. Both must pass before container build.
- **Plan‑apply separation** – `ci-terraform` validates and publishes a plan artifact; `cd-terraform` applies **that exact artifact** with no re‑plan.
- **Trunk‑based development** – Only `main` and short‑lived `feat/*` branches. Environment differences via `.tfvars`.
- **Secrets never in pipelines** – Authentication uses OIDC federation. Code uses `DefaultAzureCredential` (Managed Identity / workload identity). No connection strings in variables.
- **Immutable deployments** – Container images tagged with Git commit SHA, never `latest`.
- **Serverless cost model** – Container App Jobs scale to zero. Pipelines themselves have zero infrastructure cost outside of execution minutes.

---

## Conventions

- File extension `.yaml` (not `.yml`).
- Template references use `azure-pipelines/templates/` relative path.
- All pipelines declare `pool: vmImage: ubuntu-24.04` at the top level.
- Path triggers include the pipeline definition itself and shared templates.
- Service connections use OIDC, named `azdo-oidc-ci` (CI) and `azdo-oidc-cd` (CD).
- Environment variables injected via Azure DevOps variable groups – never hard‑coded.

---

## Variable groups

| Variable group | Contains |
|----------------|----------|
| `elt-ci-vars` | `AZURE_STORAGE_ACCOUNT_NAME`, `RAW_CONTAINER_NAME`, `azureServiceConnection` (for tests) |
| `ml-ci-vars` | `MLFLOW_TRACKING_URI`, `containerRegistry`, `azureServiceConnection` |
| `container-ci-vars` | `containerRegistry`, `azureServiceConnection` (for build/push) |
| `terraform-ci-vars` | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` |
| `terraform-cd-vars` | Same as terraform‑ci |
| `deployment-vars` | `containerRegistry`, `azureServiceConnection`, `CONTAINER_APP_JOB_NAME`, staging/production resource groups |

---

## How to run

1. **Push to `src/workloads/training_pipeline/elt/`** → `ci-elt` runs: lint → type‑check → ELT unit tests.
2. **Push to `src/workloads/training_pipeline/train/`** → `ci-ml-training` runs: lint → type‑check → training tests.
3. **Push to `src/workloads/training_pipeline/` (any change)** → `ci-container` runs: build image → push to ACR.
4. **Push to `src/terraform/main/`** → `ci-terraform` runs: fmt, validate, plan.
5. **Merge to `main`** – all CI pipelines run again. On success, `cd-deploy` is triggered with `deployContainer: true`, updating the ACA Job in staging. After manual approval, promote to production.
6. **Infrastructure changes** – human manually triggers `cd-terraform` to apply the approved plan.

---

## Security scanning

The `full_repo_security_scan.yaml` pipeline runs on every push to `main` and uses:

- **OpenGrep** – SAST with OWASP Top Ten and Docker rulesets.
- **Gitleaks** – full‑history secrets detection.
- **Trivy** – filesystem vulnerability and misconfiguration scan (CRITICAL severity).

Tool binaries are downloaded with pinned SHAs and verified at runtime. The scan runs on a clean ephemeral agent with full repository history.

---

## Adding a new workload

1. Place new code under `src/workloads/<new-workload>/`.
2. Create a new CI pipeline `ci-<workload>.yaml` with appropriate path filters.
3. If it affects the training container, extend `ci-container.yaml` to include the new paths.
4. Update `cd-deploy.yaml` parameters if the new workload requires separate deployment logic.
5. Create a corresponding variable group in Azure DevOps.
6. Ensure the workload follows conventions: `DefaultAzureCredential`, environment variables, scale‑to‑zero if serverless.