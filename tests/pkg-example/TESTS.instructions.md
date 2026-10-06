# qli-ci Test Architecture Instructions

This document describes the current end-to-end test architecture in `qli-ci`.
Its purpose is to keep the human-level test design and the implemented code in
sync, and to make architecture changes reviewable in PRs.

## Scope

Current scope is the `pkg-example` loop test implemented by:

- `.github/workflows/pkg-example-e2e-loop.yml`
- `tests/pkg-example/pkg_example_e2e_loop.sh`
- `tests/pkg-example/debian/` (Debian packaging metadata fixture)

The suite validates that a given `qli-ci` ref works across the package
lifecycle loop:

`reset -> promote -> promotion PR build (or Debusine CI check) -> merge ->
release`

for source tags:

- `v1.0.0`
- `v1.1.0`

and lanes:

- Debusine lane (`qcom/debian/latest`, Debusine PR CI check)
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
  - `tests/pkg-example/pkg_example_e2e_loop.sh` traps `SIGINT` and `SIGTERM`.
  - long polling/retry loops check cancellation and exit immediately.

Expected behavior:

- manual cancel on the workflow run stops the current lane promptly.
- concurrency preemption (new commit pushed) stops the older run promptly.

## Job Topology

The workflow is intentionally split into sequential jobs for GitHub UI clarity:

1. `pkg-example e2e (global slot)`
2. `pkg-example e2e (debusine lane)`
3. `pkg-example e2e (fork PR check)`
4. `pkg-example e2e (prebuilt promote lane)`
5. `pkg-example e2e (debian lane)`
6. `pkg-example e2e (ubuntu lane)`

Execution model:

- Debusine lane runs first, before every other lane.
- Fork PR check runs after the debusine lane (success, failure, or skipped),
  so it never races that lane's reset, and before the prebuilt promote lane.
- Prebuilt promote lane runs before Debian lane.
- Debian lane runs before Ubuntu lane.

State handoff:

- Debusine and prebuilt promote lanes are self-contained: each uses its own
  dedicated state/summary files and nothing downstream consumes them.
- Debian uploads `/tmp/pkg-example-e2e-state.json` as artifact
  `pkg-example-e2e-state-<run_id>`.
- Ubuntu downloads this shared state when Debian succeeded.
- If Debian is skipped or artifact download fails, Ubuntu initializes fallback
  state locally and continues.

## Lane Gating

Job-level toggles come from repo variables (`'true'` or `'false'`; each
defaults to `'false'`, i.e. enabled, when unset — the job's `if:` always
defaults via `(vars.X || 'false') != 'true'` so a missing variable never
accidentally disables a lane):

- `DISABLE_DEBUSINE_PATH`
- `DISABLE_FORK_PR_PATH`
- `DISABLE_PREBUILT_PATH`
- `DISABLE_DEBIAN_PATH`
- `DISABLE_UBUNTU_PATH`

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
keeps only the normal `Ubuntu Production` environment gate.

## Reset Model

`pkg-example` is treated as a fully disposable sandbox: every lane's reset
phase wipes and rebuilds it entirely from this repo's own checkout, using
direct git operations against a bot-token clone (the same pattern
`seed-ubuntu`/`seed-prebuilt-fixtures` use), not by dispatching anything that
lives in `pkg-example` itself. This closed a real drift bug: `pkg-example`'s
own `debian-branch-default-content/` had fallen back to the pre-cutover
`qcom-build-utils@development` contract, and the old ref-patch step only
matched `qualcomm-linux/qli-ci/...` lines, so it silently never got
retargeted.

`reset-lane <lane>` performs, in order:

1. Rebuild `pkg-example`'s default branch from scratch as a fresh orphan
   commit, force-pushed. Content comes from this qli-ci checkout: the
   `pkg-workflows/qli-ci/` caller workflows, including the `workflow_run`
   `pkg-pr-build-check.yml` and the default-branch copy of `pkg-pr-hook.yml`
   (all ref-patched to the ref under test), and the full
   Debusine default-branch set (`debusine-daily.yml`,
   `debusine-pr-check.yml`, `debusine-release.yml`, `README.debusine.md`,
   also ref-patched since they call qli-ci's `debusine.yml` reusable
   workflow). `debusine-release.yml` is dispatched against
   `qcom/debian/latest`, not this branch, but it still has to be seeded
   here too: `workflow_dispatch` requires a workflow file to exist on the
   repository's actual default branch to be dispatchable via the API at
   all, regardless of the `--ref` passed at dispatch time.
