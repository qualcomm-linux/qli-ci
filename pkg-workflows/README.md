This directory holds the stub workflow files that get copied into each
managed `pkg-*` repository's `.github/workflows/`, per
`tools/repo-configs.json`'s `workflow_files` config (see
`tools/update-workflow-files`). `debusine-daily.yml`,
`debusine-pr-check.yml`, `pkg-build.yml`, `pkg-promote-prebuilt.yml`,
`pkg-promote.yml`, and `pkg-release.yml` belong on the default branch.
`debusine-pr-hook.yml`, `debusine-release.yml`, and
`README.debusine.md` are copied to a fixed, Debian-only set of legacy
packaging branches; `pkg-pr-hook.yml` is copied to every packaging
branch matching the naming schema (debian and ubuntu alike).

When these files are updated, they must also be updated in every
managed `pkg-*` repository. Currently this process is manual. We
[agreed](https://github.com/qualcomm-linux/debusine-action/pull/15)
that once landed into this repository through a peer-reviewed PR, no
further PRs are required to update the corresponding workflow files in
the pkg-* repositories.

To temporarily disable Debusine workflow execution for a repository
without removing these files, set the repository Actions variable
`DEBUSINE_WORKFLOWS_DISABLED` to `true`. Delete the variable, or set it
to any other value, to re-enable the workflows.
