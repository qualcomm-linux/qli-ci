# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
Logic for comparing the set of keys that signed an apt ``InRelease`` file
against the expected set for a signing-key migration phase.

The expected sets live as committed ASCII-armored public keys under
``data/signing-keys/<environment>/<workspace>/{old,new}/``. The environment is
derived from ``DEBUSINE_HOST`` and a "set" is the union of all public keys found
across the ``.asc`` files in a group directory.

Everything here is network-free and shells out only to ``gpg``:

* ``inrelease_signer_keyids`` enumerates *every* signature packet in an
  ``InRelease`` file via ``gpg --list-packets`` — regardless of whether the
  signing key is available — so multi-signature (phase-2) files and unknown
  signers are both captured.
* ``gpg --show-keys`` reads candidate ``.asc`` files without importing them,
  giving each key's primary fingerprint and the key IDs of its primary and
  subkeys.

Comparison is done at the *primary-fingerprint* level: a signature made by a
signing subkey is collapsed back to its primary key, so one physical public key
counts as one member of a "set" regardless of how many subkeys it carries.
"""

import os
import pathlib
import re
import subprocess
import tempfile

_HOST_SUFFIX = ".debusine.qualcomm.com"
_PRODUCTION_HOST = "debusine.qualcomm.com"

# Migration phase -> which group directories make up the expected set.
_PHASE_GROUPS = {
    1: ("old",),
    2: ("old", "new"),
    3: ("new",),
}

_DATA_ROOT = pathlib.Path(__file__).parent / "data" / "signing-keys"

# A signature packet header line from ``gpg --list-packets`` carries the 16-hex
# issuer key ID, e.g. ``:signature packet: algo 22, keyid 8A5EDAB282632443``.
_KEYID_RE = re.compile(r"keyid\s+([0-9A-Fa-f]{16})")
# Newer signatures also expose a full issuer fingerprint subpacket.
_ISSUER_FPR_RE = re.compile(r"issuer fpr[^0-9A-Fa-f]*([0-9A-Fa-f]{40})")


class SigningKeyError(Exception):
    """Raised when signing-key data or signature parsing goes wrong."""


def environment_from_host(host: str) -> str:
    """
    Map a ``DEBUSINE_HOST`` to the environment directory name.

    ``debusine.qualcomm.com`` -> ``production``;
    ``<env>.debusine.qualcomm.com`` -> ``<env>`` (e.g. ``stage``, ``test``).
    """
    if host == _PRODUCTION_HOST:
        return "production"
    if host.endswith(_HOST_SUFFIX) and len(host) > len(_HOST_SUFFIX):
        return host[: -len(_HOST_SUFFIX)]
    raise SigningKeyError(
        f"Cannot derive environment from DEBUSINE_HOST={host!r}; "
        f"expected {_PRODUCTION_HOST} or <env>{_HOST_SUFFIX}"
    )


def _run_gpg(args: list[str], *, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    """Run gpg in a throwaway GNUPGHOME so the user's keyring is never touched."""
    with tempfile.TemporaryDirectory() as home:
        return subprocess.run(
            ["gpg", "--homedir", home, "--batch", "--no-tty", *args],
            input=input_bytes,
            capture_output=True,
        )


def _extract_signature_block(inrelease_bytes: bytes) -> bytes:
    """Return the ASCII-armored PGP signature block from a clearsigned file."""
    begin = b"-----BEGIN PGP SIGNATURE-----"
    end = b"-----END PGP SIGNATURE-----"
    start = inrelease_bytes.find(begin)
    stop = inrelease_bytes.find(end)
    if start == -1 or stop == -1:
        raise SigningKeyError("InRelease has no PGP SIGNATURE armor block")
    return inrelease_bytes[start : stop + len(end)] + b"\n"


def inrelease_signer_keyids(inrelease_bytes: bytes) -> set[str]:
    """
    Return the set of 16-hex key IDs that signed a clearsigned ``InRelease``.

    Enumerates all signature packets via ``gpg --list-packets``; this does not
    require the signing keys to be present, so it reports every signer including
    unknown ones. Full issuer fingerprints are normalised to their trailing
    16-hex key ID.
    """
    sig = _extract_signature_block(inrelease_bytes)
    result = _run_gpg(["--list-packets"], input_bytes=sig)
    # gpg may exit non-zero (no keyring) yet still print the packets we need.
    text = result.stdout.decode(errors="replace")

    keyids = {m.group(1).upper() for m in _KEYID_RE.finditer(text)}
    for m in _ISSUER_FPR_RE.finditer(text):
        keyids.add(m.group(1).upper()[-16:])

    if not keyids:
        raise SigningKeyError(
            "No signature key IDs found in InRelease.\n"
            f"gpg stdout:\n{text}\n"
            f"gpg stderr:\n{result.stderr.decode(errors='replace')}"
        )
    return keyids


