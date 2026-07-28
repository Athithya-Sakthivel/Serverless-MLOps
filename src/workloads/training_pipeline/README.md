# Training Pipeline — Serverless MLOps on Azure

The training pipeline is a **serverless, event‑driven** workflow that turns raw ACS (American Community Survey) data into a trained LightGBM classifier, exports it to ONNX, logs everything to MLflow, and automatically registers the model if it passes **quality and performance thresholds**. It then triggers a canary deployment of the serving API. The pipeline runs as an **Azure Container Apps Job** triggered by **Event Grid** when a new raw Parquet file lands in Azure Blob Storage.

The pipeline is **idempotent** – it can be retried safely without duplicating work – and **fully environment‑agnostic** – the same container image works locally, in CI, and in production.

---

## High‑level Architecture

```
Event Grid (new raw blob)
        │
        ▼
Azure Queue ──► ACA Job starts (main.py)
        │
        ▼
  ┌─────────────────────────┐
  │        ELT Phase        │
  │ extract → validate      │
  │ → transform → load      │
  └───────────┬─────────────┘
              │ clean parquet
              ▼
  ┌─────────────────────────┐
  │     Training Phase      │
  │ dataset → features      │
  │ → train → evaluate      │
  │ → ONNX + benchmark      │
  │ → register (if gates)   │
  └───────────┬─────────────┘
              │
              ▼
   MLflow / Azure ML Workspace
   (metrics, model, ONNX)
              │
              ▼
   REST API call to Azure DevOps
   (triggers serving canary)
```

Both phases are guarded by **checkpoints** stored in Azure Blob Storage. If a phase has already completed (`COMPLETED`), it is skipped on subsequent runs.

---

## Directory Layout (inside the container)

```
src/workloads/training_pipeline/
├── main.py                  # ACA Job entrypoint
├── local_runner.py          # Local CLI for debugging (--elt, --train, --full)
├── requirements.txt         # Single source of truth for all dependencies
├── Dockerfile
│
├── elt/                     # Extract, Validate, Transform, Load
│   ├── extract.py           # Download raw parquet from Azure Blob
│   ├── validate.py          # Schema, null, range checks
│   ├── transform.py         # Clean & standardise data
│   └── load.py              # Write clean parquet + ELT checkpoint
│
├── train/                   # Model training, evaluation & export
│   ├── dataset.py           # Load, target creation, stratified split
│   ├── features.py          # Feature engineering (numeric + one-hot state)
│   ├── model.py             # LightGBM classifier training
│   ├── evaluate.py          # Quality metrics (AUC, F1, confusion matrix, …)
│   ├── export.py            # LightGBM → ONNX conversion, verification & benchmarking
│   ├── register.py          # MLflow model registration (aliases)
│   ├── checkpoint.py        # Training checkpoint read/write
│   └── orchestrator.py      # Glues everything together, handles MLflow, triggers serving CD
│
├── utils/                   # Shared helpers
│   ├── config.py            # All configuration from environment variables (incl. performance thresholds)
│   ├── storage.py           # Azure Blob client + upload/download
│   ├── logging.py           # Structured JSON logging
│   ├── mlflow.py            # MLflow tracking URI setup
│   └── timing.py            # UTC timestamps & timing context managers
│
└── tests/                   # 56 pytest tests (unit + integration)
    ├── conftest.py          # Session‑wide Azure credential mock (for offline tests)
    ├── fakes.py             # In‑memory fake BlobServiceClient
    └── test_*.py            # Per‑module tests
```

---

## Runtime Control Flow (step by step)

### 1. Trigger

- A new Parquet file is uploaded to the `raw/` container in Azure Storage.
- **Event Grid** detects the blob and pushes an event to a **Storage Queue**.
- The **ACA Job** (`acaj-train-stg`) scales from zero and starts a container with the environment variable `INPUT_BLOB_NAME` (or `RAW_BLOB_NAME`) set to the blob path.

### 2. `main.py` — Entrypoint

- `AppConfig.from_env()` reads all configuration from environment variables (fail‑fast if mandatory ones are missing).
- `resolve_input_blob_name()` picks up the blob name from the environment.
- The **ELT phase** is executed via `_run_elt()`.
- The **Training phase** is executed via `run_training_pipeline()`.
- Both phases return the final status; the container exits with 0 on success.

### 3. ELT Phase (`_run_elt`)

