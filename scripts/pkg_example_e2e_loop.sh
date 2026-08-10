#!/usr/bin/env bash
set -uo pipefail

PKG_REPO="${PKG_EXAMPLE_REPO:-qualcomm-linux/pkg-example}"
PKG_BASE_REF="${PKG_EXAMPLE_BASE_REF:-qli-ci}"
SUMMARY_FILE="${SUMMARY_FILE:-${PWD}/pkg-example-e2e-summary.md}"
WORKDIR="$(mktemp -d)"
RUN_ID_FALLBACK="${GITHUB_RUN_ID:-manual}"
IS_FORK_PR="${IS_FORK_PR:-false}"
QLI_CI_REF="${QLI_CI_REF:-}"
QLI_CI_PR_NUMBER="${QLI_CI_PR_NUMBER:-}"
BOT_TOKEN="${BOT_TOKEN:-}"

TAGS=("v1.0.0" "v1.1.0")
LANES=("debian" "ubuntu")
declare -A LANE_BRANCH=(
  [debian]="qcom/debian/latest"
  [ubuntu]="qcom/ubuntu/resolute"
)

cleanup_local() {
  rm -rf "$WORKDIR"
}
trap cleanup_local EXIT

write_output() {
  local key="$1"
  local value="$2"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf '%s=%s\n' "$key" "$value" >> "$GITHUB_OUTPUT"
  fi
}

iso_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
}

gh_bot() {
  GH_TOKEN="$BOT_TOKEN" gh "$@"
}

status_emoji() {
  case "$1" in
    success) echo "✅" ;;
    failure) echo "❌" ;;
    skipped) echo "⏭️" ;;
    n/a) echo "➖" ;;
    *) echo "⚪" ;;
  esac
}

format_cell() {
  local status="$1"
  local url="$2"
  local emoji
  emoji="$(status_emoji "$status")"

  if [[ -n "$url" ]]; then
    echo "[$emoji $status]($url)"
  else
    echo "$emoji $status"
  fi
}

patch_qli_ref_file() {
  local file="$1"
  sed -E -i \
    "s|(uses: qualcomm-linux/qli-ci/.github/workflows/[^@]+)@[^[:space:]]+|\\1@${QLI_CI_REF}|g" \
    "$file"
  sed -E -i \
    "s|(^[[:space:]]*qli-ci-ref:[[:space:]]*).*$|\\1${QLI_CI_REF}|g" \
    "$file"
}

LAST_RUN_ID=""
LAST_RUN_URL=""
LAST_RUN_CONCLUSION=""

find_dispatched_run() {
  local workflow="$1"
  local branch="$2"
  local event="$3"
  local start_iso="$4"

  local run_json
  run_json="$(gh_bot run list \
    -R "$PKG_REPO" \
    --workflow "$workflow" \
    --branch "$branch" \
    --event "$event" \
    --limit 30 \
    --json databaseId,createdAt,url \
    | jq -c --arg start "$start_iso" 'map(select(.createdAt >= $start)) | sort_by(.createdAt) | last')"

  if [[ "$run_json" == "null" || -z "$run_json" ]]; then
    return 1
  fi

  LAST_RUN_ID="$(jq -r '.databaseId' <<<"$run_json")"
  LAST_RUN_URL="$(jq -r '.url' <<<"$run_json")"
  return 0
}

dispatch_workflow_and_wait() {
  local workflow="$1"
  local branch="$2"
  shift 2

  LAST_RUN_ID=""
  LAST_RUN_URL=""
  LAST_RUN_CONCLUSION="failure"

  local start_iso
  start_iso="$(iso_now)"

  if ! gh_bot workflow run "$workflow" -R "$PKG_REPO" --ref "$branch" "$@" >/dev/null; then
    return 1
  fi

  local found=0
  for _ in $(seq 1 80); do
    if find_dispatched_run "$workflow" "$branch" "workflow_dispatch" "$start_iso"; then
      found=1
      break
    fi
    sleep 3
  done

  if [[ "$found" -ne 1 || -z "$LAST_RUN_ID" ]]; then
    return 1
  fi

  gh_bot run watch "$LAST_RUN_ID" -R "$PKG_REPO" --exit-status >/dev/null 2>&1 || true

  LAST_RUN_CONCLUSION="$(gh_bot run view "$LAST_RUN_ID" -R "$PKG_REPO" --json conclusion --jq '.conclusion // "failure"' 2>/dev/null || echo "failure")"
  LAST_RUN_URL="$(gh_bot run view "$LAST_RUN_ID" -R "$PKG_REPO" --json url --jq '.url' 2>/dev/null || echo "$LAST_RUN_URL")"

  [[ "$LAST_RUN_CONCLUSION" == "success" ]]
}

