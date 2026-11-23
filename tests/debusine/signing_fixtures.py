# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
Session-scoped fixtures for the signing-key migration tests.

Unlike the full build/publish matrix, these do exactly one debian build in a
fresh ``qli-ci-*`` child workspace and one ``package-publish`` per target
workspace (``qli``, ``qli-staging``) — "perform one release to the relevant
workspace to verify" — so the signature-set checks run against a freshly signed
``InRelease``.
"""

import logging

import pytest

from pipeline import run_build, run_publish

# One representative debian build is enough to exercise signing; the signing
# configuration is not per-suite or per-component.
SIGNING_VENDOR = "debian"
SIGNING_SUITE = "trixie"
SIGNING_COMPONENT = "main"


@pytest.fixture(scope="session")
def signing_build_result(creds):
    """Run one debian build in a fresh child workspace for the signing checks."""
    logger = logging.getLogger("debusine.infra_test.signing.build")
    return run_build(creds, SIGNING_VENDOR, SIGNING_SUITE, SIGNING_COMPONENT, logger)


@pytest.fixture(scope="session")
def signing_publish_result(creds, release_token, signing_build_result, target_workspace):
    """
    Publish the one signing build into ``target_workspace`` (qli, qli-staging).

    Session-scoped and parametrized via the shared ``target_workspace`` fixture,
    so exactly one release runs per target workspace.
    """
    logger = logging.getLogger(f"debusine.infra_test.signing.publish.{target_workspace}")
    return run_publish(creds, release_token, signing_build_result, target_workspace, logger)
