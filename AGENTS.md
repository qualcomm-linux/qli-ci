# qli-ci — Agent Guidelines

## Purpose

`qli-ci` is the shared workflow repository for Qualcomm Linux package repos.
It is the split-out home for the package reusable workflows and helper scripts
that were historically in `qcom-build-utils`.
It is now the source of truth for package lifecycle reusable workflows and
workflow templates consumed by `pkg-*` repositories.

Primary scope:
- reusable package workflows under `.github/workflows/`
- package workflow templates under `pkg-workflows/`
- shared helper scripts under `scripts/`

## Current Build/Release Architecture

- Package repos call `pkg-build-reusable-workflow.yml` and
  `pkg-release-reusable-workflow.yml`.
- Those workflows are hybrid:
  - Debian suites (`trixie`, `sid`, `unstable`, `bookworm`, `forky`) use
    the Debusine helpers in `lib/` and Debusine builder images by default.
    They can fall back to local `pkg-builder` when Debusine credentials are not
    available or when docker build is forced by input.
  - Ubuntu codenames (`noble`, `questing`, `resolute`, and similar targets)
    use the local `pkg-builder` path with `qli-ci` composite actions.
- Ubuntu release path prepares release state, reuses the build artifacts, gates
  on environment `Ubuntu Production`, then pushes git state and uploads
  artifacts to apt artifactory. Debian release path gates on environment
  `Production`.
- Debian-path helper entrypoints come from this repo's `lib/`, checked out
  at `qli-ci-ref` as `qli-ci/lib/`:
  - `prepare-release`
  - `generate-source-package`
  - `build`
  - `generate-apt-config`
  - `release`
  - `push-release`

## Workflow Naming Convention

- `pkg-*` workflow names are package lifecycle flows (`build`, `promote`,
  `release`, package PR hooks).
- `qcom-*` names are reserved for qcom-wide infra/preflight workflows.
- Keep this naming split so package-repo automation remains easy to identify.

## Build Branch Convention (Caller Contract)

For `pkg-build-reusable-workflow.yml`, callers pass `debian-ref` where the last
two `/`-delimited fields are:

- `<family>/<suite>`

Expected values:

- `family`: `debian` or `ubuntu`
- `suite`: distro codename/suite such as `sid`, `bookworm`, `noble`,
  `resolute`

Examples:

- `qcom/debian/latest` (normalized to suite `sid`)
- `qcom/debian/bookworm`
- `qcom/ubuntu/resolute`
- `tests/qcom/ubuntu/resolute`
- `ubuntu/resolute`
- `dev/whatever/yo/debian/trixie`

Invalid examples:

- `resolute`
- `ubuntu`
- `ubuntu-resolute`

`pkg-build-reusable-workflow.yml` resolves family/suite from `debian-ref`
(without a separate suite input). For PR validation where `debian-ref` is a
transient branch (for example `debian/pr/*`), routing can fall back to
`github.base_ref`.

## Reusable Workflow Contracts

- Callers should pass explicit `qli-ci-ref` values.
- Preserve strict parity for existing caller behavior unless a design change is
  explicitly requested.
- Do not introduce silent fallbacks for required credentials.
- `DEB_PKG_BOT_CI_TOKEN` is required where reusable workflows/scripts clone
  internal repos or need write operations.
- Do not use `qcom-build-utils-ref` in caller contracts; package callers should
  target `qli-ci` reusable workflows directly.

## Important Workflows

- `.github/workflows/pkg-build-reusable-workflow.yml`
  - main hybrid package build/test entrypoint for package repos; with
    `import-pr-build` it is the trusted half of PR Build
- `.github/workflows/pkg-pr-build-hook-reusable-workflow.yml`
  - untrusted half of PR Build, called from the `pkg-pr-hook.yml`
    `pull_request` stub: builds the PR head (Debian: source package;
    Ubuntu: local pkg-builder build) with no secrets and uploads it
- `.github/workflows/pkg-release-reusable-workflow.yml`
  - hybrid release entrypoint (Debian via Debusine, Ubuntu via pkg-builder)
- `.github/workflows/pkg-promote-reusable-workflow.yml`
  - upstream-to-packaging promotion flow
- `.github/workflows/pkg-promote-prebuilt-reusable-workflow.yml`
  - prebuilt promotion flow
