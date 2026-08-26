# qli-ci Test Architecture Instructions

This document describes the current end-to-end test architecture in `qli-ci`.
Its purpose is to keep the human-level test design and the implemented code in
sync, and to make architecture changes reviewable in PRs.

## Scope

Current scope is the `pkg-example` loop test implemented by:

- `.github/workflows/pkg-example-e2e-loop.yml`
- `scripts/pkg_example_e2e_loop.sh`

The suite validates that a given `qli-ci` ref works across the package
lifecycle loop:

`reset -> promote -> promotion PR build -> merge -> release`

for source tags:

- `v1.0.0`
- `v1.1.0`

and lanes:

- Prebuilt promote lane (`qcom/ubuntu/resolute`, prebuilt mode, `v1.0.0`)
- Debian lane (`qcom/debian/latest`)
- Ubuntu lane (`qcom/ubuntu/resolute`)

## Trigger Model

Workflow triggers:

- `pull_request` (`opened`, `reopened`, `synchronize`)
- `workflow_dispatch` (optional explicit ref and PR number)

Run-level concurrency:

- `group: pkg-example-e2e-loop-${{ github.event.pull_request.number || github.ref }}`
- `cancel-in-progress: true`
- plus a dedicated `global-lock` job-level concurrency gate:
  - `group: pkg-example-e2e-loop-global`
  - `cancel-in-progress: false`

This keeps only the latest loop run active when new commits are pushed.

## Cancellation Semantics

Cancellation behavior is part of the e2e contract:

- workflow/job level:
  - Ubuntu lane is guarded so it does not start when the overall run is in
    cancelled state.
- script/runtime level:
  - `scripts/pkg_example_e2e_loop.sh` traps `SIGINT` and `SIGTERM`.
  - long polling/retry loops check cancellation and exit immediately.

Expected behavior:

- manual cancel on the workflow run stops the current lane promptly.
- concurrency preemption (new commit pushed) stops the older run promptly.

## Job Topology

The workflow is intentionally split into sequential jobs for GitHub UI clarity:

1. `pkg-example e2e (global slot)`
2. `pkg-example e2e (prebuilt promote lane)`
3. `pkg-example e2e (debian lane)`
4. `pkg-example e2e (ubuntu lane)`

Execution is sequential, not parallel:

- Prebuilt promote lane runs before Debian lane.
- Debian lane runs before Ubuntu lane.

State handoff:

- Debian uploads `/tmp/pkg-example-e2e-state.json` as artifact
  `pkg-example-e2e-state-<run_id>`.
- Ubuntu downloads this shared state when Debian succeeded.
- If Debian is skipped or artifact download fails, Ubuntu initializes fallback
  state locally and continues.

## Lane Gating

Job-level toggles come from repo variables:

- `ENABLE_PREBUILT_PATH` (`1` or `0`; defaults to enabled when unset)
- `ENABLE_DEBIAN_PATH` (`1` or `0`)
- `ENABLE_UBUNTU_PATH` (`1` or `0`)

Expected behavior:

- Disabled lane job is shown as skipped in UI.
- Step-level lane `if` gates are not the primary control surface.

## AXIOM Gating

AXIOM-related stages are gated by repo variable:

- `AXIOM_ENABLE` (`true` or `false`)

Current behavior:

- `pkg-build-reusable-workflow.yml`:
  - `Upload to S3 (AXIOM testing)` runs only when `AXIOM_ENABLE == 'true'`.
- `pkg-release-reusable-workflow.yml`:
  - `AXIOM_Check` runs only when `AXIOM_ENABLE == 'true'`.
  - `Upload Debs to S3 (Ubuntu, AXIOM testing)` runs only when
    `AXIOM_ENABLE == 'true'`.

When `AXIOM_ENABLE` is `false`, AXIOM stages are skipped and Ubuntu release
keeps only the normal `pkg-release-approval` environment gate.

## Operational Flow

The workflow invokes explicit phase commands from
`scripts/pkg_example_e2e_loop.sh` so each stage is visible in GitHub UI.

Per enabled lane:

1. `prepare-temp-branch`
2. `reset-lane <lane>`
3. Ubuntu-based lanes: `seed-ubuntu`
4. Prebuilt promote lane only: `seed-prebuilt-fixtures ubuntu`
5. For each test tag:
   - `promote-tag <lane> <tag>`
   - `sync-pr-hook <lane> <tag>`
   - `wait-pr-build <lane> <tag>`
   - `merge-pr <lane> <tag>`
   - `release-tag <lane> <tag>`
6. Ubuntu source lane only between first and second tag:
   - `curate-ubuntu-wip-after-first-release`
   - rewrites the top changelog WIP reminder entry to a releasable entry
     so the second release cycle can proceed autonomously.

Post flow (always):

- `write-summary`
- append summary to `$GITHUB_STEP_SUMMARY`
- upsert PR comment (PR events)
- `cleanup`

## State Model

Primary state file:

- `/tmp/pkg-example-e2e-state.json`

It tracks:

- metadata (`qli_ci_ref`, temp branch, promote mode, path toggles,
  overall failure flags)
- lane-level phases (`reset`, `seed`)
- tag-level phases (`promote`, `sync`, `prbuild`, `merge`, `release`)
- promotion PR metadata (`number`, URL, head branch, head SHA)

Summary output:

- `/tmp/pkg-example-e2e-summary.md`

Rendered as a table with lane/tag rows. `reset` and `seed` are displayed on the
first tag row per lane and as `n/a` on subsequent tag rows.

## Credentials and Access Contracts

Required secret:

- `DEB_PKG_BOT_CI_TOKEN`

Used for:

- cloning/pushing temp branches in `pkg-example`
- dispatching and watching downstream workflows
- reading/updating PRs and comments
- merging promotion PRs
- cleanup branch deletion

No silent fallback is expected for this token.

## Downstream PR-Build Dedupe Contract

`sync-pr-hook` can push a commit to the promotion PR branch. That push causes a
`pull_request:synchronize` event in `pkg-example`.

To avoid stale duplicate PR Build runs:

- PR-hook templates include:
  - `concurrency.group: pr-build-${{ github.event.pull_request.number || github.ref }}`
  - `cancel-in-progress: true`
- e2e waits for PR Build using the exact expected PR head SHA.
- if multiple matching runs exist, the latest by `createdAt` is selected.

## Release Approval Gates

Release runs can pause on environment approvals, including:

- `Axiom` (AXIOM check gate)
- `pkg-release-approval` (release gate)

e2e release wait behavior:

- while waiting on a release run, script polls
  `/actions/runs/<id>/pending_deployments`
- it auto-approves environments where `current_user_can_approve == true`
  using `DEB_PKG_BOT_CI_TOKEN`

Environment policy requirement:

- the service bot used by `DEB_PKG_BOT_CI_TOKEN` must be configured as an
  allowed reviewer for required environments.

## Failure Semantics

Operational phases are fail-fast:

- if a required phase fails, dependent phases in that lane/tag are skipped.
- if Ubuntu changelog curation cannot clear the WIP marker after the first
  release, the loop fails before starting the second tag cycle.

Post/reporting phases still run via workflow `if: always()` so artifacts and
summaries are preserved.

## AI Drift-Check Checklist

When validating architecture vs implementation, verify:

1. Topology: global slot -> prebuilt promote lane -> Debian lane -> Ubuntu lane.
2. Job-level lane gates use
   `ENABLE_PREBUILT_PATH`/`ENABLE_DEBIAN_PATH`/`ENABLE_UBUNTU_PATH`.
3. Loop phase order matches this document.
4. Shared state artifact handoff still exists for Debian -> Ubuntu.
5. E2E workflow still uses cancel-in-progress concurrency.
6. Cancellation semantics still hold:
   - Ubuntu lane does not schedule after cancellation.
   - script trap/polling checks still exit promptly on cancel signals.
7. Prebuilt lane seeds local fixture artifacts and sets `PROMOTE_MODE=prebuilt`.
8. PR-hook templates still define PR-level concurrency cancel-in-progress.
9. PR-build wait still keys on PR head SHA and chooses latest run.
10. Release wait still handles pending deployment approvals.
11. `DEB_PKG_BOT_CI_TOKEN` remains a required contract.
12. Post steps still run on `always()` for summary/comment/cleanup.

If any item changes intentionally, update this document in the same PR.