1. **Read ELT checkpoint** – If a checkpoint with status `"COMPLETED"` already exists for this raw blob, ELT is skipped and the clean blob name is returned immediately.
2. **Extract** – Downloads the raw blob, loads it with Polars.
3. **Validate** – Checks required columns, types, null rates, duplicates, state codes, value ranges. Raises `ValidationError` on hard failures.
4. **Transform** – Normalises state codes, coerces types, removes rows with nulls/invalid values, deduplicates.
5. **Load** – Writes the clean DataFrame as a Parquet blob to the `clean/` container.
6. **Write ELT checkpoint** – Writes a JSON blob (status `"COMPLETED"`, metrics, timestamps) to `checkpoints/elt/<blob>.json`.
7. Returns the clean blob name.

### 4. Training Phase (`run_training_pipeline`)

1. **Read training checkpoint** – If a checkpoint with status `"COMPLETED"` already exists, returns immediately with the stored MLflow run ID, metrics, etc.
2. **Write `"RUNNING"` checkpoint** – Marks the training as in progress.
3. **Data preparation** (inside an MLflow run):
   - Loads clean data, creates binary target, performs stratified 70/15/15 split.
   - Builds feature bundle (numeric + one-hot state) and transforms all splits.
4. **Class‑balance guard** – If the training split has only one class, a `RuntimeError` is raised immediately.
5. **Model training** – LightGBM classifier trained with early stopping on validation AUC.
6. **Evaluation** – AUC, F1, precision, recall, accuracy, confusion matrix, and prediction latency computed for validation and test sets.
7. **MLflow logging** – Metrics, parameters, and tags (git SHA, pipeline run ID, etc.) logged to the Azure ML workspace.
8. **Model artifact** – The trained model is saved with `joblib` and logged as a MLflow artifact.
9. **ONNX export + performance benchmark** – The model is converted to ONNX, verified (max probability delta < 1e‑3), and benchmarked for:
   - Single‑row inference latency (p50, p95, p99)
   - Throughput (rows/second)
   Performance metrics are logged to MLflow.
10. **Registration gating** – The model is registered **only if**:
    - **Quality thresholds** are met on the test set (AUC, F1, precision, recall).
    - **Performance thresholds** are met (p95 latency ≤ threshold, throughput ≥ threshold) – enabled by default.
    - `ENABLE_MODEL_REGISTRATION` is `true`.
    On success, the model is registered and the `Staging` alias is assigned.
11. **Trigger serving CD** – If registration succeeded, the training orchestrator calls the Azure DevOps REST API to trigger the `cd-service` pipeline, passing the new model version. This starts a canary deployment without rebuilding the serving image.
12. **Write `"COMPLETED"` checkpoint** – All results (run ID, metrics, ONNX SHA256, performance, registration details) are persisted to `checkpoints/training/<blob>.json`.
13. If any exception occurs, a `"FAILED"` checkpoint is written (best‑effort) and the exception is re‑raised so the ACA job reports failure.

### 5. Idempotency & Recovery

- **ELT checkpoint**: `"COMPLETED"` means the clean data is present and valid.
- **Training checkpoint**: `"COMPLETED"` means the model was trained, evaluated, ONNX‑exported, benchmarked, and (if eligible) registered.
- `"RUNNING"` state indicates a previous run died; the next trigger will overwrite it and start fresh.
- `"FAILED"` state records the error details; retries will re‑run the entire phase.

Because the ACA Job processes messages from a queue, at‑most‑once delivery and checkpointing guarantee exactly‑once processing.

---

## Configuration

All settings are read from environment variables. The following are the most important ones:

| Variable | Description | Default |
|----------|-------------|---------|
| `AZURE_STORAGE_ACCOUNT_NAME` | Storage account for raw/clean/checkpoints | **Required** |
| `MLFLOW_TRACKING_URI` | Azure ML tracking URI (e.g., `azureml://…`) | **Required** |
| `INPUT_BLOB_NAME` | The raw blob name (alternative: `RAW_BLOB_NAME`) | **Required** |
| `TRAINING_TARGET_INCOME_THRESHOLD` | Income threshold for binary target | 50000 |
| `TRAIN_RANDOM_SEED` | Seed for reproducibility | 42 |
| `TRAIN_FRACTION` | Fraction for training split | 0.70 |
| `VALIDATION_FRACTION` | Fraction for validation split | 0.15 |
| `TEST_FRACTION` | Fraction for test split | 0.15 |
| `ENABLE_MODEL_REGISTRATION` | `"true"` to register the model | `false` |
| `MODEL_NAME` | Registered model name | `acs_income_classifier` |
| `MIN_AUC`, `MIN_F1`, … | Quality promotion thresholds | 0.90, 0.85, … |
| `MAX_P95_LATENCY_MS` | Maximum allowed p95 ONNX latency | 5.0 |
| `MIN_THROUGHPUT_ROWS_PER_SEC` | Minimum ONNX throughput | 1000 |
| `ENABLE_PERFORMANCE_GATE` | Enforce performance thresholds | `true` |
| LightGBM hyperparameters | `LIGHTGBM_*` (see `utils/config.py`) | sensible defaults |

