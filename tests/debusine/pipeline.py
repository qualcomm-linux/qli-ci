# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
Shared Debusine build/publish pipeline steps.

These functions carry out the Debusine operations that the fixtures expose:
``run_build`` drives a build in a fresh ``qli-ci-*`` child workspace, and
``run_publish`` runs ``package-publish`` into a target workspace. They are
factored out of the fixtures so that both the full build/publish matrix
(``build_fixtures``/``publish_fixtures``) and the single-shot signing-key
fixtures (``signing_fixtures``) can reuse the exact same steps.
"""

import logging
import pathlib
import tempfile
import time

from debusine.artifacts.models import CollectionCategory
from debusine.client.client_utils import prepare_dsc_for_upload
from debusine.client.models import (
    CollectionDataNew,
    CreateWorkflowRequest,
    WorkflowTemplateDataNew,
)

from conftest import VENDORS
from helpers import create_minimal_source_package, make_client, poll_work_request

# Short per-component tag used in child workspace names.
_COMPONENT_ABBREV = {
    "main": "m",
    "contrib": "c",
    "non-free": "nf",
    "non-free-firmware": "nff",
    "restricted": "r",
    "universe": "u",
    "multiverse": "mv",
}


def run_build(creds, vendor, suite, component, logger) -> dict:
    """
    Run the full Debusine build pipeline for one (vendor, suite, component).

    Creates a child workspace under the parent CI workspace, creates the
    archive suite and ``debian_pipeline`` template, imports a minimal source
    package, runs the build, and polls until it completes.

    Returns a result dict describing the workspace, artifact, workflow, and the
    authenticated client (reused by the publish step to locate collections).
    """
    host = creds["host"]
    scope = creds["scope"]
    token = creds["token"]
    parent_workspace = creds["parent_workspace"]

    client = make_client(host, token, scope, logger)

    abbrev = _COMPONENT_ABBREV[component]
    ts = int(time.time())
    child_suffix = f"infra-test-{vendor}-{suite}-{abbrev}-{ts}"
    workspace = f"{parent_workspace}-{child_suffix}"

    build_dir = pathlib.Path(tempfile.mkdtemp(prefix="debusine-infra-test-"))
    srcpkg = create_minimal_source_package(build_dir, component)

    # 1. Create child workspace
    logger.info("Creating child workspace %s", workspace)
    wr = client.workflow_create(CreateWorkflowRequest(
        template_name="create-child-workspace",
        workspace=parent_workspace,
        task_data={"suffix": child_suffix},
    ))
    final = poll_work_request(client, wr.id, logger)
    assert final.result == "success", (
        f"create-child-workspace failed: result={final.result} status={final.status}"
    )

    # 2. Create archive suite with all components for this vendor in one collection
    logger.info("Creating archive suite %s in %s", suite, workspace)
    client.collection_create(
        workspace,
        CollectionDataNew(
            name=suite,
            category=CollectionCategory.SUITE,
            data={
                "components": VENDORS[vendor]["components"],
                "architectures": ["all", "amd64", "arm64"],
            },
        ),
    )

    # 3. Create debian_pipeline workflow template
    logger.info("Creating workflow template in %s", workspace)
    client.workflow_template_create(
        workspace,
        WorkflowTemplateDataNew(
            name="debian_pipeline",
            task_name="debian_pipeline",
            static_parameters={
                "vendor": vendor,
                "sbuild_environment_variant": "buildd",
                "enable_autopkgtest": False,
                "enable_lintian": False,
                "enable_piuparts": False,
                "enable_blhc": False,
                "codename": suite,
                "suite": f"{suite}@{CollectionCategory.SUITE}",
            },
            runtime_parameters={"source_artifact": "any"},
        ),
    )

    # 4. Import source package
    logger.info("Importing source package %s %s", srcpkg.name, srcpkg.version)
    local_artifact = prepare_dsc_for_upload(srcpkg.dsc_path)
    remote = client.upload_artifact(local_artifact, workspace=workspace)
    artifact_id = remote.id
    logger.info("Imported artifact id=%d", artifact_id)

    # 5. Start build workflow
    logger.info("Starting debian_pipeline workflow for artifact %d", artifact_id)
    wr = client.workflow_create(CreateWorkflowRequest(
        template_name="debian_pipeline",
        workspace=workspace,
        task_data={"source_artifact": f"{artifact_id}@artifacts"},
    ))
    workflow_id = wr.id
    logger.info(
        "Build workflow started: id=%d  URL: https://%s/%s/%s/work-request/%d/",
        workflow_id, host, scope, workspace, workflow_id,
    )

    # 6. Poll until complete
    final_wr = poll_work_request(client, workflow_id, logger)
    logger.info("Build workflow %d finished: result=%s", workflow_id, final_wr.result)

    return {
        "workspace": workspace,
        "artifact_id": artifact_id,
        "workflow_id": workflow_id,
        "final_wr": final_wr,
        "pkg_name": srcpkg.name,
        "pkg_version": srcpkg.version,
        "vendor": vendor,
        "suite": suite,
        "component": component,
        "apt_url_base": f"https://deb.{host}/{scope}/{workspace}",
        "client": client,
    }


def run_publish(creds, release_token, build_result, target_workspace, logger) -> dict:
    """
    Run the ``package-publish`` workflow for one build into ``target_workspace``.

    Locates the suite collection produced by ``build_result`` and publishes the
    source and binary artifacts into ``target_workspace`` using ``release_token``.
    Returns a result dict describing the publish workflow and the target APT URL.
    """
    host = creds["host"]
    scope = creds["scope"]

    suite = build_result["suite"]
    component = build_result["component"]

    release_client = make_client(host, release_token, scope, logger)

    suite_collection = next(
        (
            c for c in build_result["client"].collection_iter(build_result["workspace"])
            if c.category == CollectionCategory.SUITE and c.name == suite
        ),
        None,
    )
    assert suite_collection is not None, (
        f"Suite collection {suite!r} not found in workspace {build_result['workspace']!r}"
    )

    pkg_name = build_result["pkg_name"]
    pkg_version = build_result["pkg_version"]

    logger.info(
        "Starting package-publish to %s (suite collection id=%d)",
        target_workspace, suite_collection.id,
    )
    wr = release_client.workflow_create(CreateWorkflowRequest(
        template_name="package-publish",
        workspace=target_workspace,
        task_data={
            "source_artifact": (
                f"{suite_collection.id}@collections/{pkg_name}_{pkg_version}"
            ),
            "binary_artifacts": {
                "category": "debian:binary-package",
                "collection": f"{suite_collection.id}@collections",
            },
            "target_suite": f"{suite}@{CollectionCategory.SUITE}",
            "unembargo": True,
        },
    ))

    logger.info("Publish workflow started: id=%d", wr.id)
    final_wr = poll_work_request(release_client, wr.id, logger)
    logger.info("Publish workflow %d finished: result=%s", wr.id, final_wr.result)

    return {
        "final_wr": final_wr,
        "workflow_id": wr.id,
        "target_workspace": target_workspace,
        "suite": suite,
        "component": component,
        "pkg_name": pkg_name,
        "pkg_version": pkg_version,
        "apt_url_base": f"https://deb.{host}/{scope}/{target_workspace}",
    }
