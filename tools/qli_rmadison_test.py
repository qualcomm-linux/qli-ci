# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Tests for qli_rmadison.

These exercise the tool end to end via main(), replacing the network
fetch() with an in-memory synthetic repository so no real URLs are hit.
"""

import lzma
import sys
from collections import namedtuple

import pytest

import qli_rmadison

BASE = "https://deb.debusine.qualcomm.com/qualcomm/qli"
BASE_STAGING = "https://deb.debusine.qualcomm.com/qualcomm/qli-staging"


def _xz(text):
    return lzma.compress(text.encode())


def _release(architectures, components):
    return (
        f"Architectures: {' '.join(architectures)}\n"
        f"Components: {' '.join(components)}\n"
    ).encode()


def _packages(entries):
    """entries: (package, version, architecture) or (..., source) tuples.

    A 4th element sets the Source field (as "name" or "name (version)"),
    modelling a binary whose source package name differs from its own.
    """
    stanzas = []
    for entry in entries:
        package, version, arch = entry[:3]
        stanza = f"Package: {package}\nVersion: {version}\nArchitecture: {arch}\n"
        if len(entry) > 3:
            stanza += f"Source: {entry[3]}\n"
        stanzas.append(stanza)
    return _xz("\n".join(stanzas))


def _sources(entries):
    """entries: list of (package, version) tuples."""
    return _xz("\n".join(f"Package: {p}\nVersion: {v}\n" for p, v in entries))


# A representative synthetic repository covering: two suites, two
# components, several architectures, "all", multiple versions of the same
# package within a suite, and both binary and source stanzas.
REPO = {
    f"{BASE}/dists/forky/Release": _release(
        ["amd64", "arm64"], ["main", "non-free"]
    ),
    f"{BASE}/dists/trixie/Release": _release(["amd64"], ["main"]),
    # forky/main
    f"{BASE}/dists/forky/main/binary-amd64/Packages.xz": _packages(
        [
            ("hello", "2.10-1", "amd64"),
            ("hello", "2.10-2", "amd64"),
            ("libfoo", "1.0", "all"),
            # Binary children of source package "foo"; note the Source field
            # carries a version, and the binary names differ from the source.
            ("libfoo1", "4.5-1", "amd64", "foo (4.5-1)"),
            ("foo-utils", "4.5-1", "amd64", "foo"),
        ]
    ),
    f"{BASE}/dists/forky/main/binary-arm64/Packages.xz": _packages(
        [
            ("hello", "2.10-2", "arm64"),
            ("libfoo1", "4.5-1", "arm64", "foo (4.5-1)"),
        ]
    ),
    f"{BASE}/dists/forky/main/source/Sources.xz": _sources(
        [("hello", "2.10-2"), ("hello", "2.10-1"), ("foo", "4.5-1")]
    ),
    # forky/non-free
    f"{BASE}/dists/forky/non-free/binary-amd64/Packages.xz": _packages(
        [("blob", "3.0", "amd64")]
    ),
    f"{BASE}/dists/forky/non-free/binary-arm64/Packages.xz": _packages(
        [("blob", "3.0", "arm64")]
    ),
    f"{BASE}/dists/forky/non-free/source/Sources.xz": _sources([("blob", "3.0")]),
    # trixie/main
    f"{BASE}/dists/trixie/main/binary-amd64/Packages.xz": _packages(
        [("hello", "2.12-1", "amd64")]
    ),
    f"{BASE}/dists/trixie/main/source/Sources.xz": _sources([("hello", "2.12-1")]),
    # staging repository (distinct contents to prove -u selects it)
    f"{BASE_STAGING}/dists/forky/Release": _release(["amd64"], ["main"]),
    f"{BASE_STAGING}/dists/forky/main/binary-amd64/Packages.xz": _packages(
        [("hello", "9.99-1", "amd64")]
    ),
    f"{BASE_STAGING}/dists/forky/main/source/Sources.xz": _sources(
        [("hello", "9.99-1")]
    ),
}


def _patch_repo(monkeypatch, mapping):
    """Point qli_rmadison.fetch at an in-memory mapping; return the URL log.

    The real fetch() is an async httpx call; the stub is likewise a
    coroutine, but ignores the client and serves bytes from `mapping`
    (missing key -> None, modelling a 404). Fetched URLs are recorded in
    call order so tests can assert on fetch minimization.
    """
    fetched = []

    async def fake_fetch(client, url):
        fetched.append(url)
        return mapping.get(url)

    monkeypatch.setattr(qli_rmadison, "fetch", fake_fetch)
    return fetched


@pytest.fixture
def repo(monkeypatch):
    """Serve the synthetic REPO; return the URL log (see _patch_repo)."""
    return _patch_repo(monkeypatch, REPO)


# Result of driving the CLI: stdout as lines, the raw stdout/stderr text,
# and the list of URLs fetched (for asserting on fetch minimization).
Result = namedtuple("Result", ["lines", "out", "err", "fetched"])


def run(fetched, capsys, argv):
    """Invoke main() with argv; return a Result.

    `fetched` is the URL log returned by the `repo` fixture or _patch_repo.
    """
    saved = sys.argv
    sys.argv = ["qli-rmadison"] + argv
    try:
        qli_rmadison.main()
    finally:
        sys.argv = saved
    captured = capsys.readouterr()
    return Result(captured.out.splitlines(), captured.out, captured.err, list(fetched))


# --------------------------------------------------------------------------
# parse_multi
# --------------------------------------------------------------------------


def test_parse_multi_none():
    assert qli_rmadison.parse_multi(None) is None
    assert qli_rmadison.parse_multi([]) is None


def test_parse_multi_comma_and_space_and_repeats():
    assert qli_rmadison.parse_multi(["amd64,arm64"]) == {"amd64", "arm64"}
    assert qli_rmadison.parse_multi(["amd64 arm64"]) == {"amd64", "arm64"}
    assert qli_rmadison.parse_multi(["amd64", "arm64,i386"]) == {
        "amd64",
        "arm64",
        "i386",
    }


# --------------------------------------------------------------------------
# Basic listing / formatting
# --------------------------------------------------------------------------


def test_basic_listing_collapses_and_orders(repo, capsys):
    assert run(repo, capsys, ["hello"]).lines == [
        "hello      | 2.10-1        | qli         | forky          | source, amd64",
        "hello      | 2.10-2        | qli         | forky          | source, amd64, arm64",
        "hello      | 2.12-1        | qli         | trixie         | source, amd64",
        "hello      | 9.99-1        | qli-staging | forky          | source, amd64",
    ]


def test_source_listed_first_then_alphabetical(repo, capsys):
    lines = run(repo, capsys, ["hello"]).lines
    # 2.10-2 in forky has source + amd64 + arm64; source must lead, rest sorted.
    row = next(line for line in lines if "2.10-2" in line)
    assert row.endswith("| source, amd64, arm64")


def test_arch_all_is_shown(repo, capsys):
    # libfoo has a binary "all" stanza but no source stanza.
    assert run(repo, capsys, ["libfoo"]).lines == [
        "libfoo     | 1.0           | qli         | forky          | all",
    ]


def test_no_matches_produces_no_output(repo, capsys):
    assert run(repo, capsys, ["does-not-exist"]).lines == []


def test_multiple_packages(repo, capsys):
    lines = run(repo, capsys, ["hello", "libfoo"]).lines
    # Sorted by package name first.
    assert lines[0].startswith("hello")
    assert any(line.startswith("libfoo") for line in lines)


# --------------------------------------------------------------------------
# -1 (only newest)
# --------------------------------------------------------------------------


def test_only_newest_hides_older_within_suite(repo, capsys):
    # forky's 2.10-1 is dropped; 2.10-2 (newest in qli/forky) and trixie's
    # remain, plus the independent qli-staging/forky row.
    assert run(repo, capsys, ["hello", "-1"]).lines == [
        "hello      | 2.10-2        | qli         | forky          | source, amd64, arm64",
        "hello      | 2.12-1        | qli         | trixie         | source, amd64",
        "hello      | 9.99-1        | qli-staging | forky          | source, amd64",
    ]


def test_only_newest_is_per_suite_not_global(repo, capsys):
    # trixie has a newer version (2.12-1) than forky's newest (2.10-2), but
    # the forky row must survive because -1 is scoped per (repo, suite).
    lines = run(repo, capsys, ["hello", "-1"]).lines
    assert any("forky" in line for line in lines)
    assert any("trixie" in line for line in lines)


def test_only_newest_is_per_repo(repo, capsys):
    # qli-staging's 9.99-1 is newer than every qli version, but qli's forky
    # and trixie rows must survive because -1 is scoped per repository too.
    lines = run(repo, capsys, ["hello", "-1"]).lines
    assert any("| qli         |" in line for line in lines)
    assert any("| qli-staging |" in line for line in lines)


# --------------------------------------------------------------------------
# -v (verbose progress / timing, on stderr)
# --------------------------------------------------------------------------


def test_verbose_reports_each_url_on_stderr(repo, capsys):
    res = run(repo, capsys, ["hello", "-v"])
    # Every fetched URL is announced on stderr, and none of that noise
    # leaks onto stdout (which must stay a clean, pipeable listing).
    for url in res.fetched:
        assert f"Fetching {url}" in res.err
        assert url not in res.out
    # Data rows still go to stdout.
    assert "hello" in res.out


def test_verbose_reports_per_fetch_and_total_timing(repo, capsys):
    res = run(repo, capsys, ["hello", "-v"])
    # A per-fetch elapsed time in seconds appears for fetched URLs...
    assert res.err.count("s)") >= len(res.fetched)
    # ...and a single grand total is printed once at the end.
    assert res.err.count("All fetches took ") == 1
    assert res.err.rstrip().endswith("s")


def test_verbose_marks_missing_urls(capsys, monkeypatch):
    # A 404 (fetch -> None) is reported as "not found", not an error.
    partial = dict(REPO)
    del partial[f"{BASE}/dists/trixie/main/binary-amd64/Packages.xz"]
    fetched = _patch_repo(monkeypatch, partial)
    assert "not found" in run(fetched, capsys, ["hello", "-v"]).err


def test_non_verbose_is_silent_on_stderr(repo, capsys):
    res = run(repo, capsys, ["hello"])
    assert res.err == ""
    assert "hello" in res.out


# --------------------------------------------------------------------------
# -a (architecture filter)
# --------------------------------------------------------------------------


def test_arch_filter_binary_only(repo, capsys):
    res = run(repo, capsys, ["hello", "-a", "amd64"])
    assert res.lines == [
        "hello      | 2.10-1        | qli         | forky          | amd64",
        "hello      | 2.10-2        | qli         | forky          | amd64",
        "hello      | 2.12-1        | qli         | trixie         | amd64",
        "hello      | 9.99-1        | qli-staging | forky          | amd64",
    ]
    # Sources must not be fetched when 'source' is not requested.
    assert not any(url.endswith("Sources.xz") for url in res.fetched)
    # arm64 Packages must not be fetched.
    assert not any("binary-arm64" in url for url in res.fetched)


def test_arch_filter_source_only(repo, capsys):
    res = run(repo, capsys, ["hello", "-a", "source"])
    assert res.lines == [
        "hello      | 2.10-1        | qli         | forky          | source",
        "hello      | 2.10-2        | qli         | forky          | source",
        "hello      | 2.12-1        | qli         | trixie         | source",
        "hello      | 9.99-1        | qli-staging | forky          | source",
    ]
    # No binary Packages fetched when only source requested.
    assert not any("binary-" in url for url in res.fetched)


def test_arch_filter_source_and_binary(repo, capsys):
    # source is requested alongside arm64: source stanzas match in every
    # suite, arm64 binaries only where present (qli forky 2.10-2).
    assert run(repo, capsys, ["hello", "-a", "source,arm64"]).lines == [
        "hello      | 2.10-1        | qli         | forky          | source",
        "hello      | 2.10-2        | qli         | forky          | source, arm64",
        "hello      | 2.12-1        | qli         | trixie         | source",
        "hello      | 9.99-1        | qli-staging | forky          | source",
    ]


# --------------------------------------------------------------------------
# -S (source-and-binary): show binary children of queried source packages
# --------------------------------------------------------------------------


def test_source_and_binary_shows_children(repo, capsys):
    # "foo" is a source package; its binaries are libfoo1 and foo-utils.
    assert run(repo, capsys, ["foo", "-S"]).lines == [
        "foo        | 4.5-1         | qli         | forky          | source",
        "foo-utils  | 4.5-1         | qli         | forky          | amd64",
        "libfoo1    | 4.5-1         | qli         | forky          | amd64, arm64",
    ]


def test_without_source_and_binary_children_are_hidden(repo, capsys):
    # Without -S, querying the source name matches only the source stanza.
    assert run(repo, capsys, ["foo"]).lines == [
        "foo        | 4.5-1         | qli         | forky          | source",
    ]


def test_source_and_binary_still_matches_exact_binary_name(repo, capsys):
    # -S must not lose direct matches on the binary's own package name.
    assert run(repo, capsys, ["libfoo1", "-S"]).lines == [
        "libfoo1    | 4.5-1         | qli         | forky          | amd64, arm64",
    ]


def test_source_and_binary_respects_arch_filter(repo, capsys):
    # Only libfoo1 has an arm64 binary; source and amd64-only foo-utils drop.
    assert run(repo, capsys, ["foo", "-S", "-a", "arm64"]).lines == [
        "libfoo1    | 4.5-1         | qli         | forky          | arm64",
    ]


# --------------------------------------------------------------------------
# -c (component filter)
# --------------------------------------------------------------------------


def test_component_filter(repo, capsys):
    assert run(repo, capsys, ["blob", "-c", "non-free"]).lines == [
        "blob       | 3.0           | qli         | forky          | source, amd64, arm64",
    ]


def test_component_filter_excludes_other_components(repo, capsys):
    res = run(repo, capsys, ["hello", "-c", "non-free"])
    assert res.lines == []
    # main component must not be fetched at all.
    assert not any("/main/" in url for url in res.fetched)


# --------------------------------------------------------------------------
# -s (suite filter)
# --------------------------------------------------------------------------


def test_suite_filter(repo, capsys):
    res = run(repo, capsys, ["hello", "-s", "trixie"])
    # qli-staging has no trixie suite, so only the qli row appears.
    assert res.lines == [
        "hello      | 2.12-1        | qli         | trixie         | source, amd64",
    ]
    # forky must not be fetched from either repository.
    assert not any("/forky/" in url for url in res.fetched)


def test_suite_filter_multiple(repo, capsys):
    # 3 qli rows (2 forky versions + 1 trixie) plus 1 qli-staging forky row.
    assert len(run(repo, capsys, ["hello", "-s", "forky,trixie"]).lines) == 4


# --------------------------------------------------------------------------
# -u (repository selection)
# --------------------------------------------------------------------------


def test_url_staging(repo, capsys):
    res = run(repo, capsys, ["hello", "-u", "qli-staging"])
    assert res.lines == [
        "hello      | 9.99-1        | qli-staging | forky          | source, amd64",
    ]
    assert all(url.startswith(BASE_STAGING) for url in res.fetched)


def test_url_qli_restricts_to_qli(repo, capsys):
    # -u qli must not touch the staging repository at all.
    res = run(repo, capsys, ["hello", "-u", "qli"])
    assert all(url.startswith(BASE + "/") for url in res.fetched)
    assert all("| qli         |" in line for line in res.lines)
    assert not any("qli-staging" in line for line in res.lines)


def test_default_queries_both_repositories(repo, capsys):
    # With no -u, both repositories are fetched and both can produce rows.
    res = run(repo, capsys, ["hello"])
    assert any(url.startswith(BASE + "/") for url in res.fetched)
    assert any(url.startswith(BASE_STAGING + "/") for url in res.fetched)
    assert any("| qli         |" in line for line in res.lines)
    assert any("| qli-staging |" in line for line in res.lines)


def test_invalid_url_rejected(repo, capsys):
    with pytest.raises(SystemExit):
        run(repo, capsys, ["hello", "-u", "debian"])


# --------------------------------------------------------------------------
# fetch minimization: only supported suites are ever requested
# --------------------------------------------------------------------------


def test_only_supported_suites_fetched(repo, capsys):
    fetched = run(repo, capsys, ["hello"]).fetched
    releases = [url for url in fetched if url.endswith("/Release")]
    # Both repositories, both supported suites.
    assert sorted(releases) == sorted(
        f"{REPO_BASE}/dists/{suite}/Release"
        for REPO_BASE in (BASE, BASE_STAGING)
        for suite in ("forky", "trixie")
    )


def test_missing_release_suite_skipped(capsys, monkeypatch):
    # Drop trixie's Release; the suite should simply be skipped, not error.
    partial = dict(REPO)
    del partial[f"{BASE}/dists/trixie/Release"]
    fetched = _patch_repo(monkeypatch, partial)
    lines = run(fetched, capsys, ["hello"]).lines
    assert all("trixie" not in line for line in lines)
    assert any("forky" in line for line in lines)


# --------------------------------------------------------------------------
# Column widths adapt to long names
# --------------------------------------------------------------------------


def test_column_widths_adapt(monkeypatch, capsys):
    long_repo = {
        f"{BASE}/dists/forky/Release": _release(["amd64"], ["main"]),
        f"{BASE}/dists/trixie/Release": _release(["amd64"], ["main"]),
        f"{BASE}/dists/forky/main/binary-amd64/Packages.xz": _packages(
            [("a-very-long-package-name", "1.2.3-4ubuntu5~bpo12+1", "amd64")]
        ),
        f"{BASE}/dists/forky/main/source/Sources.xz": _sources([]),
        f"{BASE}/dists/trixie/main/binary-amd64/Packages.xz": _packages([]),
        f"{BASE}/dists/trixie/main/source/Sources.xz": _sources([]),
    }
    fetched = _patch_repo(monkeypatch, long_repo)
    lines = run(fetched, capsys, ["a-very-long-package-name", "-u", "qli"]).lines
    assert len(lines) == 1
    (line,) = lines
    package, version, repo, suite, arches = [c.strip() for c in line.split("|")]
    assert package == "a-very-long-package-name"
    assert version == "1.2.3-4ubuntu5~bpo12+1"
    assert repo == "qli"
    assert suite == "forky"
    assert arches == "amd64"