2. Wipe all tags and all `qcom/*`, `upstream/latest`, and `debian/pr/*`
   branches.
3. Recreate `qcom/debian/latest` as a fresh orphan branch, seeded from
   `tests/pkg-example/debian/` plus a lane-specific PR-hook/release set:
   - debusine lane: `debusine-pr-hook.yml`, `debusine-release.yml`, and
     `README.debusine.md` only (the two workflows ref-patched as above) -
     deliberately no `pkg-pr-hook.yml`, so this lane's promotion PRs (opened
     via `pkg-promote`, since no debusine-specific promote flow exists yet)
     only exercise the standalone debusine PR-hook/check split, not
     `pkg-build-reusable-workflow.yml` too. That's the debian lane's job.
   - debian/ubuntu lanes: `pkg-pr-hook.yml` only (ref-patched) - no Debusine
     files, so their promotion PRs don't spuriously also trigger the
     debusine split. `pkg-pr-hook.yml` is the untrusted half of PR Build;
     the default branch's `pkg-pr-build-check.yml` reacts to it (see PR
     Build Check Contract below).

Every subsequent workflow dispatch in the loop targets `pkg-example`'s real
default branch directly (there is no more `ci/qli-loop/*` temp branch): it is
already rebuilt fresh before every lane runs.

**Accepted trade-off**: `pkg-example`'s default branch git history is
rewritten on every e2e run and no longer represents stable, human-relied-upon
content between runs.

A standalone workflow, `.github/workflows/pkg-example-reset.yml`, exposes the
same reset logic via `workflow_dispatch` for humans who want to reset
`pkg-example` without running the full loop.

## Operational Flow

The workflow invokes explicit phase commands from
`tests/pkg-example/pkg_example_e2e_loop.sh` so each stage is visible in GitHub UI.

Per enabled lane:

1. `prepare-repo` (clone `pkg-example`, configure git identity/remote)
2. `reset-lane <lane>` (see Reset Model above)
3. Ubuntu-based lanes: `seed-ubuntu`
4. Prebuilt promote lane only: `seed-prebuilt-fixtures ubuntu`
5. For each test tag:
   - `promote-tag <lane> <tag>`
   - Debian/Ubuntu lanes: `sync-pr-hook <lane> <tag>` then
     `wait-pr-build <lane> <tag>`
   - Debusine lane: `wait-debusine-check <lane> <tag>` (no `sync-pr-hook`:
     reset already ref-patched `debusine-pr-hook.yml` on `qcom/debian/latest`,
     and promotion PRs branch off it)
   - `merge-pr <lane> <tag>`
   - `release-tag <lane> <tag>` — Debusine lane also dispatches
     `debusine-release.yml` here (see Debusine Lane Check Contract below)
6. Ubuntu source lane only between first and second tag:
   - `curate-ubuntu-wip-after-first-release`
   - rewrites the top changelog WIP reminder entry to a releasable entry
     so the second release cycle can proceed autonomously.
7. Ubuntu source lane only, after the last tag: `fork-pr-check ubuntu` (see
   Fork PR Checks below).

Post flow (always):

- `write-summary`
- append summary to `$GITHUB_STEP_SUMMARY`
- upsert PR comment (PR events, debian/ubuntu lanes)
- `cleanup`

## PR Build Check Contract

