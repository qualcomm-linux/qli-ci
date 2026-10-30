# Debusine infrastructure tests

## Scope and purpose

These are deployment tests. Their goal is to verify that our specific Debusine deployment satisfies the requirements of the packaging workflows in `pkg-workflows/debusine/` — that the workspaces, archive suites, workflow templates, and APT repository configuration are all set up correctly and behave as the workflows depend on.

The packaging workflows do not talk to Debusine directly: they call the reusable workflow in `.github/workflows/debusine.yml`, which in turn drives Debusine through the helper scripts in `lib/` (`build`, `release`, and friends). That reusable workflow is therefore part of what these tests exist to protect — the Debusine operations it performs (creating child workspaces, creating archive suites, creating the `debian_pipeline` workflow template, importing source packages, running builds, and running `package-publish` to release into the target workspaces) are exactly the behaviours exercised here.

**These tests must be kept in sync with the workflows they protect.** When `packaging-workflows/`, the reusable `.github/workflows/debusine.yml`, or the `lib/` scripts it invokes change what Debusine is asked to do — new suites, new components, new workflow steps, changed access requirements — the tests here must be updated to match.

Testing Debusine itself is out of scope: Debusine has its own test suite, and bugs or regressions in the upstream software are not what these tests are for. The focus is on configuration and deployment properties that could diverge from our requirements — for example, access controls, workspace topology, suite and component names, and repository visibility.

The test suite drives three distinct Debusine service users, one per task, and
verifies both that each user can perform its own task and that the other two
users are correctly prevented from performing it:

- **Build user** — creates a child workspace under `qli-ci`, uploads the source
  package, and builds it in that child workspace.
- **Staging release user** — releases built packages to `qli-staging`.
- **Production release user** — releases built packages to `qli`.

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

### Permissions (`-m permissions`)

Verifies the authorisation boundary between the three service users. The release
rights differ per user, but all three share the `qli-ci` child-workspace
operations:

- **Create child workspace under `qli-ci`** — allowed to the build user and to
  both release users. (The build user's grant is covered by the build phase;
  this phase asserts each release user is also permitted.)
- **Release to `qli-staging`** — denied to the build user and the production
  release user.
- **Release to `qli`** — denied to the build user and the staging release user.

Each check is resolved synchronously by `workflow_create`; no build or publish
workflow is run. The release cases reuse the shared build results (debian only),
so this phase adds no extra builds.

## Dependencies

The tests run inside a `debusine-pkg-builder` container or any host with:

- `python3-debusine >= 0.14.9`
- `python3-debian`, `python3-tenacity`, `python3-requests`
- `dpkg-dev` (for `dpkg-source`, used to create the test source package)

## Credentials

You need three API tokens, one per service user (top-right → Tokens):

| Variable | Service user | Used for |
|---|---|---|
| `DEBUSINE_TOKEN` | Build | Read/write on `qli-ci` and child workspaces (create child workspace, upload, build) |
| `DEBUSINE_STAGING_RELEASE_TOKEN` | Staging release | Write to `qli-staging` (publish tests targeting `qli-staging`) |
| `DEBUSINE_PRODUCTION_RELEASE_TOKEN` | Production release | Write to `qli` (publish tests targeting `qli`) |

The build token needs permission to create child workspaces under `qli-ci` and to run `debian_pipeline` workflows. Each release token needs permission to run `package-publish` in its own target workspace — and, mirroring production `lib/release`, **read** access to the child workspaces under `qli-ci` so it can resolve the suite collection to publish from.

`DEBUSINE_USER` is the **build** user's APT/basic-auth username. It is the only username the suite needs: it is used solely for the two HTTP Basic reads the build user performs (fetching the build signing key and reading the child workspace's APT repository). The release users are token-only, and the released target repositories are verified over unauthenticated (public) APT.

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

Run only the permissions phase:
```sh
py.test-3 -m permissions .
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
