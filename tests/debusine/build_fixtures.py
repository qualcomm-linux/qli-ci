# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
Session-scoped fixture that runs the build pipeline once per
(vendor, suite, component) combination and shares the result across all tests.
"""

import logging

import pytest

from pipeline import run_build


@pytest.fixture(scope="session")
def build_result(creds, build_case):
    """
    Run the full Debusine build pipeline for one (vendor, suite, component).

    Session-scoped: pytest runs this once per build_case combination
    and reuses the result for every test that requests it.
    """
    vendor, suite, component = build_case
    logger = logging.getLogger(f"debusine.infra_test.build.{vendor}.{suite}.{component}")
    return run_build(creds, vendor, suite, component, logger)
