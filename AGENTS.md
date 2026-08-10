# qli-ci — Agent Guidelines

## Purpose

`qli-ci` is the reusable CI workflow repository for Qualcomm Linux package
repositories.

It is the split-out successor for package reusable workflows and shared CI
scripts that were previously hosted in `qcom-build-utils`.

Primary scope:
- reusable package workflows under `.github/workflows/`
- package workflow templates under `.github/pkg-workflows/`
- shared helper scripts under `scripts/`

## Current Operating Model

Package repositories call reusable workflows from `qualcomm-linux/qli-ci`.

Current key reusable entrypoints:
- `pkg-build-reusable-workflow.yml`
- `pkg-promote-reusable-workflow.yml`
- `pkg-promote-prebuilt-reusable-workflow.yml`
- `pkg-release-reusable-workflow.yml`
- `pkg-upstream-pr-build-reusable-workflow.yml`

Callers are expected to pass explicit `qli-ci-ref` values.

## Contracts And Guardrails

- Keep behavior parity with production caller expectations unless explicitly
  changed by maintainers.
- Do not add silent fallback token behavior where a required secret is part of
  the contract.
- For `qli-ci` checkout in reusable flows, `DEB_PKG_BOT_CI_TOKEN` is expected
  when access requires it.
- Promotion paths must keep PR hook workflow content aligned with the canonical
  template so promotion PR builds use the intended reusable workflow ref.

## Validation Approach

Use `pkg-example` as the primary canary repository.

A dedicated e2e workflow in this repo now validates the full package loop on
PRs:
- reset
- promote
- promotion PR build
- acceptance merge
- release

The e2e loop currently exercises both Debian and Ubuntu branch lanes using
fixed tags to keep runs deterministic.

## Key Files To Review First

- `.github/workflows/pkg-example-e2e-loop.yml`
- `scripts/pkg_example_e2e_loop.sh`
- `.github/workflows/pkg-build-reusable-workflow.yml`
- `.github/workflows/pkg-promote-reusable-workflow.yml`
- `.github/workflows/pkg-release-reusable-workflow.yml`

## Collaboration Notes

When modifying reusable workflow contracts:
- update caller templates under `.github/pkg-workflows/` as needed
- validate downstream behavior in `pkg-example`
- keep changes explicit and reviewable (no hidden behavior changes)
