# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Shared packaging-branch naming-schema matching logic."""

import re
from typing import Any, Dict, List


def build_packaging_branch_pattern(schema: Dict[str, Any]) -> re.Pattern:
    """
    Build the compiled regex matching valid packaging branch names from a
    packaging_branch_schema config. Each entry in schema["shapes"] is a
    '/'-joined template: the literal pair 'family/suite' expands to an
    alternation over schema["families"] (suite must belong to its own
    family), and '*' matches any single path segment (e.g. an arbitrary
    prefix). This lets the branch name shape itself change via config,
    without touching this code.
    """
    families = schema["families"]
    family_suite_alternation = "|".join(
        f"{re.escape(family)}/(?:{'|'.join(re.escape(suite) for suite in suites)})"
        for family, suites in families.items()
    )

    shape_patterns = []
    for shape in schema["shapes"]:
        segments = shape.split("/")
        parts = []
        i = 0
        while i < len(segments):
            if segments[i:i + 2] == ["family", "suite"]:
                parts.append(f"(?:{family_suite_alternation})")
                i += 2
            elif segments[i] == "*":
                parts.append("[^/]+")
                i += 1
            else:
                raise ValueError(f"unknown packaging branch shape segment: {segments[i]!r}")
        shape_patterns.append("/".join(parts))

    return re.compile(rf"^(?:{'|'.join(shape_patterns)})$")


def is_packaging_branch_attempt(branch_name: str, families: Dict[str, List[str]]) -> bool:
    """
    A branch is treated as a packaging branch attempt (and therefore subject
    to the naming schema) if any of its path segments names a known family,
    regardless of position or suite validity - except transient promotion PR
    branches (e.g. debian/pr/1.0.0-1, created by scripts/create_promotion_pr.py),
    identified by a literal 'pr' path segment, which are a different, expected
    kind of branch and were never meant to conform to this schema.
    """
    segments = branch_name.split("/")
    if "pr" in segments:
        return False
    return any(segment in families for segment in segments)