PR Build is split the same way as Debusine PR CI. `pkg-pr-hook.yml` ("PR
Build Hook", `pull_request`, on packaging branches) calls
`pkg-pr-build-hook-reusable-workflow.yml`, which has no secrets and builds the
PR head SHA: a source package for Debian suites, a local pkg-builder build for
Ubuntu codenames. `pkg-pr-build-check.yml` ("PR Build Check", `workflow_run`,
seeded on the default branch by reset) resolves the PR from the run's head
SHA, stages the hook's artifact, calls `pkg-build-reusable-workflow.yml` with
`import-pr-build: true`, and posts the `PR Build` commit status on the PR
head SHA.

The default branch also carries an identical copy of `pkg-pr-hook.yml`. It
never triggers there, but GitHub registers a workflow's name from the default
branch copy and `workflow_run` matches on that registered name. Without it,
the hook stays registered under whatever name the default branch last had
(historically `PR Build`), and `pkg-pr-build-check.yml` never fires.

`wait-pr-build` polls `/repos/{repo}/commits/{sha}/status` for the `PR Build`
context (same polling helper, `wait_for_commit_status`, as
`wait-debusine-check`), keyed on the PR head SHA recorded by `sync-pr-hook`.
There is no post-merge PR build.

## Debusine Lane Check Contract

`debusine-pr-check.yml` is `workflow_run`-triggered and only ever fires if it
exists on `pkg-example`'s actual default branch (GitHub will not register a
`workflow_run` listener from any other branch). Reset (above) guarantees
that. It reacts to `debusine-pr-hook.yml` (seeded on `qcom/debian/latest`,
present on the promotion PR since it targets that branch) and posts a
`Debusine CI` commit status on the PR head SHA.

`wait-debusine-check` polls `/repos/{repo}/commits/{sha}/status` for that
context directly — there is no dispatched run to watch, unlike
`wait-pr-build`. Its result is recorded in the tag's `prbuild` phase (the
same field name debian/ubuntu use for their PR-build wait), so `merge-pr` and
`release-tag`'s gating logic is unchanged and shared across all three lanes.

`release-tag` additionally dispatches `debusine-release.yml` directly against
`qcom/debian/latest` with `release=false` for the debusine lane only: it is a
separate, standalone release path (via the `debusine.yml` reusable
workflow) and is not exercised by `pkg-release.yml`'s own internal Debusine helper calls, so it
needs its own validation. `release=false` because the real release already
happened via the preceding `pkg-release.yml` dispatch; this step only
validates the wiring.

## Fork PR Checks

Two checks open a real fork PR, one per PR CI split:

- `fork-pr-check` (standalone job, described below): `qcom/debian/latest`,
  `Debusine CI` status, recorded in `lanes["fork-pr-check"].fork_pr_check`.
- `fork-pr-check ubuntu` (last step of the ubuntu lane, after its second
  release): same steps against `qcom/ubuntu/resolute`, waiting for the
  `PR Build` status, recorded in `lanes.ubuntu.fork_pr_check`. It covers the
  untrusted local pkg-builder build and the trusted import of its build
  area. The Debian PR Build path (source package -> Debusine) is the same
  mechanism the Debusine fork check already exercises with a real fork.

Both use the same fork-bot credentials and gate on `DISABLE_FORK_PR_PATH`.

### Debusine Fork PR Check

`promote-tag`'s promotion PRs are opened by `DEB_PKG_BOT_CI_TOKEN`, which has
write access to `pkg-example`. GitHub therefore never applies the
restricted-token/empty-`pull_requests[]` treatment to those PRs -
`workflow_run.pull_requests[0]` is already populated correctly for them. That
means the tag loop above validates the hook/check *wiring*, but not the two
things that only differ for a genuine fork PR:

- PR identity: `debusine-pr-check.yml`'s `resolve-pr` job must find the PR by
  head `owner:branch`, since `workflow_run.pull_requests` is empty and
  `listPullRequestsAssociatedWithCommit` does not see fork-only commits.
- Untrusted code: the trusted check must never check out or execute the PR.
  The hook builds the source package with the fork's read-only, secret-less
  token, and the check only takes that artifact as data
  (`import_source_package` in `debusine.yml`).
  `actions/checkout` also refuses fork PR checkouts from `workflow_run`, so a
  regression here fails loudly rather than silently.

A regression in either would pass the tag loop above without being caught.

`fork-pr-check` closes that gap with a real fork PR, run as a standalone job
(after the debusine lane, without resetting `pkg-example` itself):

