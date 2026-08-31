# qli-ci — Agent Guidelines

## Purpose

`qli-ci` is the shared workflow repository for Qualcomm Linux package repos.
It is the split-out home for the package reusable workflows and helper scripts
that were historically in `qcom-build-utils`.
It is now the source of truth for package lifecycle reusable workflows and
workflow templates consumed by `pkg-*` repositories.

Primary scope:
- reusable package workflows under `.github/workflows/`
- package workflow templates under `.github/pkg-workflows/`
- shared helper scripts under `scripts/`
- repository configuration tooling under `tools/`

## Current Build/Release Architecture

- Package repos call `pkg-build-reusable-workflow.yml` and
  `pkg-release-reusable-workflow.yml`.
- Those workflows are hybrid:
  - Debian suites (`trixie`, `sid`, `unstable`, `bookworm`, `forky`) use
    `qualcomm-linux/debusine-action` and Debusine builder images by default.
    They can fall back to local `pkg-builder` when Debusine credentials are not
    available or when docker build is forced by input.
  - Ubuntu codenames (`noble`, `questing`, `resolute`, and similar targets)
    use the local `pkg-builder` path with `qli-ci` composite actions.
- Ubuntu release path prepares release state, reuses the build artifacts, gates
  on environment `pkg-release-approval`, then pushes git state and uploads
  artifacts to apt artifactory.
- Debian-path helper entrypoints come from checked-out
  `debusine-action/lib/`:
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
- `test/qcom/ubuntu/resolute`
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
  - main hybrid package build/test entrypoint for package repos
- `.github/workflows/pkg-release-reusable-workflow.yml`
  - hybrid release entrypoint (Debian via Debusine, Ubuntu via pkg-builder)
- `.github/workflows/pkg-promote-reusable-workflow.yml`
  - upstream-to-packaging promotion flow
- `.github/workflows/pkg-promote-prebuilt-reusable-workflow.yml`
  - prebuilt promotion flow
- `.github/workflows/pkg-upstream-pr-build-reusable-workflow.yml`
  - validate upstream PRs against Debian packaging build

## Important Debian/Debusine Helper Entrypoints

The Debian branch of reusable workflows depends on checked-out
`debusine-action/lib/` scripts. If you change those interfaces, update both the
`debusine-action` repo and the call sites in `qli-ci`.

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

`qli-ci` owns a dedicated `pkg-example` loop test.

Source of truth for test architecture:

- `docs/TESTS.instructions.md`

Core implementation entrypoints:

- `.github/workflows/pkg-example-e2e-loop.yml`
- `scripts/pkg_example_e2e_loop.sh`

Keep architecture details, invariants, and drift-check rules in
`docs/TESTS.instructions.md` and update that document in the same PR whenever
the test flow contract changes.

## Editing Guidance

- Keep package-repo callers thin; shared behavior belongs in reusable
  workflows/scripts here.
- Keep Debusine implementation details in `debusine-action` unless `qli-ci`
  orchestration must change.
- When workflow contracts change, update templates under `.github/pkg-workflows/`
  and verify downstream in `pkg-example`.
- Keep changes explicit and reviewable; avoid hidden behavior changes.

## Validation Expectations

For changes touching build/release/promotion contracts:

1. validate edited scripts and workflow YAML locally
2. push branch updates as needed
3. validate Debian path behavior in `pkg-example` (or e2e loop when enabled)
4. validate Ubuntu path behavior in `pkg-example`
5. ensure PR-hook workflow refs remain aligned with the ref under test