- `.github/workflows/pkg-upstream-pr-build-reusable-workflow.yml`
  - validate upstream PRs against Debian packaging build
- `.github/workflows/debusine.yml`
  - standalone Debusine reusable workflow called by the `pkg-workflows/debusine/`
    stubs; checks out `lib/` at its `qli-ci-ref` input
- `.github/workflows/debusine-source-package.yml`
  - untrusted half of Debusine PR CI, called from the `pull_request` hook
    stub: generates the source package from the PR head and uploads it
- `pkg-workflows/debusine/*`
  - source Debusine stub workflows copied into managed `pkg-*` repos
- `pkg-workflows/debian/pkg-pr-hook.yml` (packaging branches) and
  `pkg-workflows/qli-ci/pkg-pr-build-check.yml` (default branch)
  - PR Build hook/check pair, synced by `workflows_sync.yml`
  - `pkg-workflows/qli-ci/pkg-pr-hook.yml` is a byte-identical default-branch
    copy of the hook. It never triggers there; it keeps GitHub's registered
    workflow name at "PR Build Hook", which the check's `workflow_run`
    trigger matches on (GitHub takes the name from the default branch copy)

## PR Build Hook/Check Split

PR Build follows the same untrusted/trusted split as Debusine PR CI:

- `pkg-pr-hook.yml` ("PR Build Hook", `pull_request`) calls
  `pkg-pr-build-hook-reusable-workflow.yml`, which declares no secrets.
  It resolves family/suite from `github.base_ref` only and builds
  `pull_request.head.sha`: a `source-package-0` artifact for Debian suites, a
  `docker-build-area` artifact for Ubuntu codenames.
- `pkg-pr-build-check.yml` ("PR Build Check", `workflow_run`, default branch)
  resolves the PR from `workflow_run.head_sha` the same way
  `debusine-pr-check.yml` does, copies the hook artifact into its own run
  (`untrusted-source-package-0` / `untrusted-docker-build-area`), and calls
  `pkg-build-reusable-workflow.yml` with `import-pr-build: true`. It owns the
  `PR Build` commit status.
- With `import-pr-build`, `pkg-build-reusable-workflow.yml` never checks out
  `debian-ref` (only uses it for suite routing), validates the Debian artifact
  with `lib/import-source-package` before submitting it to Debusine, and
  validates the Ubuntu build archive as a flat list of checksummed files. It
  refuses `release`, and requires `DEBUSINE_TOKEN` for Debian rather than
  falling back to a local build.
- There is no post-merge PR build; release builds rebuild the branch.
- Keep the hook reusable secret-free. Anything that needs a secret belongs on
  the trusted side and must only consume the artifact as data.

## Important Debian/Debusine Helper Entrypoints

The Debian branch of reusable workflows (and `debusine.yml`) depends on this
repo's `lib/` scripts, checked out at `qli-ci-ref`. If you change those
interfaces, update all call sites in `.github/workflows/`. These were moved here
(with history) from `qualcomm-linux/debusine-action`.

## Debusine Reusable Workflow (`debusine.yml`)

`.github/workflows/debusine.yml` is the standalone reusable workflow called by
the `pkg-workflows/debusine/` stubs. It is split into `resolve`,
`source-package`, `build`, and `release` jobs.

- Source-package generation runs in the suite-matched builder image
  `ghcr.io/qualcomm-linux/debusine-pkg-builder:<suite>`; Debusine client,
  build orchestration, and release steps run in the `trixie` builder image.
  Builder images are still published from `qualcomm-linux/debusine-action`.
- Branch-to-suite resolution is explicit in `resolve`:
  - `qli/debian/latest`, `qli-staging/debian/latest`, or `qcom/debian/latest`
    (transitional) -> `forky`
  - `qli/debian/trixie`, `qli-staging/debian/trixie`, or `qcom/debian/trixie`
    (transitional) -> `trixie`
- Branch prefix also determines the package version string identifier:
  - `qli/` or `qcom/` (transitional) -> `qli`
  - `qli-staging/` -> `qli+staging`

Caller inputs: `target_branch`, `source_ref`, `release`, `qli-ci-ref`,
`debusine-parent-workspace` (defaults to `qli-ci`), `workflow_kind`,
`job_index`, `import_source_package`. Required secrets: `DEBUSINE_USER`, `DEBUSINE_TOKEN`,
`DEBUSINE_RELEASE_TOKEN`.

