#!/usr/bin/env python3
"""
Shared helpers for the repository-management tools in this directory
(configure-repo, set-repo-secrets, update-workflow-files).

This module is imported by those tools; it is not meant to be run directly.
"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

# This module's behaviour must match what SPECIFICATION.md describes. Before
# changing what the shared helpers do to or check about a repository, update
# SPECIFICATION.md first, then bring this implementation into sync with it.

import json
import subprocess
from typing import Dict, List


def run_gh_command(args: List[str]) -> str:
    """Run a gh CLI command and return stdout. Raises CalledProcessError on failure."""
    result = subprocess.run(
        ["gh"] + args, capture_output=True, text=True, check=True
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


def get_repo_custom_properties(repo: str) -> Dict[str, str]:
    """Get repository custom properties as a name→value dict."""
    stdout = run_gh_command(["api", f"repos/{repo}/properties/values"])
    data = json.loads(stdout)
    return {item["property_name"]: item["value"] for item in data}


def is_pkg_repo_approved(repo: str) -> bool:
    """
    COMPLIANCE GATE: return True only if the repository's 'is-pkg-repo' custom
    property is set to 'true'.

    This property is the record that compliance approval has taken place before
    QLI CI is enabled on a given repository, so this is the single source of
    truth for that gate across all three tools. The gate is deliberate and MUST
    NOT be bypassable by --force or any other means; callers must refuse to
    proceed when this returns False. See SPECIFICATION.md ("Compliance Gate:
    is-pkg-repo"). Do not relax this without a corresponding specification
    change and compliance sign-off.
    """
    props = get_repo_custom_properties(repo)
    return props.get("is-pkg-repo") == "true"
