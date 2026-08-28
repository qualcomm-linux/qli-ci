# qli-ci

Shared CI/workflow repository for Qualcomm Linux `pkg-*` package repositories.

`qli-ci` is the split-out home for package reusable workflows and helper scripts
that were historically maintained in `qcom-build-utils`.
It is now the sole source of package lifecycle reusable workflows and package
workflow templates for Qualcomm Linux `pkg-*` repositories.

## Repository Scope

- Reusable package workflows under `.github/workflows/`
- Package workflow templates under `.github/pkg-workflows/`
- Shared helper scripts under `scripts/`
- End-to-end CI architecture documentation under `docs/`

## Primary Reusable Workflows

- `pkg-build-reusable-workflow.yml`
- `pkg-promote-reusable-workflow.yml`
- `pkg-promote-prebuilt-reusable-workflow.yml`
- `pkg-release-reusable-workflow.yml`
- `pkg-upstream-pr-build-reusable-workflow.yml`

## Notes

- Downstream package repos should call reusable workflows from this repository
  with explicit refs.
- Debusine implementation logic remains in
  `qualcomm-linux/debusine-action`; this repo owns package-facing orchestration
  and workflow templates.
- Pull requests should run the `pkg-example e2e loop` workflow to validate
  end-to-end package CI behavior before merge.
- See `AGENTS.md` for detailed operational guidance and workflow contracts.

## License

This repository is licensed under the terms in `LICENSE.txt`.