1. Clones `pkg-example` and checks that `origin/qcom/debian/latest` exists. It
   does not reset anything: it runs against the branch as the debusine lane
   (or, with that lane disabled, the last run that reset it) left it, which
   also makes it cheap to iterate on with every other lane disabled.
2. Branches off `qcom/debian/latest`, appends a run-unique comment line to
   `debian/copyright` (a PR needs a diff and each run a fresh head SHA; the
   change must stay inside `debian/`, since anything outside it is an
   unrecorded upstream change for a `3.0 (quilt)` package and `dpkg-source`
   refuses to build), commits it, and pushes to a dedicated fork (`DEB_PKG_FORK_BOT_CI_USER`/`DEB_PKG_FORK_BOT_CI_REPO_NAME`)
   owned by an account with **no write access** to `pkg-example` - that lack of
   write access is what makes GitHub treat the resulting PR as a genuine fork PR
   (restricted token, no secrets on the hook, empty `pull_requests[]` on the
   reacting check).
3. Opens the PR from `<fork-bot>:<branch>` to
   `qualcomm-linux/pkg-example:qcom/debian/latest`, authenticated as the fork
   bot (`DEB_PKG_FORK_BOT_CI_TOKEN`).
4. Waits for the `Debusine CI` commit status on the fork PR's head SHA
   (`wait_for_commit_status`, same polling logic as the tag loop's check).
5. Closes the PR without merging (disposable smoke check, not a real
   promotion) and deletes the throwaway branch on the fork.

Result is recorded in the fork-pr-check job's state file, separate from the
debusine lane's tag loop results, so a failure here is attributable specifically
to the fork-PR path.

**Invariant that must hold forever for this test to mean anything**: the
fork-bot account must never be granted write access to `pkg-example`. If it
ever were (e.g. added as a collaborator), its PRs would stop getting the
restricted treatment and this check would silently stop testing what it
claims to.

## State Model

Primary state file (path varies per job; each job uses its own dedicated file):

- Debusine lane: `/tmp/pkg-example-e2e-debusine-state.json`
- Fork PR check: `/tmp/pkg-example-e2e-fork-pr-check-state.json`
- Prebuilt promote lane: `/tmp/pkg-example-e2e-prebuilt-state.json`
- Debian/Ubuntu lanes: `/tmp/pkg-example-e2e-state.json` (shared)

Each state file tracks:

- metadata (`qli_ci_ref`, promote mode, path toggles, `prepared`, local
  `repo_dir`, overall failure flags)
- lane-level phases (`reset`, `seed`) for `debusine`, `debian`, `ubuntu`
- fork-pr-check lane: `fork_pr_check` phase only (see Fork PR Checks above)
- ubuntu lane: also a lane-level `fork_pr_check` phase
- tag-level phases (`promote`, `sync`, `prbuild`, `merge`, `release`)
- promotion PR metadata (`number`, URL, head branch, head SHA)

`sync` and `seed` stay unused (always `skipped`/`n/a`) for the debusine lane;
`prbuild` holds the Debusine CI check result there instead of a PR-build run
result (see Debusine Lane Check Contract above).

Summary output path matches the state file's job (e.g.
`/tmp/pkg-example-e2e-fork-pr-check-summary.md` for fork-pr-check job).

Rendered as a table with lane/tag rows. `reset` and `seed` are displayed on the
first tag row per lane and as `n/a` on subsequent tag rows. Fork PR check
results are displayed as separate lines.

## Credentials and Access Contracts

Required secret:

- `DEB_PKG_BOT_CI_TOKEN`

Used for:

- cloning `pkg-example` and rebuilding its default/packaging branches from
  scratch on reset
- dispatching and watching downstream workflows
- reading/updating PRs and comments
- merging promotion PRs
- cleanup of the local clone

No silent fallback is expected for this token.

Required for the fork-pr-check job and the ubuntu lane's fork PR check step:

- `DEB_PKG_FORK_BOT_CI_TOKEN` (secret) - PAT for the dedicated fork-bot
  account
