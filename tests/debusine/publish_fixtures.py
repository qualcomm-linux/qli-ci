# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
Session-scoped fixture that runs the publish pipeline once per
(suite, component, target_workspace) combination and shares the result
across all tests.
"""

import logging

import pytest

from pipeline import run_publish


@pytest.fixture(scope="session")
def publish_result(creds, release_token, build_result, target_workspace):
    """
    Run the package-publish workflow for one (suite, component, target_workspace).

    Session-scoped: pytest runs this once per (suite, component, target_workspace)
    combination and reuses the result for every test that requests it.
    """
    if build_result["vendor"] != "debian":
        pytest.skip("publish is debian-only until ubuntu target workspaces exist")

    suite = build_result["suite"]
    component = build_result["component"]
    logger = logging.getLogger(
        f"debusine.infra_test.publish.{suite}.{component}.{target_workspace}"
    )
    return run_publish(creds, release_token, build_result, target_workspace, logger)