LAST_PR_NUMBER=""
LAST_PR_URL=""
LAST_PR_HEAD=""
LAST_PR_HEAD_SHA=""

find_promotion_pr() {
  local base_branch="$1"
  local start_iso="$2"

  LAST_PR_NUMBER=""
  LAST_PR_URL=""
  LAST_PR_HEAD=""
  LAST_PR_HEAD_SHA=""

  local pr_json
  pr_json="$(gh_bot pr list \
    -R "$PKG_REPO" \
    --state open \
    --base "$base_branch" \
    --limit 50 \
    --json number,url,headRefName,createdAt \
    | jq -c --arg start "$start_iso" 'map(select(.createdAt >= $start and (.headRefName | startswith("debian/pr/")))) | sort_by(.createdAt) | last')"

  if [[ "$pr_json" == "null" || -z "$pr_json" ]]; then
    return 1
  fi

  LAST_PR_NUMBER="$(jq -r '.number' <<<"$pr_json")"
  LAST_PR_URL="$(jq -r '.url' <<<"$pr_json")"
  LAST_PR_HEAD="$(jq -r '.headRefName' <<<"$pr_json")"

  LAST_PR_HEAD_SHA="$(gh_bot pr view "$LAST_PR_NUMBER" -R "$PKG_REPO" --json headRefOid --jq '.headRefOid' 2>/dev/null || true)"

  if [[ -z "$LAST_PR_NUMBER" || -z "$LAST_PR_HEAD" || -z "$LAST_PR_HEAD_SHA" ]]; then
    return 1
  fi

  return 0
}

wait_for_pr_build() {
  local pr_branch="$1"
  local pr_head_sha="$2"
  local start_iso="$3"

  LAST_RUN_ID=""
  LAST_RUN_URL=""
  LAST_RUN_CONCLUSION="failure"

  local found=0
  for _ in $(seq 1 100); do
    local run_json
    run_json="$(gh_bot run list \
      -R "$PKG_REPO" \
      --workflow .github/workflows/pkg-pr-hook.yml \
      --branch "$pr_branch" \
      --event pull_request \
      --limit 40 \
      --json databaseId,headSha,createdAt,url \
      | jq -c --arg sha "$pr_head_sha" --arg start "$start_iso" 'map(select(.headSha == $sha and .createdAt >= $start)) | sort_by(.createdAt) | last')"

    if [[ "$run_json" != "null" && -n "$run_json" ]]; then
      LAST_RUN_ID="$(jq -r '.databaseId' <<<"$run_json")"
      LAST_RUN_URL="$(jq -r '.url' <<<"$run_json")"
      found=1
      break
    fi

    sleep 5
  done

  if [[ "$found" -ne 1 || -z "$LAST_RUN_ID" ]]; then
    return 1
  fi

  gh_bot run watch "$LAST_RUN_ID" -R "$PKG_REPO" --exit-status >/dev/null 2>&1 || true

  LAST_RUN_CONCLUSION="$(gh_bot run view "$LAST_RUN_ID" -R "$PKG_REPO" --json conclusion --jq '.conclusion // "failure"' 2>/dev/null || echo "failure")"
  LAST_RUN_URL="$(gh_bot run view "$LAST_RUN_ID" -R "$PKG_REPO" --json url --jq '.url' 2>/dev/null || echo "$LAST_RUN_URL")"

  [[ "$LAST_RUN_CONCLUSION" == "success" ]]
}

merge_promotion_pr() {
  local pr_number="$1"

  for _ in $(seq 1 12); do
    gh_bot pr merge "$pr_number" -R "$PKG_REPO" --merge >/dev/null 2>&1 || true
    if [[ "$(gh_bot pr view "$pr_number" -R "$PKG_REPO" --json merged --jq '.merged' 2>/dev/null || echo "false")" == "true" ]]; then
      return 0
    fi
    sleep 10
  done

  return 1
}