- `DEB_PKG_FORK_BOT_CI_USER` (repo variable) - fork-bot account login
- `DEB_PKG_FORK_BOT_CI_REPO_NAME` (repo variable) - name of that account's
  fork of `pkg-example`

The fork-bot account must have **no write access** to `pkg-example` - see the
invariant in Debusine Fork PR Check above.

## Downstream PR-Build Dedupe Contract

`sync-pr-hook` (debian/ubuntu lanes only) can push a commit to the promotion
PR branch. That push causes a `pull_request:synchronize` event in
`pkg-example`.

To avoid stale duplicate PR Build runs:

- PR-hook templates include:
  - `concurrency.group: pr-build-${{ github.event.pull_request.number || github.ref }}`
  - `cancel-in-progress: true`
- e2e waits for the `PR Build` commit status on the exact expected PR head
  SHA, so a cancelled run for the pre-sync SHA is never picked up.
- `pkg-pr-build-check.yml` posts no status for a cancelled hook run.

The debusine lane has no equivalent step: reset already ref-patches
`debusine-pr-hook.yml` on `qcom/debian/latest`, and promotion PRs branch off
it, so there is nothing to re-patch and re-push.

## Release Approval Gates

Release runs can pause on environment approvals, including:

- `Axiom` (AXIOM check gate)
- `Production` (Debian release gate)
- `Ubuntu Production` (Ubuntu release gate)

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

1. Topology: global slot -> debusine lane -> fork PR check ->
   prebuilt promote lane -> Debian lane -> Ubuntu lane.
2. Job-level lane gates use
   `DISABLE_DEBUSINE_PATH`/`DISABLE_FORK_PR_PATH`/`DISABLE_PREBUILT_PATH`/`DISABLE_DEBIAN_PATH`/`DISABLE_UBUNTU_PATH`,
   each defaulting to enabled (`'false'`) when unset.
3. Loop phase order matches this document.
4. Shared state artifact handoff still exists for Debian -> Ubuntu.
5. E2E workflow still uses cancel-in-progress concurrency.
6. Cancellation semantics still hold:
   - Ubuntu lane does not schedule after cancellation.
   - script trap/polling checks still exit promptly on cancel signals.
7. Prebuilt lane seeds local fixture artifacts and sets `PROMOTE_MODE=prebuilt`.
8. `reset-lane` rebuilds `pkg-example`'s default branch and `qcom/debian/latest`
   from this repo's own `pkg-workflows/*` and `tests/pkg-example/*` content —
   it must never dispatch anything that lives in `pkg-example` itself.
9. PR-hook templates still define PR-level concurrency cancel-in-progress.
10. PR-build wait still polls the `PR Build` commit status context on the PR
    head SHA, not a dispatched or hook run.
11. Debusine check wait still polls the `Debusine CI` commit status context,
    not a dispatched run.
12. Release wait still handles pending deployment approvals.
13. `DEB_PKG_BOT_CI_TOKEN` remains a required contract.
14. Fork PR check job runs after the debusine lane and before the prebuilt
    promote lane, so no reset can race it.
15. Fork PR check requires only `prepare-repo` (no reset); checks that
    `qcom/debian/latest` exists.
16. Post steps still run on `always()` for summary/comment/cleanup.
17. The `DEB_PKG_FORK_BOT_CI_USER` account still has no write access to
    `pkg-example` - if it does, `fork-pr-check` silently stops exercising a
    real fork PR.
18. `pkg-pr-build-hook-reusable-workflow.yml` still declares no `secrets:`,
    resolves its build target from `github.base_ref` only, and builds
    `pull_request.head.sha`; `pkg-pr-build-check.yml` never checks out PR
    code and only calls `pkg-build-reusable-workflow.yml` with
    `import-pr-build: true`.
19. The ubuntu lane still ends with `fork-pr-check ubuntu`, gated on
    `DISABLE_FORK_PR_PATH`.
20. `pkg-workflows/qli-ci/pkg-pr-hook.yml` and
    `pkg-workflows/debian/pkg-pr-hook.yml` are still byte-identical, and
    their `name:` still matches `pkg-pr-build-check.yml`'s
    `workflow_run.workflows` entry.

If any item changes intentionally, update this document in the same PR.
