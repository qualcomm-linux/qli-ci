"""pytest coverage for the signing-key-is-valid armor check."""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

import pathlib
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).parent / "signing-key-is-valid"

# The exact well-formed but empty armor block Debusine's signing-keys.asc
# briefly returns for a freshly created workspace: armor markers and a CRC24
# checksum only, with no key packet data. This is the regression that motivated
# the check.
EMPTY_ARMOR_BLOCK = """\
-----BEGIN PGP PUBLIC KEY BLOCK-----


=twTO
-----END PGP PUBLIC KEY BLOCK-----
"""

# A real exported public key (an actual Debusine archive signing key).
GOOD_KEY = """\
-----BEGIN PGP PUBLIC KEY BLOCK-----

mDMEajuyfxYJKwYBBAHaRw8BAQdASazjfos7KwJJ+G6xdBRzc3v7orITHEY6jKc3
RJ9SKQ+0LEFyY2hpdmUgc2lnbmluZyBrZXkgZm9yIHF1YWxjb21tL3FsaS1zdGFn
aW5niJAEExYKADgWIQQHaA+DhymsE9b1W++Bhz/mnjKc1AUCajuyfwIbAwULCQgH
AgYVCgkICwIEFgIDAQIeAQIXgAAKCRCBhz/mnjKc1BFAAQCBx/l+c5fPIl1yxrHZ
oesE1USx5864EapEurg7g8Ov6gD/ZJbguusDuXxCCPkZtyR/APq3ckIEy6zIl7/0
9RR1JgI=
=sKJS
-----END PGP PUBLIC KEY BLOCK-----
"""


def run_check(text: str) -> int:
    """Feed text to signing-key-is-valid on stdin and return its exit status."""
    proc = subprocess.run(
        [str(SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
    )
    assert proc.stdout == "", f"expected no stdout, got {proc.stdout!r}"
    return proc.returncode


@pytest.mark.parametrize(
    "text",
    [
        # A real exported key -> valid.
        GOOD_KEY,
        # Packet data present without a trailing CRC24 line -> valid.
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
        "\n"
        "mDMEajuyfxYJKwYBBAHaRw8BAQdASazjfos7KwJJ\n"
        "-----END PGP PUBLIC KEY BLOCK-----\n",
        # Armor header plus packet data -> valid.
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
        "Version: GnuPG v2\n"
        "Comment: a comment\n"
        "\n"
        "mDMEajuyfxYJKwYBBAHaRw8BAQdASazjfos7KwJJ\n"
        "=sKJS\n"
        "-----END PGP PUBLIC KEY BLOCK-----\n",
        # Text surrounding the block is ignored; the block itself is valid.
        "leading noise\n" + GOOD_KEY + "trailing noise\n",
    ],
)
def test_accepts_valid_keys(text: str) -> None:
    """A block containing real base64 packet data is accepted."""
    assert run_check(text) == 0


@pytest.mark.parametrize(
    "text",
    [
        # The reported empty armor block -> invalid.
        EMPTY_ARMOR_BLOCK,
        # No input at all -> invalid.
        "",
        # Armor headers only, no packet data -> invalid.
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
        "Version: GnuPG v2\n"
        "\n"
        "=twTO\n"
        "-----END PGP PUBLIC KEY BLOCK-----\n",
        # Junk with no armor markers -> invalid.
        "not a key at all\njust some text\n",
        # Packet-looking data outside the armor markers is ignored -> invalid.
        "mDMEajuyfxYJKwYBBAHaRw8BAQdASazjfos7KwJJ\n",
    ],
)
def test_rejects_invalid_keys(text: str) -> None:
    """A block without real base64 packet data is rejected."""
    assert run_check(text) == 1