No hard‑coded secrets. Authentication always uses `DefaultAzureCredential` (works with `az login` locally, managed identity in ACA, and OIDC in CI).

---

## Local Development

### Prerequisites

- Python 3.14
- Azure CLI (`az login`) with **Storage Blob Data Contributor** role on the storage account
- Virtual environment with dependencies installed (`pip install -r requirements.txt`)

### Run the full pipeline locally (real Azure)

Use the provided `test_e2e_locally.sh` script. It exports all needed environment variables, uploads a test blob, and runs `python main.py` — identical to the ACA job.

```bash
# One-time: grant yourself Storage Blob Data Contributor
az role assignment create --assignee $(az ad signed-in-user show --query id -o tsv) \
  --role "Storage Blob Data Contributor" \
  --scope "/subscriptions/.../storageAccounts/smstgartifactsf41930"

# Run the pipeline
bash src/workloads/training_pipeline/test_e2e_locally.sh --force        # fresh start
bash src/workloads/training_pipeline/test_e2e_locally.sh                # idempotent – skips already done work
```

---

## CI/CD Automation (Azure DevOps)

### CI Pipelines

**`ci-elt.yaml`** – Fast feedback for data‑engineering changes  
- Triggered by changes under `elt/**`, `utils/**`, `tests/test_elt.py`, `main.py`.  
- Runs: ruff → basedpyright → pytest (ELT tests only).  

**`ci-ml-training.yaml`** – Full validation for model training changes  
- Triggered by changes under `train/**`, `utils/**`, `tests/**`, `main.py`.  
- Runs: ruff → basedpyright → pytest (all 56 tests).  

Both pipelines use the reusable template `python-ci.yaml` (which includes Trivy FS scan and pip‑audit). No Docker build is performed in CI.

### CD Pipeline

**`cd-training-job.yaml`** – Builds the training image and deploys it to the ACA Job.  
- Triggered automatically when **either** `ci-elt` **or** `ci-ml-training` succeeds on `main`.  
- Builds the Docker image, pushes it to ACR with an immutable tag.  
- Deploys to **Staging** (`acaj-train-stg`).  
- After manual approval, deploys to **Production** (`acaj-train-prod`).  

All configuration (resource groups, job names, ACR) comes from the `sm-all-vars` variable group (populated by Terraform). Secrets (like the Azure DevOps PAT) are fetched from Key Vault at runtime.

### Model Promotion Chain

After a successful training run that passes both quality and performance gates, the training orchestrator **automatically**:
1. Registers the model in Azure ML with the `Staging` alias.
2. Sends a REST API call to Azure DevOps to trigger the **serving CD pipeline** (`cd-service`), passing the new model version.

The serving CD then performs a canary deployment (private FQDN validation, gradual traffic shift, k6 tests, automatic rollback). No manual intervention is needed – the model goes from training to production safely and automatically.

---

## Failure Modes & Resilience

- **Missing environment variables**: `AppConfig.from_env()` fails immediately with a clear error message.
- **Single‑class training labels**: Detected by a class‑balance guard and raised as `RuntimeError` before training.
- **Azure authentication failure**: Caught by storage helpers and wrapped in `RuntimeError`; the checkpoint is marked `FAILED`.
- **MLflow network issues**: Propagated as exceptions; the orchestrator writes a `FAILED` checkpoint and re‑raises.
- **ONNX verification failure**: If the max delta exceeds 1e‑3, a `RuntimeError` is raised, preventing an invalid model from being registered.
- **Performance threshold violation**: If the ONNX model is too slow or low‑throughput, registration is skipped (with a clear tag in MLflow), but the run itself succeeds.
- **Corrupted checkpoints**: Empty or invalid JSON is treated as “no checkpoint” and the phase re‑runs.

All errors are logged as structured JSON (including tracebacks) for easy querying in Azure Log Analytics.

---

## Dependencies (simplified)

- **Data processing**: `polars`, `numpy`
- **Machine learning**: `lightgbm`, `scikit-learn`, `joblib`
- **Model export**: `onnx`, `onnxmltools`, `onnxruntime`
- **Tracking**: `mlflow`, `azureml-mlflow`
- **Infrastructure**: `azure-identity`, `azure-storage-blob`
- **Testing / Linting**: `pytest`, `ruff`, `basedpyright`, `pip-audit`

Everything is pinned in `requirements.txt`.

---

## Summary

The training pipeline is a complete, production‑grade MLOps workflow. It runs on a serverless Azure Container Apps Job, is fully idempotent, and automatically promotes only models that meet strict quality and performance thresholds. It seamlessly triggers a canary deployment of the serving API, creating a fully automated, safe, and observable machine learning delivery pipeline.