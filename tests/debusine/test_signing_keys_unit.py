# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
Offline unit tests for the signing-key logic in ``signing_keys.py``.

These need only ``gpg`` plus the committed key data and sample ``InRelease``
files under ``testdata/`` — no Debusine account — so they are marked ``unit``
and run with ``py.test-3 -m unit`` without sourcing credentials.
"""

import pathlib
import re

import pytest

from signing_keys import (
    SigningKeyError,
    classify_signers,
    expected_primary_fprs,
    inrelease_signer_keyids,
    known_keyid_to_primary,
)

pytestmark = pytest.mark.unit

_TESTDATA = pathlib.Path(__file__).parent / "testdata"

# Sample InRelease files committed under testdata/, paired with the environment
# and workspace whose committed key is expected to have signed them.
_SAMPLES = {
    "qli": _TESTDATA / "InRelease-production-qli-trixie",
    "qli-staging": _TESTDATA / "InRelease-production-qli-staging-trixie",
}

_KEYID_RE = re.compile(r"^[0-9A-F]{16}$")
_FPR_RE = re.compile(r"^[0-9A-F]{40}$")


def _sample(workspace: str) -> bytes:
    return _SAMPLES[workspace].read_bytes()


# ---------------------------------------------------------------------------
# inrelease_signer_keyids
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workspace", list(_SAMPLES))
def test_signer_keyids_are_uppercase_16_hex(workspace):
    keyids = inrelease_signer_keyids(_sample(workspace))
    assert keyids, "expected at least one signer key ID"
    assert all(_KEYID_RE.match(k) for k in keyids), keyids


@pytest.mark.parametrize("workspace", list(_SAMPLES))
def test_signer_keyids_identify_the_committed_key(workspace):
    """Each sample InRelease is signed by exactly its committed production key."""
    keyids = inrelease_signer_keyids(_sample(workspace))
    expected = expected_primary_fprs("production", workspace, 1)
    known, unknown = classify_signers(keyids, known_keyid_to_primary())
    assert not unknown, f"signers {sorted(unknown)} match no committed key"
    assert known == expected


def test_different_workspaces_have_different_signers():
    assert inrelease_signer_keyids(_sample("qli")) != inrelease_signer_keyids(
        _sample("qli-staging")
    )


def test_signer_keyids_without_signature_block_raises():
    with pytest.raises(SigningKeyError, match="SIGNATURE armor"):
        inrelease_signer_keyids(b"Origin: not a signed file\n")


# ---------------------------------------------------------------------------
# expected_primary_fprs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workspace", list(_SAMPLES))
def test_expected_phase1_is_one_primary_fingerprint(workspace):
    fprs = expected_primary_fprs("production", workspace, 1)
    assert fprs, "phase-1 set must be non-empty"
    assert all(_FPR_RE.match(f) for f in fprs), fprs


def test_expected_phase1_qli_and_staging_differ():
    assert expected_primary_fprs("production", "qli", 1) != expected_primary_fprs(
        "production", "qli-staging", 1
    )


@pytest.mark.parametrize("phase", [2, 3])
def test_expected_raises_when_new_set_absent(phase):
    """Phases needing new/ fail with an actionable message while it is empty."""
    with pytest.raises(SigningKeyError, match="No .asc files"):
        expected_primary_fprs("production", "qli", phase)


def test_expected_rejects_unknown_phase():
    with pytest.raises(SigningKeyError, match="phase"):
        expected_primary_fprs("production", "qli", 99)


def test_expected_raises_when_env_workspace_has_no_keys():
    # dev2/qli publishes no key yet, so only a .gitkeep lives under old/.
    with pytest.raises(SigningKeyError, match="No .asc files"):
        expected_primary_fprs("dev2", "qli", 1)
