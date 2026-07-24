#!/usr/bin/env python3
"""Generate a deploy manifest JSON from a pushed container image."""

import argparse
import json
import subprocess
from pathlib import Path


def resolve_digest_from_acr(registry: str, repository: str, tag: str) -> str:
    """Query ACR for the digest of the given tag."""
    result = subprocess.run(
        [
            "az",
            "acr",
            "repository",
            "show-manifests",
            "--name",
            registry,
            "--repository",
            repository,
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    manifests = json.loads(result.stdout)
    for manifest in manifests:
        if tag in (manifest.get("tags") or []):
            return manifest["digest"]
    raise SystemExit(f"Could not resolve digest for tag {tag} in {registry}/{repository}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deploy manifest")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--image", required=True, help="Full image reference (registry/repo:tag)")
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--output", required=True, help="Path for deploy-manifest.json")
    args = parser.parse_args()

    # Prefer local Docker digest, fall back to ACR query
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", args.image, "--format", "{{index .RepoDigests 0}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        image_reference = result.stdout.strip()
    except subprocess.CalledProcessError:
        digest = resolve_digest_from_acr(args.registry, args.repository, args.tag)
        image_reference = f"{args.registry}.azurecr.io/{args.repository}@{digest}"

    manifest = {
        "serviceName": args.repository,
        "imageReference": image_reference,
        "buildId": args.build_id,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Manifest written to {output_path}")


if __name__ == "__main__":
    main()
