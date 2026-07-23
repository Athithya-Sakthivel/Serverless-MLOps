# Azure Pipelines – Serverless MLOps CI/CD

Continuous integration and delivery for the **Serverless‑MLOps** system.  
Pipelines are organised by code‑change domain: data transformation (ELT), model training, and model serving. Training and serving share infrastructure but are validated and deployed independently.

---

## Directory structure

```
azure-pipelines/
├── ci/
│   ├── ci-elt.yaml
│   ├── ci-ml-training.yaml
│   ├── ci-service.yaml
│   ├── ci-terraform.yaml
│   └── full_repo_security_scan.yaml
├── cd/
│   ├── cd-training-job.yaml
│   ├── cd-service.yaml
│   └── cd-terraform.yaml
├── templates/
│   ├── python-ci.yaml
│   └── aca-deploy.yaml
└── README.md
```

---

## Pipeline inventory

### CI pipelines

| Pipeline | Trigger (paths) | Purpose |
|----------|-----------------|---------|
| `ci-elt.yaml` | `src/workloads/training_pipeline/elt/**`<br>`src/workloads/training_pipeline/utils/**` | Ruff lint, basedpyright, pytest for ELT |
| `ci-ml-training.yaml` | `src/workloads/training_pipeline/train/**`<br>`src/workloads/training_pipeline/utils/**` | Ruff lint, basedpyright, pytest for training |
| `ci-service.yaml` | `src/workloads/serving/**` | Ruff lint, basedpyright, pytest for serving app |
| `ci-terraform.yaml` | `src/terraform/main/**` | tofu fmt, validate, plan |
| `full_repo_security_scan.yaml` | Push to `main` (batched) | SAST, secrets detection, vulnerability scan |

### CD pipelines

| Pipeline | Trigger | Purpose |
|----------|---------|---------|
| `cd-training-job.yaml` | CI success on `main` (training paths) | Deploy training container to ACA Job |
| `cd-service.yaml` | CI success on `main` (serving paths) | Deploy serving container to ACA App |
| `cd-terraform.yaml` | Manual only | Apply the exact plan artifact from `ci-terraform` |

---

## Templates

| Template | Used by | Purpose |
|----------|---------|---------|
| `python-ci.yaml` | `ci-elt.yaml`, `ci-ml-training.yaml`, `ci-service.yaml` | Ruff lint, basedpyright type-check, pytest, Trivy FS scan |
| `aca-deploy.yaml` | `cd-training-job.yaml`, `cd-service.yaml` | Update Azure Container App Job/App image |

---

## Agent pool

All pipelines run on **Microsoft‑hosted** `ubuntu-24.04` agents.  
No private network or self‑hosted infrastructure is required.

---

## Key design decisions

- **Separate CI by domain** – ELT, training, and serving code are validated independently with path‑specific triggers for fast feedback.
- **Separate CD per workload** – Training job and serving app deploy independently. A training code change does not redeploy the serving container, and vice versa.
- **Immutable deployments** – Container images tagged with Git commit SHA, never `latest`.
- **Plan‑apply separation** – `ci-terraform` validates and publishes a plan artifact; `cd-terraform` applies that exact artifact with no re‑plan.
- **Trunk‑based development** – Only `main` and short‑lived `feat/*` branches. Environment differences via `.tfvars`.
- **Secrets never in pipelines** – Authentication uses OIDC federation. Code uses `DefaultAzureCredential` (Managed Identity / workload identity). No connection strings in variables.
- **Serverless cost model** – Container App Jobs and Apps scale to zero. Pipelines themselves have zero infrastructure cost outside of execution minutes.

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
| `elt-ci-vars` | `AZURE_STORAGE_ACCOUNT_NAME`, `MLFLOW_TRACKING_URI`, container names, `azureServiceConnection` |
| `ml-ci-vars` | `MLFLOW_TRACKING_URI`, `containerRegistry`, `azureServiceConnection` |
| `service-ci-vars` | `containerRegistry`, `azureServiceConnection` |
| `terraform-ci-vars` | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` |
| `terraform-cd-vars` | Same as terraform‑ci |
| `deployment-vars` | `containerRegistry`, `azureServiceConnection`, `CONTAINER_APP_JOB_NAME`, `CONTAINER_APP_NAME`, staging/production resource groups |

---

## How to run

1. **Push to `src/workloads/training_pipeline/elt/`** → `ci-elt` runs: lint → type‑check → ELT unit tests.
2. **Push to `src/workloads/training_pipeline/train/`** → `ci-ml-training` runs: lint → type‑check → training tests.
3. **Push to `src/workloads/serving/`** → `ci-service` runs: lint → type‑check → serving tests.
4. **Push to `src/terraform/main/`** → `ci-terraform` runs: fmt, validate, plan.
5. **Merge to `main`** – all affected CI pipelines run again. On success, corresponding CD pipelines deploy to staging. After manual approval, promote to production.
6. **Infrastructure changes** – human manually triggers `cd-terraform` to apply the approved plan.

---

## Security scanning

The `full_repo_security_scan.yaml` pipeline runs on every push to `main` and uses:

- **Gitleaks** – full‑history secrets detection.
- **Trivy** – filesystem vulnerability and misconfiguration scan (HIGH, CRITICAL severity).
- **pip‑audit** – Python dependency vulnerability audit.

Tool binaries are downloaded with pinned versions and verified at runtime. The scan runs on a clean ephemeral agent with full repository history.

---

## Adding a new workload

1. Place new code under `src/workloads/<new-workload>/`.
2. Create a new CI pipeline `ci-<workload>.yaml` with appropriate path filters.
3. Create a new CD pipeline `cd-<workload>.yaml` using the `aca-deploy.yaml` template.
4. Create a corresponding variable group in Azure DevOps.
5. Update this README.
6. Ensure the workload follows conventions: `DefaultAzureCredential`, environment variables, scale‑to‑zero if serverless.