write_summary() {
  local overall_status="$1"
  local marker_message="$2"

  {
    echo "## pkg-example e2e loop"
    echo
    echo "- qli-ci ref under test: \`$QLI_CI_REF\`"
    if [[ -n "$QLI_CI_PR_NUMBER" ]]; then
      echo "- qli-ci PR: #$QLI_CI_PR_NUMBER"
    fi
    echo "- pkg-example temp branch: \`$TEMP_BRANCH\`"
    echo "- result: **$overall_status**"
    if [[ -n "$marker_message" ]]; then
      echo "- note: $marker_message"
    fi
    echo
    echo "| Lane | Tag | Reset | Seed Ubuntu | Promote | PR Build | Merge PR | Release |"
    echo "| --- | --- | --- | --- | --- | --- | --- | --- |"

    for lane in "${LANES[@]}"; do
      local_reset_status="${RESET_STATUS[$lane]:-skipped}"
      local_reset_url="${RESET_URL[$lane]:-}"
      local_seed_status="${SEED_STATUS[$lane]:-n/a}"
      local_seed_url="${SEED_URL[$lane]:-}"

      for tag in "${TAGS[@]}"; do
        key_base="${lane}|${tag}|"

        promote_status="${STEP_STATUS[${key_base}promote]:-skipped}"
        promote_url="${STEP_URL[${key_base}promote]:-}"

        prbuild_status="${STEP_STATUS[${key_base}prbuild]:-skipped}"
        prbuild_url="${STEP_URL[${key_base}prbuild]:-}"

        merge_status="${STEP_STATUS[${key_base}merge]:-skipped}"
        merge_url="${STEP_URL[${key_base}merge]:-}"

        release_status="${STEP_STATUS[${key_base}release]:-skipped}"
        release_url="${STEP_URL[${key_base}release]:-}"

        echo "| $lane | $tag | $(format_cell "$local_reset_status" "$local_reset_url") | $(format_cell "$local_seed_status" "$local_seed_url") | $(format_cell "$promote_status" "$promote_url") | $(format_cell "$prbuild_status" "$prbuild_url") | $(format_cell "$merge_status" "$merge_url") | $(format_cell "$release_status" "$release_url") |"
      done
    done

    echo
    echo "Generated: $(iso_now)"
  } > "$SUMMARY_FILE"
}

mark_tag_skipped() {
  local lane="$1"
  local tag="$2"
  local phase="$3"

  STEP_STATUS["${lane}|${tag}|${phase}"]="skipped"
  STEP_URL["${lane}|${tag}|${phase}"]=""
}

mark_remaining_skipped() {
  local lane="$1"
  local tag="$2"
  local start_phase="$3"

  case "$start_phase" in
    promote)
      mark_tag_skipped "$lane" "$tag" "promote"
      ;;&
    prbuild)
      mark_tag_skipped "$lane" "$tag" "prbuild"
      ;;&
    merge)
      mark_tag_skipped "$lane" "$tag" "merge"
      ;;&
    release)
      mark_tag_skipped "$lane" "$tag" "release"
      ;;
  esac
}

require_cmd gh
require_cmd jq
require_cmd git

if [[ -z "$QLI_CI_REF" ]]; then
  echo "QLI_CI_REF must be set" >&2
  exit 1
fi

REF_SHORT="$(printf '%s' "$QLI_CI_REF" | cut -c1-8)"
if [[ -n "$QLI_CI_PR_NUMBER" ]]; then
  TEMP_BRANCH="ci/qli-loop/pr-${QLI_CI_PR_NUMBER}-${REF_SHORT}"
else
  TEMP_BRANCH="ci/qli-loop/run-${RUN_ID_FALLBACK}-${REF_SHORT}"
fi

write_output "summary_file" "$SUMMARY_FILE"
write_output "temp_branch" "$TEMP_BRANCH"

if [[ "$IS_FORK_PR" == "true" ]]; then
  declare -A RESET_STATUS RESET_URL SEED_STATUS SEED_URL STEP_STATUS STEP_URL
  write_summary "skipped" "Fork PR detected; skipping because required secrets are not available to fork-triggered pull_request runs."
  write_output "overall_status" "skipped"
  exit 0
fi

if [[ -z "$BOT_TOKEN" ]]; then
  echo "BOT_TOKEN must be set for non-fork runs" >&2
  exit 1
fi

REPO_DIR="$WORKDIR/pkg-example"

declare -A RESET_STATUS RESET_URL SEED_STATUS SEED_URL STEP_STATUS STEP_URL

OVERALL_FAILURE=0
SETUP_FAILED=0
FINAL_NOTE=""

if ! git clone "https://x-access-token:${BOT_TOKEN}@github.com/${PKG_REPO}.git" "$REPO_DIR" >/dev/null 2>&1; then
  FINAL_NOTE="Failed to clone ${PKG_REPO}"
  OVERALL_FAILURE=1
  SETUP_FAILED=1
