# Serverless End‑to‑End tabular MLOps on Azure


# Get Started

## Prerequisites

1. **Docker installed, running *without* sudo access (sudo usermod -aG docker $USER && newgrp docker)**
2. **Visual Studio Code with the Dev Containers extension installed (for a deterministic environments): [Docs](https://code.visualstudio.com/docs/devcontainers/containers)**
3. **An Azure subscription(Temporary resources, free tier or azure for students is sufficient)** with permissions to create:
   *   **Azure Container Apps** (Compute)
   *   **Azure Monitor** (Application Insights & Log Analytics Workspace)
   *   **Azure Storage Account** (Terraform State Backend + ACR)
   *   **Azure Container Registry (ACR)** (Private Endpoints)
   *   **Cosmos DB** (Serverless, Private Endpoints)
   *   **Key Vault** (Secrets Management)
   *   **Azure Functions** (HTTP-triggered serverless compute)
   *   **Identity & Access** (Microsoft Entra ID, SAMI, Workload Identity Federation, RBAC)
   *   **Azure DevOps Organization** (CI/CD Orchestration)

### PHASE 0.1: Clone the repo and build the devcontainer(Reproducible). This will take 10-20 minutes. 
```sh 
cd $HOME && rm -rf Serverless-MLOps && git clone https://github.com/Athithya-Sakthivel/Serverless-MLOps.git && cd Serverless-MLOps && code .
```
> ctrl + shift + P -> paste `Dev containers: Rebuild Container Without Cache` and enter

### PHASE 0.2 Open a new terminal and login to your gh account
```sh
git config --global user.name "Your Name"
git config --global user.email you@example.com
gh auth login

? What account do you want to log into? GitHub.com
? What is your preferred protocol for Git operations? `SSH`
? Generate a new SSH key to add to your GitHub account? `No`
? How would you like to authenticate GitHub CLI? `Login with a web browser`

! First copy your one-time code: <code>
- Press Enter to open github.com in your browser... 
✓ Authentication complete. Press Enter to continue...
```

---
### PHASE 0.3 Create a private repo in your gh account

```sh
export REPO_NAME="Serverless-MLOps-1" # or any name
git remote remove origin 2>/dev/null || true
gh repo create "$REPO_NAME" --private >/dev/null 2>&1
REMOTE_URL="https://github.com/$(gh api user | jq -r .login)/$REPO_NAME.git"
git remote add origin "$REMOTE_URL" 2>/dev/null || true
git branch -M main 2>/dev/null || true
git push -u origin main
git pull
git remote -v
echo "[INFO] A private repo '$REPO_NAME' created and pushed. Only visible from your account."
```

---
### PHASE 0.4 Log in to azure and select subscription 

```bash
az login
```

<details>
<summary>▶ Expected output</summary>

![alt text](docs/screenshots/login.png)
</details>

---

## PHASE 1.1: Azure DevOps Organization Setup (No API Automation Available)

If you do not already have an Azure DevOps organization, create one via the [official guide](https://learn.microsoft.com/en-in/azure/devops/organizations/accounts/create-organization?view=azure-devops#create-an-organization-1). Automated organization creation is unsupported; all organizations must be created manually through the web portal.

Microsoft recommends using GitHub as the primary repository and source of truth for source code, with Azure DevOps focused on CI/CD orchestration. This guide follows that recommended approach.

<details>
<summary>▶ Expected output</summary>

![alt text](docs/screenshots/azdo_pat.png)

</details>

---
## PHASE 1.2: Bootstrap Azure DevOps and Terraform Backend

This script provisions the Terraform state backend and bootstraps Azure DevOps (project, GitHub service connection, OIDC federation, service connections, and security scan pipeline). Upon success, it trigger Terraform CI pipeline and outputs the pipeline URLs. Idempotent. The bootstrap process provisions Workload Identity Federation (WIF) so downstream CI/CD pipelines authenticate via OIDC — no stored secrets, no certificates, no rotation.


```bash
export TF_VAR_AZDO_ORG_SERVICE_URL="https://dev.azure.com/<organization_name>"
export TF_VAR_AZDO_PERSONAL_ACCESS_TOKEN="<azure-devops-pat>"   # Generate at https://dev.azure.com/<organization_name>/_usersSettings/tokens
export TF_VAR_AZDO_GITHUB_SERVICE_CONNECTION_PAT="<github-pat>" # Generate at https://github.com/settings/tokens/new

bash src/terraform/bootstrap/bootstrap.sh --create
sleep 20
git add . && git commit -m "bootstrap extend" && git push origin main

```


<details>
<summary>▶ Expected outputs</summary>

!![alt text](docs/screenshots/bootstrap.png)
![alt text](docs/screenshots/allow_ci.png)

</details>

# Run main
export TF_VAR_project_name="${TF_VAR_project_name:-agentic-sre}"
bash src/terraform/main/run.sh --plan --env staging

![alt text](image.png)














