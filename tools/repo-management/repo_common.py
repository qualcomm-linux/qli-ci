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
import urllib.error
import urllib.request
from typing import Dict, List


# Live list of repositories that QLI CI tooling is permitted to act on. The
# tools fetch this at runtime and refuse to touch a repository that is not
# listed, unless --force is given. This is an operational targeting guard, not
# a statement about repository state, so it is documented in README.md rather
# than SPECIFICATION.md. It is distinct from, and weaker than, the is-pkg-repo
# compliance gate (see is_pkg_repo_approved): that gate is never bypassable;
# this one is bypassable with --force.
ACTIVE_REPO_LIST_URL = (
    "https://github.com/qualcomm-linux/qli-ci/raw/refs/heads/"
    "active-repo-list/active-repo.list"
)


class ActiveRepoListError(Exception):
    """Raised when the live active-repo list cannot be fetched."""


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


def fetch_active_repo_list() -> List[str]:
    """
    Fetch the live list of active repositories (see ACTIVE_REPO_LIST_URL).

    Returns the repository names (short, un-expanded form, one per line),
    ignoring blank lines. Raises ActiveRepoListError if the list cannot be
    fetched.
    """
    try:
        with urllib.request.urlopen(ACTIVE_REPO_LIST_URL) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as e:
        raise ActiveRepoListError(
            f"could not fetch active-repo list from {ACTIVE_REPO_LIST_URL}: {e}"
        )
    return [line.strip() for line in body.splitlines() if line.strip()]


def is_repo_active(repo_name: str) -> bool:
    """
    Return True if repo_name is on the live active-repo list.

    repo_name is the short, un-expanded name (e.g. 'pkg-fastrpc'), matching the
    form the list stores. Raises ActiveRepoListError if the list cannot be
    fetched.
    """
    return repo_name in fetch_active_repo_list()


# Shared message appended when refusing to act on a repository that is not on
# the active-repo list, so every tool points at --force consistently.
ACTIVE_REPO_FORCE_HINT = "Use --force to bypass this check."


def active_repo_refusal_message(repo_name: str) -> str:
    """Return the standard error text for a repo that is not on the active list."""
    return (
        f"Repository '{repo_name}' is not on the active-repo list "
        f"({ACTIVE_REPO_LIST_URL}). " + ACTIVE_REPO_FORCE_HINT
    )