fi

if [[ "$SETUP_FAILED" -eq 0 ]]; then
  (
    cd "$REPO_DIR"

    git config user.name "GitHub Service Bot"
    git config user.email "githubservice@qti.qualcomm.com"
    git remote set-url origin "https://x-access-token:${BOT_TOKEN}@github.com/${PKG_REPO}.git"

    if ! git fetch origin "$PKG_BASE_REF" >/dev/null 2>&1; then
      exit 101
    fi

    if ! git checkout -B "$TEMP_BRANCH" "origin/$PKG_BASE_REF" >/dev/null 2>&1; then
      exit 102
    fi

    files_to_patch=(
      .github/workflows/pkg-build.yml
      .github/workflows/pkg-promote.yml
      .github/workflows/pkg-release.yml
      .github/workflows/pkg-promote-prebuilt.yml
      .github/workflows/pkg-pr-build-check.yml
      .github/workflows/pkg-pr-hook.yml
    )

    for wf in "${files_to_patch[@]}"; do
      if [[ -f "$wf" ]]; then
        patch_qli_ref_file "$wf"
      fi
    done

    if ! git diff --quiet; then
      git add .github/workflows
      git commit -s -m "ci: pin pkg-example workflows to qli-ci ref ${QLI_CI_REF}" >/dev/null 2>&1 || true
    fi

    if ! git push origin "HEAD:refs/heads/${TEMP_BRANCH}" --force >/dev/null 2>&1; then
      exit 103
    fi
  )

  patch_rc=$?
  if [[ "$patch_rc" -ne 0 ]]; then
    OVERALL_FAILURE=1
    SETUP_FAILED=1
    case "$patch_rc" in
      101) FINAL_NOTE="Failed to fetch ${PKG_BASE_REF} from ${PKG_REPO}" ;;
      102) FINAL_NOTE="Failed to check out temp branch ${TEMP_BRANCH}" ;;
      103) FINAL_NOTE="Failed to push temp branch ${TEMP_BRANCH}" ;;
      *) FINAL_NOTE="Failed preparing temp workflow branch" ;;
    esac
  fi
fi

