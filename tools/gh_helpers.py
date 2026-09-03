# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Shared `gh` CLI helpers used by configure-repo and rulesets."""

import json
import subprocess
from typing import Dict, List, Optional


def run_gh_command(args: List[str]) -> str:
    """
    Run a gh CLI command and return stdout. Raises CalledProcessError on
    failure. stderr is left connected to the parent process rather than
    captured, so gh's actual error message (permission/scope errors, HTTP
    status, etc.) is visible in the log instead of being swallowed silently
    and replaced with an opaque traceback.
    """
    result = subprocess.run(
        ["gh"] + args, stdout=subprocess.PIPE, text=True, check=True
    )
    return result.stdout


def expand_repo_name(repo_name: str) -> str:
    """Expand repo name to full owner/repo format."""
    return f"qualcomm-linux/{repo_name}"


def check_gh_auth() -> bool:
    """Check if gh is authenticated."""
    try:
        run_gh_command(["auth", "status"])
        return True
    except subprocess.CalledProcessError:
        return False


def get_repo_info(repo: str) -> Dict:
    """Get repository metadata (default_branch, visibility, etc.)."""
    stdout = run_gh_command(["api", f"repos/{repo}"])
    return json.loads(stdout)


def get_repo_custom_properties(repo: str) -> Dict[str, str]:
    """Get repository custom properties as a name→value dict."""
    stdout = run_gh_command(["api", f"repos/{repo}/properties/values"])
    data = json.loads(stdout)
    return {item["property_name"]: item["value"] for item in data}


def get_environment(repo: str, env_name: str) -> Optional[Dict]:
    """Get environment details if it exists."""
    try:
        stdout = run_gh_command(
            ["api", f"repos/{repo}/environments/{env_name}"]
        )
        return json.loads(stdout)
    except subprocess.CalledProcessError:
        return None


def create_environment(repo: str, env_name: str, dry_run: bool) -> None:
    """Create an environment."""
    if dry_run:
        print(f"  Would create environment: {env_name}")
        return

    run_gh_command(
        ["api", f"repos/{repo}/environments/{env_name}", "-X", "PUT"]
    )


def get_repo_variables(repo: str) -> Dict[str, str]:
    """Get all repository-level variables."""
    stdout = run_gh_command(["api", f"repos/{repo}/actions/variables"])
    data = json.loads(stdout)
    return {var["name"]: var["value"] for var in data.get("variables", [])}


def get_visible_org_secrets(repo: str) -> List[str]:
    """Get names of organization-level secrets visible to this repository."""
    stdout = run_gh_command(
        ["api", f"repos/{repo}/actions/organization-secrets"]
    )
    data = json.loads(stdout)
    return [secret["name"] for secret in data.get("secrets", [])]


def get_visible_org_variables(repo: str) -> Dict[str, str]:
    """Get name→value of organization-level variables visible to this repository."""
    stdout = run_gh_command(
        ["api", f"repos/{repo}/actions/organization-variables"]
    )
    data = json.loads(stdout)
    return {var["name"]: var["value"] for var in data.get("variables", [])}


def get_repo_branches(repo: str) -> List[str]:
    """Get the names of all branches in the repository."""
    stdout = run_gh_command(["api", f"repos/{repo}/branches", "--paginate"])
    return [branch["name"] for branch in json.loads(stdout)]


def set_repo_variable(repo: str, name: str, value: str, dry_run: bool) -> None:
    """Set a repository-level variable."""
    if dry_run:
        print(f"  Would set variable {name}={value}")
        return

    # Try to update first
    try:
        run_gh_command(
            [
                "api",
                f"repos/{repo}/actions/variables/{name}",
                "-X",
                "PATCH",
                "-f",
                f"value={value}",
            ]
        )
        return
    except subprocess.CalledProcessError:
        pass

    # If update failed, try to create
    run_gh_command(
        [
            "api",
            f"repos/{repo}/actions/variables",
            "-X",
            "POST",
            "-f",
            f"name={name}",
            "-f",
            f"value={value}",
        ]
    )


def delete_repo_variable(repo: str, name: str, dry_run: bool) -> None:
    """Delete a repository-level variable."""
    if dry_run:
        print(f"  Would delete variable {name}")
        return

    run_gh_command(
        ["api", f"repos/{repo}/actions/variables/{name}", "-X", "DELETE"]
    )


def get_user_id(username: str) -> int:
    """Get a GitHub user's numeric ID."""
    stdout = run_gh_command(["api", f"users/{username}"])
    data = json.loads(stdout)
    return data["id"]