def _show_keys(asc_paths: list[pathlib.Path]) -> tuple[set[str], dict[str, str]]:
    """
    Parse the given ``.asc`` files with ``gpg --show-keys``.

    Returns ``(primary_fprs, keyid_to_primary)`` where ``primary_fprs`` is the
    set of primary key fingerprints (one per physical public key) and
    ``keyid_to_primary`` maps every 16-hex key ID *and* full fingerprint (of
    both primary keys and subkeys) to the owning primary fingerprint.
    """
    primary_fprs: set[str] = set()
    keyid_to_primary: dict[str, str] = {}

    for path in asc_paths:
        result = _run_gpg(
            ["--show-keys", "--with-colons"], input_bytes=path.read_bytes()
        )
        if result.returncode != 0:
            raise SigningKeyError(
                f"gpg --show-keys failed for {path}:\n"
                f"{result.stderr.decode(errors='replace')}"
            )
        current_primary: str | None = None
        pending: str | None = None  # 'pub' or 'sub' awaiting its fpr record
        pending_keyid: str | None = None
        for line in result.stdout.decode(errors="replace").splitlines():
            fields = line.split(":")
            rectype = fields[0]
            if rectype in ("pub", "sub"):
                pending = rectype
                pending_keyid = fields[4].upper()
            elif rectype == "fpr" and pending is not None:
                fpr = fields[9].upper()
                if pending == "pub":
                    current_primary = fpr
                    primary_fprs.add(fpr)
                if current_primary is not None:
                    keyid_to_primary[pending_keyid] = current_primary
                    keyid_to_primary[fpr] = current_primary
                pending = None
                pending_keyid = None

    return primary_fprs, keyid_to_primary


def classify_signers(
    signer_keyids: set[str], keyid_to_primary: dict[str, str]
) -> tuple[set[str], set[str]]:
    """
    Split signer key IDs into known primary fingerprints and unknown key IDs.

    Returns ``(known_primaries, unknown_keyids)``: ``known_primaries`` is the
    set of primary fingerprints the signers map to, ``unknown_keyids`` the key
    IDs that match none of the candidate keys.
    """
    known: set[str] = set()
    unknown: set[str] = set()
    for keyid in signer_keyids:
        primary = keyid_to_primary.get(keyid)
        if primary is None:
            unknown.add(keyid)
        else:
            known.add(primary)
    return known, unknown


def _group_asc_paths(env: str, workspace: str, group: str) -> list[pathlib.Path]:
    group_dir = _DATA_ROOT / env / workspace / group
    return sorted(group_dir.glob("*.asc"))


def expected_primary_fprs(env: str, workspace: str, phase: int) -> set[str]:
    """
    Return the expected set of primary fingerprints for a migration phase.

    Phase 1 -> ``old/``, phase 2 -> ``old/`` + ``new/``, phase 3 -> ``new/``.
    Raises :class:`SigningKeyError` if a required group directory holds no
    ``.asc`` files, so the caller can turn that into an actionable skip/failure.
    """
    if phase not in _PHASE_GROUPS:
        raise SigningKeyError(f"Unknown signing-key phase {phase!r}; expected 1, 2 or 3")

    paths: list[pathlib.Path] = []
    for group in _PHASE_GROUPS[phase]:
        group_paths = _group_asc_paths(env, workspace, group)
        if not group_paths:
            raise SigningKeyError(
                f"No .asc files in {_DATA_ROOT / env / workspace / group} "
                f"(needed for phase {phase}, workspace {workspace!r}, env {env!r}). "
                f"Populate it before running this phase."
            )
        paths.extend(group_paths)

    primary_fprs, _ = _show_keys(paths)
    if not primary_fprs:
        raise SigningKeyError(
            f"No public keys found in {[str(p) for p in paths]} "
            f"(phase {phase}, workspace {workspace!r}, env {env!r}); "
            f"the .asc file(s) carry no key material."
        )
    return primary_fprs


def all_known_asc_paths() -> list[pathlib.Path]:
    """Return every committed signing-key ``.asc`` across all envs/workspaces/groups."""
    return sorted(_DATA_ROOT.glob("*/*/*/*.asc"))


def known_keyid_to_primary() -> dict[str, str]:
    """Map every key ID/fingerprint across all committed keys to its primary fingerprint."""
    _, keyid_to_primary = _show_keys(all_known_asc_paths())
    return keyid_to_primary
