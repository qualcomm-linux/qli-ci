This directory holds the workflow stubs that
`tools/repo-management/update-workflow-files` deploys into `pkg-*`
repositories. See `tools/repo-management/SPECIFICATION.md` ("Workflow
Files") for which branches get which files. Every file is deployed to
`.github/workflows/` under its own name, so this directory is kept flat and
each name means exactly one file.

The `debusine-*.yml` files are a set of four workflows that should be imported
into each pkg-* repository to enable Debusine CI. `debusine-daily.yml` and
`debusine-pr-check.yml` belong in the default branch. `debusine-pr-hook.yml`
and `debusine-release.yml` must also be copied to each enabled packaging
(`qli/debian/*`, `qli-staging/debian/*`, or `qcom/debian/*` during transition)
branch. `README.debusine.md` should also be copied into
`.github/workflows/` in each branch touched to help future maintainers.

When these files are updated, they must also updated in every "managed" pkg-*
repository. Currently this process is manual. We
[agreed](https://github.com/qualcomm-linux/debusine-action/pull/15) that once
landed into this repository through a peer-reviewed PR, no further PRs are
required to update the corresponding workflow files in the pkg-* repositories.

To temporarily disable Debusine workflow execution for a repository without
removing these files, set the repository Actions variable
`DEBUSINE_WORKFLOWS_DISABLED` to `true`. Delete the variable, or set it to any
other value, to re-enable the workflows.