for lane in "${LANES[@]}"; do
  lane_branch="${LANE_BRANCH[$lane]}"
  RESET_STATUS[$lane]="skipped"
  RESET_URL[$lane]=""

  if [[ "$lane" == "ubuntu" ]]; then
    SEED_STATUS[$lane]="skipped"
    SEED_URL[$lane]=""
  else
    SEED_STATUS[$lane]="n/a"
    SEED_URL[$lane]=""
  fi

  for tag in "${TAGS[@]}"; do
    mark_remaining_skipped "$lane" "$tag" "promote"
  done

  if [[ "$SETUP_FAILED" -ne 0 ]]; then
    continue
  fi

  if dispatch_workflow_and_wait .github/workflows/reset-repo.yml "$TEMP_BRANCH" -f confirmation=true; then
    RESET_STATUS[$lane]="success"
  else
    RESET_STATUS[$lane]="failure"
    OVERALL_FAILURE=1
  fi
  RESET_URL[$lane]="$LAST_RUN_URL"

  if [[ "${RESET_STATUS[$lane]}" != "success" ]]; then
    continue
  fi

  if [[ "$lane" == "ubuntu" ]]; then
    if (
      cd "$REPO_DIR"
      git fetch origin qcom/debian/latest >/dev/null 2>&1
      git checkout -B qcom/ubuntu/resolute origin/qcom/debian/latest >/dev/null 2>&1
      git push origin HEAD:refs/heads/qcom/ubuntu/resolute --force >/dev/null 2>&1
      git checkout "$TEMP_BRANCH" >/dev/null 2>&1
    ); then
      SEED_STATUS[$lane]="success"
      SEED_URL[$lane]="https://github.com/${PKG_REPO}/tree/qcom/ubuntu/resolute"
    else
      SEED_STATUS[$lane]="failure"
      SEED_URL[$lane]="https://github.com/${PKG_REPO}/branches"
      OVERALL_FAILURE=1
      continue
    fi
  fi

  for tag in "${TAGS[@]}"; do
    key_base="${lane}|${tag}|"

    STEP_STATUS["${key_base}promote"]="skipped"
    STEP_STATUS["${key_base}prbuild"]="skipped"
    STEP_STATUS["${key_base}merge"]="skipped"
    STEP_STATUS["${key_base}release"]="skipped"

    STEP_URL["${key_base}promote"]=""
    STEP_URL["${key_base}prbuild"]=""
    STEP_URL["${key_base}merge"]=""
    STEP_URL["${key_base}release"]=""

    promote_start="$(iso_now)"
    if dispatch_workflow_and_wait .github/workflows/pkg-promote.yml "$TEMP_BRANCH" -f debian-branch="$lane_branch" -f upstream-tag="$tag"; then
      STEP_STATUS["${key_base}promote"]="success"
    else
      STEP_STATUS["${key_base}promote"]="failure"
      OVERALL_FAILURE=1
      STEP_URL["${key_base}promote"]="$LAST_RUN_URL"
      continue
    fi
    STEP_URL["${key_base}promote"]="$LAST_RUN_URL"

    if ! find_promotion_pr "$lane_branch" "$promote_start"; then
      STEP_STATUS["${key_base}prbuild"]="failure"
      OVERALL_FAILURE=1
      continue
    fi

    patch_start="$(iso_now)"
    if ! (
      cd "$REPO_DIR"
      git fetch origin "$LAST_PR_HEAD" >/dev/null 2>&1
      git checkout -B "$LAST_PR_HEAD" "origin/$LAST_PR_HEAD" >/dev/null 2>&1
      patch_qli_ref_file .github/workflows/pkg-pr-hook.yml
      if ! git diff --quiet -- .github/workflows/pkg-pr-hook.yml; then
        git add .github/workflows/pkg-pr-hook.yml
        git commit -s -m "ci: pin pr hook to qli-ci ref ${QLI_CI_REF}" >/dev/null 2>&1 || true
        git push origin "HEAD:refs/heads/${LAST_PR_HEAD}" >/dev/null 2>&1
      fi
      git checkout "$TEMP_BRANCH" >/dev/null 2>&1
    ); then
      STEP_STATUS["${key_base}prbuild"]="failure"
      STEP_URL["${key_base}prbuild"]="$LAST_PR_URL"
      OVERALL_FAILURE=1
      continue
    fi

    LAST_PR_HEAD_SHA="$(gh_bot pr view "$LAST_PR_NUMBER" -R "$PKG_REPO" --json headRefOid --jq '.headRefOid' 2>/dev/null || true)"
    if [[ -z "$LAST_PR_HEAD_SHA" ]]; then
      STEP_STATUS["${key_base}prbuild"]="failure"
      STEP_URL["${key_base}prbuild"]="$LAST_PR_URL"
      OVERALL_FAILURE=1
      continue
    fi

    if wait_for_pr_build "$LAST_PR_HEAD" "$LAST_PR_HEAD_SHA" "$patch_start"; then
      STEP_STATUS["${key_base}prbuild"]="success"
      STEP_URL["${key_base}prbuild"]="$LAST_RUN_URL"
    else
      STEP_STATUS["${key_base}prbuild"]="failure"
      STEP_URL["${key_base}prbuild"]="${LAST_RUN_URL:-$LAST_PR_URL}"
      OVERALL_FAILURE=1
      continue
    fi

    if merge_promotion_pr "$LAST_PR_NUMBER"; then
      STEP_STATUS["${key_base}merge"]="success"
      STEP_URL["${key_base}merge"]="$LAST_PR_URL"
    else
      STEP_STATUS["${key_base}merge"]="failure"
      STEP_URL["${key_base}merge"]="$LAST_PR_URL"
      OVERALL_FAILURE=1
      continue
    fi

    if dispatch_workflow_and_wait .github/workflows/pkg-release.yml "$TEMP_BRANCH" -f debian-branch="$lane_branch"; then
      STEP_STATUS["${key_base}release"]="success"
      STEP_URL["${key_base}release"]="$LAST_RUN_URL"
    else
      STEP_STATUS["${key_base}release"]="failure"
      STEP_URL["${key_base}release"]="$LAST_RUN_URL"
      OVERALL_FAILURE=1
      continue
    fi
  done
done

if [[ -n "$BOT_TOKEN" && -n "${TEMP_BRANCH:-}" ]]; then
  (
    cd "$REPO_DIR"
    git push origin --delete "$TEMP_BRANCH" >/dev/null 2>&1 || true
  ) || true
fi

if [[ "$OVERALL_FAILURE" -eq 0 ]]; then
  write_summary "success" "$FINAL_NOTE"
  write_output "overall_status" "success"
  exit 0
fi

write_summary "failure" "$FINAL_NOTE"
write_output "overall_status" "failure"
exit 1
