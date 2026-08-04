#!/usr/bin/env python3
"""
live_training_pipeline.py  –  End‑to‑end training pipeline observability.

Uploads a slice of the ACS dataset (unless a blob was uploaded within the
last 10 minutes) and traces every step:
    Blob → Event Grid → Function App → ACA Job → container logs

All resource names are derived deterministically from the subscription ID
and environment – no Terraform outputs needed.

Usage:
    python3 src/scripts/live_training_pipeline.py --env staging
    python3 src/scripts/live_training_pipeline.py --env staging --upload --wait 600
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str], check: bool = False, silent: bool = True) -> subprocess.CompletedProcess:
    if not silent:
        print(f"  [RUN] {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  [FAIL] {result.stderr.strip()}", file=sys.stderr)
        sys.exit(result.returncode)
    return result


def az(*args: str, check: bool = False, silent: bool = True) -> subprocess.CompletedProcess:
    return run(["az", *args], check=check, silent=silent)


def az_output(cmd: list[str]) -> str:
    res = run(cmd, check=True, silent=True)
    return res.stdout.strip()


def resolve_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# deterministic names (match locals.tf)
# ---------------------------------------------------------------------------


def derive_names(subscription_id: str, environment: str) -> dict[str, str]:
    project_abbr = "sm"
    sub_suffix = subscription_id[-6:]
    env_abbr = "stg" if environment == "staging" else "prod"

    return {
        "resource_group": f"rg-{project_abbr}-artifacts-{env_abbr}",
        "storage_name": f"{project_abbr}{env_abbr}artifacts{sub_suffix}",
        "function_name": f"func-blob-trigger-{env_abbr}",
        "train_job_name": f"acaj-train-{env_abbr}",
        "log_analytics_name": f"law-{project_abbr}-{env_abbr}",
        "event_sub_name": f"func-blob-trigger-{environment}-blob-created",
    }


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="End‑to‑end training pipeline observability")
    parser.add_argument("--env", default="staging", choices=["staging", "prod"])
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--wait", type=int, default=420)
    parser.add_argument("--poll-interval", type=int, default=20)
    parser.add_argument(
        "--skip-recent-upload",
        type=int,
        default=10,
        help="Skip upload if a blob was uploaded within N minutes (default 10)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()

    for tool in ("az",):
        if subprocess.run(["which", tool], capture_output=True).returncode != 0:
            print(f"[ERROR] {tool} is not installed.", file=sys.stderr)
            sys.exit(1)

    repo_root = resolve_repo_root()

    print("=== Resolving infrastructure names ===")
    try:
        sub_id = az_output(["az", "account", "show", "--query", "id", "-o", "tsv"])
    except Exception:
        print("[ERROR] Unable to determine subscription ID. Run 'az login'.", file=sys.stderr)
        sys.exit(1)

    names = derive_names(sub_id, args.env)

    rg = names["resource_group"]
    storage = names["storage_name"]
    func = names["function_name"]
    job = names["train_job_name"]
    law_name = names["log_analytics_name"]
    event_sub = names["event_sub_name"]

    # workspace GUID for Log Analytics queries
    try:
        workspace_customer_id = az_output(
            [
                "az",
                "monitor",
                "log-analytics",
                "workspace",
                "show",
                "-g",
                rg,
                "-n",
                law_name,
                "--query",
                "customerId",
                "-o",
                "tsv",
            ]
        )
    except Exception:
        print("[ERROR] Could not retrieve Log Analytics workspace customerId.", file=sys.stderr)
        sys.exit(1)

    storage_id = f"/subscriptions/{sub_id}/resourceGroups/{rg}/providers/Microsoft.Storage/storageAccounts/{storage}"
    event_sub_resource = (
        f"{storage_id}/providers/Microsoft.EventGrid/eventSubscriptions/{event_sub}"
    )

    print(f"  Subscription   : {sub_id}")
    print(f"  Resource Group : {rg}")
    print(f"  Storage        : {storage}")
    print(f"  Function App   : {func}")
    print(f"  Training Job   : {job}")
    print(f"  Log Analytics  : {workspace_customer_id}")
    print(f"  Event Sub      : {event_sub}")

    # ----- optional blob upload (idempotent) ---------------------------------
    if args.upload:
        recent_blob = False
        try:
            blobs_json = az_output(
                [
                    "az",
                    "storage",
                    "blob",
                    "list",
                    "--container-name",
                    "raw",
                    "--prefix",
                    "monthly/",
                    "--account-name",
                    storage,
                    "--auth-mode",
                    "login",
                    "--query",
                    "[].properties.creationTime",
                    "-o",
                    "json",
                ]
            )
            times = json.loads(blobs_json) if blobs_json else []
            if times:
                latest = max(t for t in times if t)
                latest_dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
                if datetime.now(UTC) - latest_dt < timedelta(minutes=args.skip_recent_upload):
                    print(
                        f"\n[SKIP] Blob uploaded {latest_dt:%H:%M:%S} UTC "
                        f"(<{args.skip_recent_upload}min ago). Skipping upload."
                    )
                    recent_blob = True
        except Exception:
            pass

        if not recent_blob:
            print("\n=== Uploading ACS dataset ===")
            upload_script = repo_root / "src" / "scripts" / "simulate_data_upload.py"
            if not upload_script.exists():
                print(f"[ERROR] Upload script not found: {upload_script}", file=sys.stderr)
                sys.exit(1)

            env = os.environ.copy()
            env["ARTIFACTS_STORAGE_ACC_NAME"] = storage
            env.setdefault("MAX_DATASET_ROWS", "1000000")

            print(f"  Storage account: {storage}")
            print(f"  Max rows: {env['MAX_DATASET_ROWS']}")
            print(f"  HF_TOKEN: {'present' if 'HF_TOKEN' in env else 'not set'}")

            try:
                subprocess.run(["python3", str(upload_script)], env=env, check=True, text=True)
            except subprocess.CalledProcessError:
                print("[ERROR] Blob upload failed. See above.", file=sys.stderr)
                sys.exit(1)
    else:
        print("\n[SKIP] Use --upload to upload a blob.")

    # ----- verify Event Grid subscription endpoint --------------------------
    try:
        endpoint_url = az_output(
            [
                "az",
                "eventgrid",
                "event-subscription",
                "show",
                "--name",
                event_sub,
                "--source-resource-id",
                storage_id,
                "--include-full-endpoint-url",
                "true",
                "--query",
                "destination.endpointUrl",
                "-o",
                "tsv",
            ]
        )
        if "functionName=Host.Functions.blob_created" not in endpoint_url:
            print(f"[WARNING] Webhook URL may be missing functionName parameter: {endpoint_url}")
        else:
            print("\nEvent Grid webhook endpoint verified.")
    except Exception:
        print("\n[WARNING] Could not verify Event Grid endpoint URL.")

    # ----- monitoring loop ----------------------------------------------------
    print(f"\n=== Monitoring (max {args.wait}s) ===")
    start = time.time()
    deadline = start + args.wait
    job_exec_name: str | None = None

    while time.time() < deadline:
        elapsed = int(time.time() - start)
        print(f"\n--- T+{elapsed}s ---")

        # 1. Event Grid metrics
        try:
            delivered_raw = az_output(
                [
                    "az",
                    "monitor",
                    "metrics",
                    "list",
                    "--resource",
                    event_sub_resource,
                    "--metric",
                    "DeliverySuccessCount",
                    "--aggregation",
                    "Total",
                    "--interval",
                    "PT1H",
                    "--query",
                    "value[0].timeseries[0].data[-1].total",
                    "-o",
                    "tsv",
                ]
            )
            delivered = int(float(delivered_raw)) if delivered_raw else 0
            print(f"  Event Grid delivered (1h): {delivered}")
        except Exception:
            print("  Event Grid metrics: unavailable")

        # 2. Function traces (using AppTraces table in Log Analytics)
        try:
            kql = (
                "AppTraces "
                "| where TimeGenerated > ago(10m) "
                "| where Message contains 'Blob trigger' or Message contains 'ACA job' "
                "| project TimeGenerated, Message "
                "| order by TimeGenerated desc "
                "| take 10"
            )
            traces_json = az_output(
                [
                    "az",
                    "monitor",
                    "log-analytics",
                    "query",
                    "--workspace",
                    workspace_customer_id,
                    "--analytics-query",
                    kql,
                    "-o",
                    "json",
                ]
            )
            traces = json.loads(traces_json) if traces_json else []
            if traces:
                print(f"  Function traces: {len(traces)} entries")
                for row in traces[:3]:
                    print(f"    {row.get('TimeGenerated', '')} | {row.get('Message', '')}")
            else:
                print("  Function traces: none yet")
        except Exception:
            print("  Function traces: query failed")

        # 3. ACA job executions
        try:
            exec_json = az_output(
                [
                    "az",
                    "containerapp",
                    "job",
                    "execution",
                    "list",
                    "-g",
                    rg,
                    "-n",
                    job,
                    "-o",
                    "json",
                ]
            )
            executions = json.loads(exec_json) if exec_json else []
        except Exception:
            executions = []

        if executions:
            latest = max(executions, key=lambda e: e.get("properties", {}).get("startTime", ""))
            status = latest["properties"]["status"]
            name = latest["name"]
            print(f"  Latest execution: {name} ({status})")
            if status in ("Succeeded", "Failed"):
                job_exec_name = name
                break
        else:
            print("  No executions yet")

        time.sleep(args.poll_interval)

    # ----- final logs ---------------------------------------------------------
    print("\n=== Final evidence ===")

    if job_exec_name:
        try:
            kql = (
                "ContainerAppConsoleLogs_CL "
                f"| where ContainerGroupName_s startswith '{job_exec_name}' "
                "| project TimeGenerated, Log_s "
                "| order by TimeGenerated asc "
                "| take 200"
            )
            logs_json = az_output(
                [
                    "az",
                    "monitor",
                    "log-analytics",
                    "query",
                    "--workspace",
                    workspace_customer_id,
                    "--analytics-query",
                    kql,
                    "-o",
                    "json",
                ]
            )
            logs = json.loads(logs_json) if logs_json else []
            if logs:
                print(f"  Container logs ({len(logs)} lines):")
                for row in logs:
                    print(f"    {row.get('TimeGenerated', '')} | {row.get('Log_s', '')}")
            else:
                print("  No container logs in Log Analytics")
        except Exception as e:
            print(f"  Container log query failed: {e}")

        try:
            detail_json = az_output(
                [
                    "az",
                    "containerapp",
                    "job",
                    "execution",
                    "show",
                    "-g",
                    rg,
                    "-n",
                    job,
                    "--job-execution-name",
                    job_exec_name,
                    "-o",
                    "json",
                ]
            )
            detail = json.loads(detail_json)
            props = detail.get("properties", {})
            print("\n  Execution summary:")
            print(f"    Name       : {job_exec_name}")
            print(f"    Status     : {props.get('status', 'unknown')}")
            print(f"    Start (UTC): {props.get('startTime', '')}")
            print(f"    End (UTC)  : {props.get('endTime', '')}")
        except Exception as e:
            print(f"  Execution detail failed: {e}")
    else:
        print("  No terminal execution detected within the waiting window.")

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
