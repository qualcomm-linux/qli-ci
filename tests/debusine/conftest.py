# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
Shared fixtures for the Debusine infrastructure test suite.

All Debusine API calls use python3-debusine >= 0.14.9 directly.
Run: source ./setenv, then py.test-3 with any pytest args.
"""

import os
from collections import namedtuple

import pytest

pytest_plugins = ["build_fixtures", "publish_fixtures"]


# ---------------------------------------------------------------------------
# Credential enforcement — fail at collection time, not per-test
# ---------------------------------------------------------------------------

_REQUIRED_VARS = [
    "DEBUSINE_HOST",
    "DEBUSINE_SCOPE",
    "DEBUSINE_USER",
    "DEBUSINE_TOKEN",
    "DEBUSINE_PRODUCTION_RELEASE_TOKEN",
    "DEBUSINE_STAGING_RELEASE_TOKEN",
]


def pytest_configure(config):
    missing = [v for v in _REQUIRED_VARS if not os.environ.get(v)]
    if missing:
        pytest.exit(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Run: source ./setenv",
            returncode=3,
        )


# ---------------------------------------------------------------------------
# Parametrized fixtures
# ---------------------------------------------------------------------------

# Per-vendor build matrix. Suites and components are paired within a vendor so
# that debian suites never cross with ubuntu components (and vice versa).
BuildCase = namedtuple("BuildCase", ["vendor", "suite", "component"])

VENDORS = {
    "debian": {
        "suites": ["trixie", "forky"],
        "components": ["main", "contrib", "non-free", "non-free-firmware"],
    },
    "ubuntu": {
        "suites": ["resolute", "stonking"],
        "components": ["main", "restricted", "universe", "multiverse"],
    },
}


def _build_cases():
    return [
        BuildCase(vendor, suite, component)
        for vendor, cfg in VENDORS.items()
        for suite in cfg["suites"]
        for component in cfg["components"]
    ]


@pytest.fixture(
    scope="session",
    params=_build_cases(),
    ids=lambda c: f"{c.vendor}-{c.suite}-{c.component}",
)
def build_case(request):
    return request.param


@pytest.fixture(scope="session", params=["qli", "qli-staging"])
def target_workspace(request):
    return request.param


# ---------------------------------------------------------------------------
# Service users
# ---------------------------------------------------------------------------

# The suite drives three distinct Debusine service users, each performing one
# task and holding its own API token:
#
#   build       -> creates the child workspace under qli-ci, uploads the source
#                  package and builds it in the child workspace.
#   qli-staging -> releases built packages to the qli-staging workspace.
#   qli         -> releases built packages to the qli workspace.
#
# The release roles are keyed by their target workspace name so that fixtures
# parametrized over target_workspace can look their token up directly.
SERVICE_TOKEN_ENV = {
    "build": "DEBUSINE_TOKEN",
    "qli-staging": "DEBUSINE_STAGING_RELEASE_TOKEN",
    "qli": "DEBUSINE_PRODUCTION_RELEASE_TOKEN",
}


@pytest.fixture(scope="session")
def service_tokens():
    """Map each service-user role to its API token."""
    return {role: os.environ[var] for role, var in SERVICE_TOKEN_ENV.items()}


@pytest.fixture(scope="session")
def creds():
    return {
        "host": os.environ["DEBUSINE_HOST"],
        "scope": os.environ["DEBUSINE_SCOPE"],
        "user": os.environ["DEBUSINE_USER"],
        "token": os.environ["DEBUSINE_TOKEN"],
        "parent_workspace": os.environ.get("DEBUSINE_PARENT_WORKSPACE", "qli-ci"),
    }


@pytest.fixture(scope="session")
def release_token(service_tokens, target_workspace):
    return service_tokens[target_workspace]
