# Azure Pipelines – Serverless MLOps CI/CD

Continuous integration and delivery for the **Serverless‑MLOps** system.  
Pipelines are organised by domain: data transformation (ELT), model training, model
serving, and infrastructure. Training and serving code are validated independently
but share infrastructure where appropriate.

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

## Pipeline inventory

### CI pipelines – code validation only (no Docker build, no deployment)

| Pipeline | Trigger (paths) | Purpose |
|----------|-----------------|---------|
| `ci-elt.yaml` | `src/workloads/training_pipeline/elt/**`<br>`src/workloads/training_pipeline/utils/**` | Ruff lint, basedpyright, **ELT unit tests only** |
| `ci-ml-training.yaml` | `src/workloads/training_pipeline/train/**`<br>`src/workloads/training_pipeline/utils/**`<br>`src/workloads/training_pipeline/tests/**` | Ruff lint, basedpyright, **all 56 tests** (ELT + training) |
| `ci-service.yaml` | `src/workloads/serving/**` | Ruff lint, basedpyright, pytest for serving app |
| `ci-terraform.yaml` | `src/terraform/main/**` | `tofu fmt`, validate, plan; publishes plan artifact (audit only) |
| `full_repo_security_scan.yaml` | Push to `main` (batched) | OpenGrep SAST, Gitleaks secrets, Trivy vulnerability scan |

All Python CI pipelines use the reusable `python-ci.yaml` template, which also runs
`pip-audit` and a Trivy filesystem scan (HIGH/CRITICAL severity). No Docker images
are built in CI – that is left to the CD pipelines.

### CD pipelines – build, scan, deploy

| Pipeline | Trigger | Purpose |
|----------|---------|---------|
| `cd-training-job.yaml` | **Both** `ci-elt` **and** `ci-ml-training` success on `main` | Build training container image (ELT + training code), scan with Trivy, push to ACR, update ACA Job in staging; after manual approval, promote to production |
| `cd-service.yaml` | `ci-service` success on `main`, **or** manual trigger (model promotion) | Build serving image (code changes only), or update model version via environment variable. Performs **canary deployment** using `src/scripts/canary-deploy.sh` (revision‑based traffic splitting, k6 load tests, automatic rollback). |
| `cd-terraform.yaml` | **Manual only** | Apply the exact plan artifact from `ci-terraform`. Fetches `azdo-pat` from Key Vault. |

## Templates

| Template | Used by | Purpose |
|----------|---------|---------|
| `python-ci.yaml` | all `ci-*` Python pipelines | Ruff, basedpyright, pytest, pip‑audit, Trivy FS scan |
| `aca-deploy.yaml` | `cd-training-job.yaml` only | Update Azure Container App Job image |

The serving CD pipeline calls an external script (`canary-deploy.sh`) rather than
using a heavy YAML template – this keeps the pipeline definition minimal and all
complex logic testable outside Azure DevOps.

## Agent pool

All pipelines run on **Microsoft‑hosted** `ubuntu-24.04` agents.  
No private network or self‑hosted infrastructure is required.

## Variable groups & secrets

### Variable group (non‑secrets)

A single variable group **`sm-all-vars`** is populated by Terraform (module
`azure_devops`) and contains every non‑secret configuration value the pipelines
need:

- `AZURE_STORAGE_ACCOUNT_NAME`
- `MLFLOW_TRACKING_URI`
- `RAW_CONTAINER_NAME`, `CLEAN_CONTAINER_NAME`, `CHECKPOINT_CONTAINER_NAME`
- `containerRegistry`
- `CONTAINER_APP_JOB_NAME`, `CONTAINER_APP_NAME`
- `STAGING_RG`, `PROD_RG`
- `azureServiceConnection`

No secrets are stored in variable groups.

### Secrets

The only persistent secret in the system is the **Azure DevOps PAT**, stored in
**Azure Key Vault** as `azdo-pat`. Both CI and CD pipelines that require the PAT
(e.g., `ci-terraform`, `cd-terraform`) fetch it at runtime using the
`AzureKeyVault@2` task:

```yaml
- task: AzureKeyVault@2
  inputs:
    azureSubscription: 'azdo-oidc-cd'
    KeyVaultName: $(kvName)
    SecretsFilter: 'azdo-pat'
```

The PAT is then injected into the task’s environment via an `env:` block.
It never appears in logs or pipeline definitions.

All Azure‑to‑Azure authentication uses **OIDC federation** – no client secrets
or connection strings are stored anywhere.

## Key design decisions

- **Separate CI by domain** – ELT, training, and serving code are validated
  independently with path‑specific triggers for fast feedback.
- **One CD per domain** – Both ELT and training changes trigger the same training
  CD (they share one container image). Serving has its own CD. Infrastructure
  CD is manual.
- **Docker build in CD only** – CI never builds images. CD builds, scans, and
  deploys.
- **Canary deployments for serving** – A separate script (`canary-deploy.sh`)
  creates a new revision at 0 % traffic, validates it in isolation using the
  revision’s private FQDN and k6 load tests, then gradually shifts traffic.
  Automatic rollback if any threshold is violated.
- **Model updates without image rebuild** – New model versions are deployed by
  updating the `MODEL_VERSION` environment variable on the serving Container App.
  The serving image itself only changes when serving code changes.
- **Immutable deployments** – Container images tagged with Git commit SHA, never
  `latest`.
- **Plan‑apply separation** – `ci-terraform` validates and publishes a plan
  artifact; `cd-terraform` applies that exact artifact with no re‑plan.
- **Trunk‑based development** – Only `main` and short‑lived `feat/*` branches.
  Environment differences via `.tfvars`.
- **Secrets in Key Vault, config in variable group** – Clear boundary between
  sensitive and non‑sensitive values.
- **Serverless cost model** – Container App Jobs and Apps scale to zero.
  Pipelines have zero infrastructure cost outside of execution minutes.

## How to run

1. **Push to ELT or training code** → `ci-elt` and/or `ci-ml-training` run.
2. **Push to serving code** → `ci-service` runs.
3. **Push to Terraform code** → `ci-terraform` runs (fmt, validate, plan).
4. **Merge to `main`** – all affected CI pipelines run again. On success:
   - Training CD builds the training image and updates the ACA Job.
   - Serving CD builds the serving image (if code changed) and runs canary deployment.
5. **Infrastructure changes** – human manually triggers `cd-terraform`.
6. **Model promotion** – human (or automation) manually triggers `cd-service`
   with an updated `MODEL_VERSION`. The canary script validates the new model
   and rolls it out safely.

## Security scanning

The `full_repo_security_scan.yaml` pipeline runs on every push to `main` and uses:

- **OpenGrep** – SAST (OWASP Top Ten, Docker, secrets).
- **Gitleaks** – full‑history secrets detection.
- **Trivy** – filesystem vulnerability and misconfiguration scan (CRITICAL only).

Tool binaries are downloaded with pinned versions and verified at runtime. The
scan runs on a clean ephemeral agent with full repository history.

## Adding a new workload

1. Place new code under `src/workloads/<new-workload>/`.
2. Create a CI pipeline `ci-<workload>.yaml` with path filters.
3. Create a CD pipeline `cd-<workload>.yaml` (reuse `aca-deploy.yaml` for simple
   updates, or add a dedicated canary script if needed).
4. Add required variables to `sm-all-vars` (via Terraform).
5. Update this README.
6. Ensure the workload follows conventions: `DefaultAzureCredential`, environment
   variables, scale‑to‑zero if serverless.
