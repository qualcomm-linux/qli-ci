# Debusine infrastructure tests

## Scope and purpose

These are deployment tests. Their goal is to verify that our specific Debusine deployment satisfies the requirements of the packaging workflows in `pkg-workflows/debusine/` — that the workspaces, archive suites, workflow templates, and APT repository configuration are all set up correctly and behave as the workflows depend on.

The packaging workflows do not talk to Debusine directly: they call the reusable workflow in `.github/workflows/debusine.yml`, which in turn drives Debusine through the helper scripts in `lib/` (`build`, `release`, and friends). That reusable workflow is therefore part of what these tests exist to protect — the Debusine operations it performs (creating child workspaces, creating archive suites, creating the `debian_pipeline` workflow template, importing source packages, running builds, and running `package-publish` to release into the target workspaces) are exactly the behaviours exercised here.

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

### Signing keys (`-m signing`)

These verify which keys sign the apt repositories, so the migration of the
InRelease signing keys into AWS KMS can be validated at each phase. They do one
fresh debian build in an ephemeral `qli-ci-*` workspace and one
`package-publish` into each of `qli` and `qli-staging`, then:

- For both `qli` and `qli-staging`: the exact set of keys that signed the freshly
  released public `InRelease` must equal the expected set for the current phase
  (`DEBUSINE_SIGNING_KEY_PHASE`):
  - **phase 1** — the *old* set only,
  - **phase 2** — the *old* ∪ *new* set,
  - **phase 3** — the *new* set only.
- For the ephemeral `qli-ci-*` build workspace: its `InRelease` must be signed by
  a throwaway key that is **none** of the known qli/qli-staging keys in any
  environment.

Set comparison is done at the primary-key-fingerprint level, so a signature made
by a signing subkey still counts as its one parent key.

#### Key data

The expected sets are committed ASCII-armored public keys, per environment and
per workspace:

```
data/signing-keys/<environment>/<workspace>/{old,new}/*.asc
```

- `<environment>` is derived from `DEBUSINE_HOST`: `debusine.qualcomm.com` →
  `production`; `<env>.debusine.qualcomm.com` → `<env>` (`stage`, `test`, `dev2`).
- A "set" is the union of all public keys found across the `.asc` files in a
  group directory.
- The **old** set is populated from the live deployments with
  `data/signing-keys/fetch-old-keys` (a one-off; re-run only to refresh the
  snapshot). An environment/workspace with no published key is skipped, and its
  signing tests then skip.
- For **phase 2+**, drop the new public key(s) into
  `data/signing-keys/<environment>/<workspace>/new/` (one `signing-keys.asc`
  with all new keys, or several `.asc` files).

#### Running a phase

`DEBUSINE_SIGNING_KEY_PHASE` (prompted by `setenv`, default `1`) selects the
expectation. To advance the migration: commit the new keys, bump the phase, and
re-run `-m signing`. A phase whose required `old/` or `new/` directory is empty
skips with a message naming the directory to populate. The ephemeral check is
phase-independent and uses every committed key.

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

Run only the signing-key phase:
```sh
py.test-3 -m signing .
```

Run only the offline unit tests (no credentials needed — these exercise the
signing-key logic against the committed keys and the sample `InRelease` files in
`testdata/`, so you can run them without sourcing `setenv`):
```sh
py.test-3 -m unit .
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
