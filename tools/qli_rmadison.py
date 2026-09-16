#!/usr/bin/env python3
"""qli-rmadison -- rmadison-style package version listing for the qli apt repositories.

Queries the custom qli / qli-staging apt repositories and prints, per
package, which version is present in each suite and for which
architectures, in the same column format as rmadison(1).

This is entirely a workaround for Debusine not supporting the rmadison API.
Upstream bug: https://salsa.debian.org/freexian-team/debusine/-/work_items/1162
"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

import argparse
import asyncio
import io
import lzma
import sys
import time
from collections import defaultdict

import httpx
from debian import deb822
from debian.debian_support import Version

# Hardcoded apt repositories, over HTTPS. Signatures are not checked: we
# fetch Release/Packages/Sources directly and never consult Release.gpg.
REPOS = {
    "qli": "https://deb.debusine.qualcomm.com/qualcomm/qli",
    "qli-staging": "https://deb.debusine.qualcomm.com/qualcomm/qli-staging",
}

# Hardcoded set of supported suites.
RELEASES = ["forky", "trixie"]


def parse_multi(values):
    """Flatten repeated / comma- or space-separated option values into a set.

    Returns None when the option was not given (meaning "no filter").
    """
    if not values:
        return None
    out = set()
    for value in values:
        out.update(value.replace(",", " ").split())
    return out


async def fetch(client, url):
    """Fetch a URL, returning its bytes, or None if it does not exist (404)."""
    response = await client.get(url)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def main():
    parser = argparse.ArgumentParser(
        description="rmadison-style version listing for the qli apt repositories.",
    )
    parser.add_argument(
        "-a", "--architecture", action="append", metavar="ARCH",
        help="only show info for ARCH(s) (comma/space separated; 'source' allowed)",
    )
    parser.add_argument(
        "-c", "--component", action="append", metavar="COMPONENT",
        help="only show info for COMPONENT(s) (comma/space separated)",
    )
    parser.add_argument(
        "-s", "--suite", action="append", metavar="SUITE",
        help="only show info for SUITE(s) (comma/space separated)",
    )
    parser.add_argument(
        "-S", "--source-and-binary", dest="source_and_binary",
        action="store_true",
        help="also show the binary children of queried source packages",
    )
    parser.add_argument(
        "-u", "--url", default=None, choices=sorted(REPOS),
        help="repository to query (default: query all of %s)" % ", ".join(REPOS),
    )
    parser.add_argument(
        "-1", dest="only_newest", action="store_true",
        help="hide any listing that also has a version that is strictly newer",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="report each URL being fetched and how long fetches take (on stderr)",
    )
    parser.add_argument("packages", nargs="+", metavar="PACKAGE")
    args = parser.parse_args()
    return asyncio.run(run_query(args))


async def run_query(args):
    arch_filter = parse_multi(args.architecture)
    comp_filter = parse_multi(args.component)
    suite_filter = parse_multi(args.suite)

    suites = [s for s in RELEASES if suite_filter is None or s in suite_filter]
    packages = set(args.packages)
    want_source = arch_filter is None or "source" in arch_filter

    # Which repositories to query: the one named by -u, or all of them.
    repos = [args.url] if args.url is not None else list(REPOS)

    # (package, version, repo, suite) -> set of architecture tokens ("source"
    # plus binary architectures / "all").
    matches = defaultdict(set)

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        wall_start = time.monotonic()

        async def timed_fetch(url):
            """Fetch url concurrently, reporting URL + elapsed time if verbose."""
            if args.verbose:
                print(f"Fetching {url}", file=sys.stderr, flush=True)
            start = time.monotonic()
            data = await fetch(client, url)
            elapsed = time.monotonic() - start
            if args.verbose:
                status = "not found" if data is None else f"{len(data)} bytes"
                print(f"  {url} -> {status} ({elapsed:.2f}s)", file=sys.stderr)
            return data

        async def gather(urls):
            """Fetch all urls concurrently, returning a {url: data} mapping."""
            results = await asyncio.gather(*(timed_fetch(u) for u in urls))
            return dict(zip(urls, results))

        # Round 1: fetch every repo's per-suite Release file in parallel.
        # release url -> (repo, suite)
        release_meta = {
            f"{REPOS[repo]}/dists/{suite}/Release": (repo, suite)
            for repo in repos
            for suite in suites
        }
        releases = await gather(list(release_meta))

        # From each Release, enumerate the index files we need, honouring the
        # architecture / component filters. Collect them all, then fetch the
        # whole set concurrently in one round.
        index_urls = []
        # index url -> (kind, repo, suite, arch); kind is "binary" or "source".
        index_meta = {}
        for release_url, (repo, suite) in release_meta.items():
            release = releases.get(release_url)
            if release is None:
                continue
            release = deb822.Deb822(io.BytesIO(release))
            repo_base = REPOS[repo]

            architectures = release.get("Architectures", "").split()
            components = release.get("Components", "").split()
            if arch_filter is not None:
                architectures = [a for a in architectures if a in arch_filter]
            if comp_filter is not None:
                components = [c for c in components if c in comp_filter]

            for component in components:
                for arch in architectures:
                    url = (
                        f"{repo_base}/dists/{suite}/{component}"
                        f"/binary-{arch}/Packages.xz"
                    )
                    index_urls.append(url)
                    index_meta[url] = ("binary", repo, suite, arch)
                if want_source:
                    url = f"{repo_base}/dists/{suite}/{component}/source/Sources.xz"
                    index_urls.append(url)
                    index_meta[url] = ("source", repo, suite, None)

        # Round 2: fetch every Packages/Sources index in parallel.
        indexes = await gather(index_urls)

        if args.verbose:
            wall = time.monotonic() - wall_start
            print(f"All fetches took {wall:.2f}s", file=sys.stderr)

    # Parse the fetched indexes (order is deterministic: index_urls order).
    for url in index_urls:
        data = indexes.get(url)
        if data is None:
            continue
        kind, repo, suite, arch = index_meta[url]
        raw = io.BytesIO(lzma.decompress(data))
        if kind == "binary":
            for stanza in deb822.Packages.iter_paragraphs(raw, use_apt_pkg=False):
                package = stanza.get("Package")
                matched = package in packages
                if not matched and args.source_and_binary:
                    # -S: match binaries whose source package was queried.
                    # The Source field is "name" or "name (version)".
                    source = stanza.get("Source", package).split(" ", 1)[0]
                    matched = source in packages
                if not matched:
                    continue
                token = stanza.get("Architecture") or arch
                matches[(package, stanza["Version"], repo, suite)].add(token)
        else:
            for stanza in deb822.Sources.iter_paragraphs(raw, use_apt_pkg=False):
                if stanza.get("Package") not in packages:
                    continue
                key = (stanza["Package"], stanza["Version"], repo, suite)
                matches[key].add("source")

    if args.only_newest:
        # Within each (package, repo, suite), keep only the newest version(s).
        #
        # Edge case: rows are keyed by the raw version *string*, but ordering
        # uses Version(). Two spellings that are distinct strings yet equal
        # under Debian version semantics (e.g. "2.0" and "0:2.0", the epoch
        # being implicitly 0) are never collapsed, and both compare == to the
        # newest, so both survive -- defeating -1 for that pair. Real indexes
        # don't carry two spellings of one version per suite, so this is left
        # unfixed; the fix would be to key identity by Version() too.
        newest = {}
        for package, version, repo, suite in matches:
            key = (package, repo, suite)
            if key not in newest or Version(version) > Version(newest[key]):
                newest[key] = version
        matches = {
            (package, version, repo, suite): tokens
            for (package, version, repo, suite), tokens in matches.items()
            if Version(version) == Version(newest[(package, repo, suite)])
        }

    rows = []
    for (package, version, repo, suite), tokens in matches.items():
        ordered = (["source"] if "source" in tokens else []) + sorted(
            tokens - {"source"}
        )
        rows.append((package, version, repo, suite, ", ".join(ordered)))

    # Order by package, then version (ascending), then repo, then suite.
    rows.sort(key=lambda r: (r[0], Version(r[1]), r[2], RELEASES.index(r[3])))

    if not rows:
        return
    width_pkg = max([10] + [len(r[0]) for r in rows])
    width_ver = max([13] + [len(r[1]) for r in rows])
    width_repo = max([11] + [len(r[2]) for r in rows])
    width_suite = max([14] + [len(r[3]) for r in rows])
    for package, version, repo, suite, arches in rows:
        print(
            f"{package:<{width_pkg}} | {version:<{width_ver}} | "
            f"{repo:<{width_repo}} | {suite:<{width_suite}} | {arches}"
        )


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as exc:
        sys.exit(f"qli-rmadison: network error: {exc}")
    except BrokenPipeError:
        # stdout was closed early (e.g. piped into `head`); exit quietly
        # rather than dumping a traceback onto the listing.
        sys.exit(0)
