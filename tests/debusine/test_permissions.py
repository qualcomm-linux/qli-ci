# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
Cross-principal authorization tests.

The three service users have different release rights but share the qli-ci
child-workspace operations. These tests assert both halves of that boundary:

  create child workspace under qli-ci -> allowed to the build user AND both
      release users (the build user's grant is covered by the build phase; here
      we assert the release users share it)
  release qli-staging -> package-publish into qli-staging (staging user only)
  release qli -> package-publish into qli (production user only)

The attempts are resolved synchronously by workflow_create (no build is run and
no workflow is polled), so these tests are cheap. The release tests reuse the
session-scoped build_result so they add no new builds.
"""

import logging
import time

import pytest

from debusine.artifacts.models import CollectionCategory
from debusine.client.models import CreateWorkflowRequest

from helpers import assert_permission_denied, make_client


# Service users that — alongside the build user — are granted the qli-ci
# child-workspace operations. The build user's grant is already exercised by the
# build phase; here we assert the release users share it.
BUILD_ALLOWED_RELEASE_ROLES = ["qli-staging", "qli"]

# For each release target, the two service users that must NOT be able to
# publish into it (everyone except that target's own release user).
RELEASE_DENIED = [
    ("qli-staging", "build"),
    ("qli-staging", "qli"),
    ("qli", "build"),
    ("qli", "qli-staging"),
]


@pytest.mark.permissions
@pytest.mark.parametrize("allowed_role", BUILD_ALLOWED_RELEASE_ROLES)
def test_create_child_workspace_allowed_to_release_users(
    creds, service_tokens, allowed_role
):
    """
    A release user must be able to create a child workspace under qli-ci.

    Release users are granted the qli-ci child-workspace operations (not just
    the build user), so starting create-child-workspace must be accepted rather
    than rejected on authorization. The request is only started (permission is
    checked at that point); it is not polled.
    """
    logger = logging.getLogger(
        f"debusine.infra_test.permissions.build.{allowed_role}"
    )
    client = make_client(
        creds["host"], service_tokens[allowed_role], creds["scope"], logger
    )
    suffix = f"infra-test-authz-{allowed_role}-{int(time.time())}"

    wr = client.workflow_create(
        CreateWorkflowRequest(
            template_name="create-child-workspace",
            workspace=creds["parent_workspace"],
            task_data={"suffix": suffix},
        )
    )
    assert isinstance(wr.id, int) and wr.id > 0, (
        f"create-child-workspace as {allowed_role} did not return a work request id: {wr!r}"
    )


@pytest.mark.permissions
@pytest.mark.parametrize("target_workspace,denied_role", RELEASE_DENIED)
def test_release_denied_to_non_owning_users(
    creds, service_tokens, build_result, target_workspace, denied_role
):
    """
    Only the target workspace's own release user may publish into it.

    Build a well-formed package-publish request (so it reaches the authorization
    check rather than failing validation first) and assert that the non-owning
    user is rejected.
    """
    if build_result["vendor"] != "debian":
        pytest.skip("publish is debian-only until ubuntu target workspaces exist")

    suite = build_result["suite"]
    pkg_name = build_result["pkg_name"]
    pkg_version = build_result["pkg_version"]

    logger = logging.getLogger(
        f"debusine.infra_test.permissions.release."
        f"{suite}.{target_workspace}.{denied_role}"
    )

    # The build client owns the child workspace; read the suite collection with
    # it so the request we build is well-formed and fails on authorization, not
    # on an unresolved lookup.
    suite_collection = next(
        (
            c for c in build_result["client"].collection_iter(build_result["workspace"])
            if c.category == CollectionCategory.SUITE and c.name == suite
        ),
        None,
    )
    assert suite_collection is not None, (
        f"Suite collection {suite!r} not found in workspace "
        f"{build_result['workspace']!r}"
    )

    task_data = {
        "source_artifact": (
            f"{suite_collection.id}@collections/{pkg_name}_{pkg_version}"
        ),
        "binary_artifacts": {
            "category": "debian:binary-package",
            "collection": f"{suite_collection.id}@collections",
        },
        "target_suite": f"{suite}@{CollectionCategory.SUITE}",
        "unembargo": True,
    }

    client = make_client(
        creds["host"], service_tokens[denied_role], creds["scope"], logger
    )
    assert_permission_denied(
        lambda: client.workflow_create(
            CreateWorkflowRequest(
                template_name="package-publish",
                workspace=target_workspace,
                task_data=task_data,
            )
        )
    )
