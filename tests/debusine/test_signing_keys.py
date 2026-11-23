# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
Signing-key migration tests.

For both target workspaces (``qli``, ``qli-staging``) these assert that the
exact set of keys that signed a freshly released ``InRelease`` matches the
expectation for the current migration phase (``DEBUSINE_SIGNING_KEY_PHASE``):

* phase 1 -> old set only
* phase 2 -> old set union new set
* phase 3 -> new set only

Separately, the ephemeral ``qli-ci-*`` build workspace must be signed by a
throwaway key that is none of the known qli/qli-staging keys in any environment.

See ``signing_keys.py`` for the expected-set data layout and the key-set logic.
"""

import os

import pytest

from helpers import (
    check_apt_authenticated,
    check_apt_unauthenticated,
    verify_inrelease_signature,
)
from signing_keys import (
    SigningKeyError,
    all_known_asc_paths,
    classify_signers,
    environment_from_host,
    expected_primary_fprs,
    inrelease_signer_keyids,
    known_keyid_to_primary,
)


def _phase() -> int:
    return int(os.environ.get("DEBUSINE_SIGNING_KEY_PHASE", "1"))


@pytest.mark.signing
def test_release_workflow_succeeded(signing_publish_result):
    wr = signing_publish_result["final_wr"]
    assert wr.result == "success", (
        f"Publish workflow {signing_publish_result['workflow_id']} finished with "
        f"result={wr.result!r} status={wr.status!r}"
    )


@pytest.mark.signing
def test_target_inrelease_signing_key_set(creds, signing_publish_result):
    """The target InRelease must be signed by exactly the expected phase set."""
    workspace = signing_publish_result["target_workspace"]
    suite = signing_publish_result["suite"]
    env = environment_from_host(creds["host"])
    phase = _phase()

    try:
        expected = expected_primary_fprs(env, workspace, phase)
    except SigningKeyError as exc:
        pytest.skip(str(exc))

    url = f"{signing_publish_result['apt_url_base']}/dists/{suite}/InRelease"
    resp = check_apt_unauthenticated(url)
    assert resp.status_code == 200, (
        f"Expected 200 (public) for InRelease at {url}, got {resp.status_code}"
    )

    signers = inrelease_signer_keyids(resp.content)
    known, unknown = classify_signers(signers, known_keyid_to_primary())

    assert not unknown, (
        f"InRelease at {url} signed by key ID(s) {sorted(unknown)} that match "
        f"none of the committed qli/qli-staging keys"
    )
    assert known == expected, (
        f"InRelease at {url} signed by {sorted(known)}, "
        f"expected phase-{phase} set {sorted(expected)} "
        f"(env={env}, workspace={workspace})"
    )

    # Secondary sanity: the signature must actually verify against the key set
    # the deployment publishes at signing-keys.asc.
    key_url = f"{signing_publish_result['apt_url_base']}/signing-keys.asc"
    key_resp = check_apt_authenticated(key_url, creds["user"], creds["token"])
    assert key_resp.status_code == 200, (
        f"Expected 200 for signing-keys.asc at {key_url}, got {key_resp.status_code}"
    )
    verify_inrelease_signature(resp.content, key_resp.text)


@pytest.mark.signing
def test_ephemeral_not_signed_by_known_keys(creds, signing_build_result):
    """An ephemeral qli-ci-* build must not be signed by any known qli key."""
    assert all_known_asc_paths(), (
        "No signing-key data committed under data/signing-keys/; "
        "run data/signing-keys/fetch-old-keys first"
    )

    suite = signing_build_result["suite"]
    url = f"{signing_build_result['apt_url_base']}/dists/{suite}/InRelease"
    resp = check_apt_authenticated(url, creds["user"], creds["token"])
    assert resp.status_code == 200, (
        f"Expected 200 for InRelease with auth at {url}, got {resp.status_code}"
    )

    signers = inrelease_signer_keyids(resp.content)
    known, unknown = classify_signers(signers, known_keyid_to_primary())

    assert known == set(), (
        f"Ephemeral workspace InRelease at {url} was signed by known "
        f"qli/qli-staging key(s): {sorted(known)}"
    )
    assert unknown, (
        f"Ephemeral workspace InRelease at {url} carried no signature key IDs"
    )
