# Debusine infrastructure tests

## Scope and purpose

These are deployment tests. Their goal is to verify that our specific Debusine deployment satisfies the requirements of the packaging workflows in `pkg-workflows/debusine/` — that the workspaces, archive suites, workflow templates, and APT repository configuration are all set up correctly and behave as the workflows depend on.

The packaging workflows do not talk to Debusine directly: they call the reusable workflow in `.github/workflows/debusine.yml`, which in turn drives Debusine through the helper scripts in `lib/` (`build`, `release`, and friends). That reusable workflow is therefore part of what these tests exist to protect — the Debusine operations it performs (creating child workspaces, creating archive suites, creating the `debian_pipeline` workflow template, importing source packages, running builds, and running `package-publish` to release into the target workspaces) are exactly the behaviours exercised here. Note: the reusable workflow in `.github/workflow/debusine.yml` and `lib/` are currently in qualcomm-linux/debusine-action, but planned to move into this repository as part of our packaging CI unification effort.

**These tests must be kept in sync with the workflows they protect.** When `packaging-workflows/`, the reusable `.github/workflows/debusine.yml`, or the `lib/` scripts it invokes change what Debusine is asked to do — new suites, new components, new workflow steps, changed access requirements — the tests here must be updated to match.

Testing Debusine itself is out of scope: Debusine has its own test suite, and bugs or regressions in the upstream software are not what these tests are for. The focus is on configuration and deployment properties that could diverge from our requirements — for example, access controls, workspace topology, suite and component names, and repository visibility.

The test suite currently runs under a single Debusine principal and therefore does not cover authorisation or permission behaviour across multiple principals (for example, whether a token with build-only permissions is correctly prevented from publishing, or vice versa).

## What is tested

### Build (`-m build`)

The build phase runs for two vendors:

- **debian** — suites `trixie`, `forky` × components `main`, `contrib`, `non-free`, `non-free-firmware`
- **ubuntu** — suites `resolute`, `stonking` × components `main`, `restricted`, `universe`, `multiverse`

For each (vendor, suite, component) combination:

1. Creates a child workspace under `qli-ci` (the parent workspace used by real CI runs).
2. Creates an archive suite with that vendor's components and architectures `all`/`amd64`/`arm64`.
3. Creates a `debian_pipeline` workflow template with the matching `vendor`.
4. Imports a minimal source package and runs a build.
5. Verifies that the resulting child workspace APT repository:
   - Requires authentication (unauthenticated requests are rejected).
   - Returns a valid `InRelease` file listing the correct component.
   - Lists the built package in `Packages.xz`.
   - Serves the `.deb` file.

### Publish (`-m publish`)

Publish currently runs for **debian only** — there are no ubuntu target
workspaces yet, so ubuntu build results are skipped in the publish phase.

For each debian build result × target workspace (`qli`, `qli-staging`):

1. Runs the `package-publish` workflow from the child CI workspace to the target workspace.
2. Verifies that the target workspace APT repository:
   - Returns a publicly accessible `InRelease` file (no authentication required).
   - Lists the published package in `Packages.xz`.
   - Serves the `.deb` file.

## Dependencies

The tests run inside a `debusine-pkg-builder` container or any host with:

- `python3-debusine >= 0.14.9`
- `python3-debian`, `python3-tenacity`, `python3-requests`
- `dpkg-dev` (for `dpkg-source`, used to create the test source package)

## Credentials

You need three API tokens from your Debusine account (top-right → Tokens):

| Variable | Used for |
|---|---|
| `DEBUSINE_TOKEN` | Read/write on `qli-ci` and child workspaces (build token) |
| `DEBUSINE_PRODUCTION_RELEASE_TOKEN` | Write to `qli` (used for publish tests targeting `qli`) |
| `DEBUSINE_STAGING_RELEASE_TOKEN` | Write to `qli-staging` (used for publish tests targeting `qli-staging`) |

The build token needs permission to create child workspaces under `qli-ci` and to run `debian_pipeline` workflows. The release tokens need permission to run `package-publish` in their respective target workspaces.

## Running the tests

Source `setenv` to be prompted for credentials and have them exported into your shell:

```sh
source ./setenv
```

Then run pytest from anywhere:

```sh
py.test-3 path/to/tests/debusine/
```

`setenv` re-uses any variables already exported in your shell (press Enter to keep them), so you only need to re-enter what has changed between runs.

### Selecting a subset

Run only the build phase:
```sh
py.test-3 -m build .
```

Run only the publish phase:
```sh
py.test-3 -m publish .
```

Run only one suite:
```sh
py.test-3 -k trixie .
```

Run only one vendor (test ids are `<vendor>-<suite>-<component>`):
```sh
py.test-3 -k ubuntu .
py.test-3 -k debian .
```

Stop on first failure:
```sh
py.test-3 -x .
```

These can be combined: `-m build -k trixie` runs only the build tests for trixie.

## Runtime

The build matrix is 16 combinations (8 debian + 8 ubuntu). Publish runs for the 8
debian builds × 2 target workspaces = 16 publish combinations; ubuntu build
results are skipped in the publish phase. Each build takes as long as a real
Debusine build (several minutes). Build results are shared across publish tests
for the same (vendor, suite, component), so the total wall time is dominated by
the 16 builds rather than by the individual test cases.

## Target host

`setenv` defaults to `stage.debusine.qualcomm.com`. Select option 2 for `debusine.qualcomm.com` (production) or option 3 for another host. You can also pre-set `DEBUSINE_HOST` before sourcing `setenv` and press Enter to keep it.

The scope is always `qualcomm`.
