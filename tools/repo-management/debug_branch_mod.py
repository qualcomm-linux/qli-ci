# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Rewrite qli-ci reusable workflow branch references in packaging workflow YAML files."""

# This is a hack that modifies the stub workflow files to use a branch of
# qli-ci to supply the reusable workflow to allow for development and
# debugging of changes to the reusable workflow in a branch without having to
# land the changes in the main branch first. For this to work, it has to have
# some knowledge of the structure of the workflow stubs, which is the necessary
# hack. It is only used in the development/debug case and deliberately excluded
# from the production code path. The `modify_workflow_files()` entry point is
# invoked via `./update-workflow-files --debug-source-branch=...`.

import itertools
import re
from pathlib import Path

import ruamel.yaml

# Matches a call to any qli-ci reusable workflow, capturing everything up to
# and including the "@" so that only the ref is rewritten.
QLI_CI_USES_PATTERN = re.compile(
    r"(qualcomm-linux/qli-ci/\.github/workflows/[^/@]+\.ya?ml@)(.*)"
)


def modify_workflow_files(directory: Path, branch: str) -> None:
    """Rewrite @main references to @branch in all .yml files under directory."""
    yaml = ruamel.yaml.YAML()
    yaml.preserve_quotes = True

    for path in itertools.chain(directory.rglob("*.yml"), directory.rglob("*.yaml")):
        doc = yaml.load(path)
        if not isinstance(doc, dict):
            continue
        jobs = doc.get("jobs")
        if not isinstance(jobs, dict):
            continue
        modified = False
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            uses = job.get("uses", "")
            match = QLI_CI_USES_PATTERN.fullmatch(uses) if isinstance(uses, str) else None
            if match:
                if match.group(2) != "main":
                    raise ValueError(
                        f"{path}: unexpected ref in uses: {uses!r} (expected @main)"
                    )
                with_block = job.get("with")
                if not isinstance(with_block, dict) or with_block.get("qli-ci-ref") != "main":
                    raise ValueError(
                        f"{path}: qli-ci-ref must be 'main' when uses is {uses!r}"
                    )
                job["uses"] = match.group(1) + branch
                with_block["qli-ci-ref"] = branch
                modified = True
        if modified:
            with path.open("w") as f:
                yaml.dump(doc, f)