Design decisions to preserve:

- Callers pass `qli-ci-ref` explicitly and internal `actions/checkout` steps
  use it for `lib/`. Do not reintroduce workflow-SHA lookup from the job OIDC
  token (or `id-token: write` in callers solely for that); this was replaced
  in response to review feedback about depending on undocumented token claims.
- Keep `debusine-release.yml` branch-local: it lives on packaging branches,
  derives the release target from `github.ref_name`, and does not ask for a
  separate `target-branch` input.
- Preserve the source-package flow: generate from the checked-out packaging
  tree, stage files from the generated `.changes`, upload as the
  `source-package` artifact, and restore into the build workspace root before
  Debusine import/build. Do not bypass it with ad hoc file moves.
- Keep the `resolve` suite map in sync with the `check-branches` candidates in
  `pkg-workflows/debusine/debusine-daily.yml` and in
  `debusine-source-package.yml`.
- Never check out or execute PR code in a trusted (`workflow_run`) context
  (this also applies to the PR Build pair, see above).
  PR CI builds the source package in the untrusted `pull_request` hook via
  `debusine-source-package.yml`; `debusine-pr-check.yml` copies that artifact
  into its own run and calls `debusine.yml` with `import_source_package: true`,
  which skips the PR checkout and validates the artifact as data with
  `lib/import-source-package`. Do not opt into `allow-unsafe-pr-checkout` or
  `pull_request_target` to work around this. `import_source_package` refuses
  `release`.

When changing `debusine.yml` contracts, also update
`pkg-workflows/debusine/*` (including `README.md` and `README.debusine.md`)
and `tools/repo-management/debug_branch_mod.py`, then resync managed `pkg-*`
repos with `tools/repo-management/update-workflow-files`.

## Do Not Reintroduce

The following historical artifacts were intentionally removed from shared
workflow orchestration and should stay out unless there is an explicit design
decision to bring them back:

- local Debusine wrapper workflows such as `qcom-debusine-reusable-workflow.yml`
- local Debusine image publishing workflows and
  `Dockerfiles/debusine-builder/`
- copied legacy `scripts/ci/` Debusine helper trees
- stale `*.old` workflow snapshots

## pkg-example End-to-End Test Suite

`qli-ci` owns a dedicated `pkg-example` loop test. `pkg-example` itself is a
fully disposable sandbox: every lane rebuilds its default branch and
`qcom/debian/latest` from scratch from this repo's own `pkg-workflows/*`
templates and `tests/pkg-example/*` fixtures, and never depends on anything
committed in `pkg-example`. Lanes run in order: debusine, fork PR check,
prebuilt promote, Debian, Ubuntu. The fork-pr-check job and the
Ubuntu lane's final step each open a real fork PR (via a dedicated
write-access-less bot account) to validate `debusine-pr-check.yml`'s and
`pkg-pr-build-check.yml`'s fork-PR handling, not just their wiring.

Source of truth for test architecture:

- `tests/pkg-example/TESTS.instructions.md`

Core implementation entrypoints:

- `.github/workflows/pkg-example-e2e-loop.yml`
- `.github/workflows/pkg-example-reset.yml` (standalone human-triggerable
  reset, reusing the same reset logic)
- `tests/pkg-example/pkg_example_e2e_loop.sh`
- `tests/pkg-example/debian/` (Debian packaging metadata fixture, seeded onto
  `qcom/debian/latest` on every reset)

Keep architecture details, invariants, and drift-check rules in
`tests/pkg-example/TESTS.instructions.md` and update that document in the same PR whenever
the test flow contract changes.

## Editing Guidance

- Keep package-repo callers thin; shared behavior belongs in reusable
  workflows/scripts here.
- Keep Debusine implementation details in `lib/`, separate from `qli-ci`
  package-facing orchestration.
- When workflow contracts change, update templates under `pkg-workflows/`
  and verify downstream in `pkg-example`.
- Keep changes explicit and reviewable; avoid hidden behavior changes.

## Validation Expectations

For changes touching build/release/promotion contracts:

1. validate edited scripts and workflow YAML locally
2. push branch updates as needed
3. validate Debian path behavior in `pkg-example` (or e2e loop when enabled)
4. validate Ubuntu path behavior in `pkg-example`
5. ensure PR-hook workflow refs remain aligned with the ref under test
