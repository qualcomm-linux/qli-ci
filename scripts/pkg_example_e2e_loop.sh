#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# SPDX-License-Identifier: BSD-3-Clause-Clear

set -uo pipefail

STATE_FILE="${STATE_FILE:-/tmp/pkg-example-e2e-state.json}"
SUMMARY_FILE="${SUMMARY_FILE:-/tmp/pkg-example-e2e-summary.md}"
PKG_REPO="${PKG_EXAMPLE_REPO:-qualcomm-linux/pkg-example}"
PKG_BASE_REF="${PKG_EXAMPLE_BASE_REF:-qli-ci}"
RUN_ID_FALLBACK="${GITHUB_RUN_ID:-manual}"
QLI_CI_REF="${QLI_CI_REF:-}"
QLI_CI_PR_NUMBER="${QLI_CI_PR_NUMBER:-}"
IS_FORK_PR="${IS_FORK_PR:-false}"
BOT_TOKEN="${BOT_TOKEN:-}"
ENABLE_DEBIAN_PATH_RAW="${ENABLE_DEBIAN_PATH:-1}"
ENABLE_UBUNTU_PATH_RAW="${ENABLE_UBUNTU_PATH:-1}"
PROMOTE_MODE="${PROMOTE_MODE:-source}"

TAGS=("v1.0.0" "v1.1.0")
PREBUILT_TAGS=("v1.0.0")
PREBUILT_DISTRO="resolute"
PREBUILT_FIXTURE_ROOT=".e2e-prebuilt-fixtures"

CANCEL_SIGNALLED=0

handle_cancel_signal() {
  CANCEL_SIGNALLED=1
  echo "Cancellation signal received; aborting pkg-example e2e step." >&2
  exit 130
}

abort_if_cancelled() {
  if [[ "$CANCEL_SIGNALLED" -eq 1 ]]; then
    echo "Cancellation requested; stopping current operation." >&2
    exit 130
  fi
}

trap 'handle_cancel_signal' INT TERM

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

normalize_bool() {
  local raw="${1:-}"
  local value
  value="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    0|false|no|off) echo "false" ;;
    1|true|yes|on|"") echo "true" ;;
    *) echo "true" ;;
  esac
}

gh_bot() {
  GH_TOKEN="$BOT_TOKEN" gh "$@"
}

state_exists() {
  [[ -f "$STATE_FILE" ]]
}

ensure_state() {
  if ! state_exists; then
    echo "Missing state file: $STATE_FILE" >&2
    exit 1
  fi
}

state_get() {
  local query="$1"
  jq -r "$query" "$STATE_FILE"
}

state_set_meta_bool() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp)"
  jq --arg key "$key" --argjson value "$value" '.meta[$key] = $value' "$STATE_FILE" > "$tmp"
  mv "$tmp" "$STATE_FILE"
}

state_set_meta_str() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp)"
  jq --arg key "$key" --arg value "$value" '.meta[$key] = $value' "$STATE_FILE" > "$tmp"
  mv "$tmp" "$STATE_FILE"
}

mark_overall_failure() {
  local note="$1"
  local tmp
  tmp="$(mktemp)"
  jq --arg note "$note" '.meta.overall_failure = true | if ($note | length) > 0 then .meta.note = $note else . end' "$STATE_FILE" > "$tmp"
  mv "$tmp" "$STATE_FILE"
}

set_lane_phase() {
  local lane="$1"
  local phase="$2"
  local status="$3"
  local url="$4"
  local tmp
  tmp="$(mktemp)"
  jq \
    --arg lane "$lane" \
    --arg phase "$phase" \
    --arg status "$status" \
    --arg url "$url" \
    '.lanes[$lane][$phase].status = $status | .lanes[$lane][$phase].url = $url' \
    "$STATE_FILE" > "$tmp"
  mv "$tmp" "$STATE_FILE"
}

set_tag_phase() {
  local lane="$1"
  local tag="$2"
  local phase="$3"
  local status="$4"
  local url="$5"
  local tmp
  tmp="$(mktemp)"
  jq \
    --arg lane "$lane" \
    --arg tag "$tag" \
    --arg phase "$phase" \
    --arg status "$status" \
    --arg url "$url" \
    '.lanes[$lane].tags[$tag][$phase].status = $status | .lanes[$lane].tags[$tag][$phase].url = $url' \
    "$STATE_FILE" > "$tmp"
  mv "$tmp" "$STATE_FILE"
}

set_tag_pr_meta() {
  local lane="$1"
  local tag="$2"
  local number="$3"
  local url="$4"
  local head="$5"
  local head_sha="$6"
  local tmp
  tmp="$(mktemp)"
  jq \
    --arg lane "$lane" \
    --arg tag "$tag" \
    --arg number "$number" \
    --arg url "$url" \
    --arg head "$head" \
    --arg head_sha "$head_sha" \
    '.lanes[$lane].tags[$tag].pr.number = $number
     | .lanes[$lane].tags[$tag].pr.url = $url
     | .lanes[$lane].tags[$tag].pr.head = $head
     | .lanes[$lane].tags[$tag].pr.head_sha = $head_sha' \
    "$STATE_FILE" > "$tmp"
  mv "$tmp" "$STATE_FILE"
}

get_lane_phase_status() {
  local lane="$1"
  local phase="$2"
  state_get ".lanes[\"$lane\"][\"$phase\"].status"
}

get_tag_phase_status() {
  local lane="$1"
  local tag="$2"
  local phase="$3"
  state_get ".lanes[\"$lane\"].tags[\"$tag\"][\"$phase\"].status"
}

lane_is_enabled() {
  local lane="$1"
  case "$lane" in
    debian) state_get '.meta.enable_debian' ;;
    ubuntu) state_get '.meta.enable_ubuntu' ;;
    *)
      echo "false"
      ;;
  esac
}

lane_branch() {
  case "$1" in
    debian) echo "qcom/debian/latest" ;;
    ubuntu) echo "qcom/ubuntu/resolute" ;;
    *)
      echo "Unsupported lane: $1" >&2
      return 1
      ;;
  esac
}

prebuilt_package_name_for_tag() {
  local tag="$1"
  local normalized="${tag#v}"
  echo "libqcom-example_${normalized}_arm64.tar.gz"
}

prebuilt_debian_version_for_tag() {
  local tag="$1"
  local normalized="${tag#v}"
  echo "${normalized}-1"
}

resolve_prebuilt_fixture_compiler() {
  local host_arch
  host_arch="$(uname -m)"

  if command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
    echo "aarch64-linux-gnu-gcc"
    return 0
  fi

  if [[ "$host_arch" == "aarch64" || "$host_arch" == "arm64" ]]; then
    if command -v gcc >/dev/null 2>&1; then
      echo "gcc"
      return 0
    fi
  fi

  return 1
}

create_prebuilt_fixture_archive() {
  local repo_dir="$1"
  local tag="$2"
  local package_name archive_dir staging_dir compiler lib_path lib_info

  compiler="${PREBUILT_FIXTURE_CC:-}"
  if [[ -z "$compiler" ]]; then
    if ! compiler="$(resolve_prebuilt_fixture_compiler)"; then
      echo "No arm64-capable compiler found for prebuilt fixture generation" >&2
      return 1
    fi
  fi

  if ! command -v "$compiler" >/dev/null 2>&1; then
    echo "Configured prebuilt fixture compiler not found: $compiler" >&2
    return 1
  fi

  package_name="$(prebuilt_package_name_for_tag "$tag")"
  archive_dir="${repo_dir}/${PREBUILT_FIXTURE_ROOT}/${tag}/${PREBUILT_DISTRO}"
  mkdir -p "$archive_dir"

  staging_dir="$(mktemp -d)"
  mkdir -p "${staging_dir}/usr/lib" "${staging_dir}/usr/include/qcom"

  cat > "${staging_dir}/qcom_example.c" <<'EOF'
int qcom_example(void) { return 1; }
int qcom_example2(void) { return 2; }
EOF

  cat > "${staging_dir}/usr/include/qcom/qcom_example.h" <<'EOF'
#ifndef QCOM_EXAMPLE_H
#define QCOM_EXAMPLE_H

int qcom_example(void);
int qcom_example2(void);

#endif
EOF

  lib_path="${staging_dir}/usr/lib/libqcom-example.so.1.0.0"

  "$compiler" -shared -fPIC -Wl,-soname,libqcom-example.so.1 \
    -o "$lib_path" \
    "${staging_dir}/qcom_example.c"

  if command -v file >/dev/null 2>&1; then
    lib_info="$(file -b "$lib_path")"
    if [[ "$lib_info" != *"ARM aarch64"* ]]; then
      echo "Generated prebuilt fixture is not arm64: ${lib_info}" >&2
      return 1
    fi
  fi

  ln -sf "libqcom-example.so.1.0.0" "${staging_dir}/usr/lib/libqcom-example.so.1"
  ln -sf "libqcom-example.so.1" "${staging_dir}/usr/lib/libqcom-example.so"

  tar -C "$staging_dir" -czf "${archive_dir}/${package_name}" usr
  rm -rf "$staging_dir"
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

wait_for_run_conclusion() {
  local run_id="$1"
  local max_attempts="${2:-360}"
  local sleep_seconds="${3:-5}"
  local auto_approve_pending="${4:-false}"

  local run_json status conclusion url
  for _ in $(seq 1 "$max_attempts"); do
    abort_if_cancelled
    run_json="$(gh_bot run view "$run_id" -R "$PKG_REPO" --json status,conclusion,url 2>/dev/null || true)"
    if [[ -n "$run_json" ]]; then
      status="$(jq -r '.status // empty' <<<"$run_json")"
      conclusion="$(jq -r '.conclusion // empty' <<<"$run_json")"
      url="$(jq -r '.url // empty' <<<"$run_json")"

      if [[ -n "$url" ]]; then
        LAST_RUN_URL="$url"
      fi

      if [[ "$status" == "completed" ]]; then
        if [[ -n "$conclusion" && "$conclusion" != "null" ]]; then
          LAST_RUN_CONCLUSION="$conclusion"
        else
          LAST_RUN_CONCLUSION="failure"
        fi

        if [[ "$LAST_RUN_CONCLUSION" == "success" ]]; then
          return 0
        fi
        return 1
      fi

      if [[ "$auto_approve_pending" == "true" ]]; then
        approve_pending_deployments "$run_id" || true
      fi
    fi

    abort_if_cancelled
    sleep "$sleep_seconds"
  done

  LAST_RUN_CONCLUSION="failure"
  return 1
}

dispatch_workflow_and_wait() {
  local workflow="$1"
  local branch="$2"
  local auto_approve_pending="false"
  shift 2

  if [[ "${1:-}" == "--auto-approve-pending-deployments" ]]; then
    auto_approve_pending="true"
    shift
  fi

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
    abort_if_cancelled
    if find_dispatched_run "$workflow" "$branch" "workflow_dispatch" "$start_iso"; then
      found=1
      break
    fi
    abort_if_cancelled
    sleep 3
  done

  if [[ "$found" -ne 1 || -z "$LAST_RUN_ID" ]]; then
    return 1
  fi

  wait_for_run_conclusion "$LAST_RUN_ID" 360 5 "$auto_approve_pending"
}

approve_pending_deployments() {
  local run_id="$1"
  local env_ids_json payload

  local pending_json
  pending_json="$(gh_bot api \
    -H "Accept: application/vnd.github+json" \
    "/repos/${PKG_REPO}/actions/runs/${run_id}/pending_deployments" 2>/dev/null || true)"

  if [[ -z "$pending_json" || "$pending_json" == "[]" ]]; then
    return 0
  fi

  env_ids_json="$(jq -c '[.[] | select(.current_user_can_approve == true) | .environment.id] | unique' <<<"$pending_json")"
  if [[ "$env_ids_json" == "[]" || -z "$env_ids_json" ]]; then
    return 0
  fi

  payload="$(jq -n \
    --argjson environment_ids "$env_ids_json" \
    --arg state "approved" \
    --arg comment "Auto-approved by qli-ci pkg-example e2e loop" \
    '{state: $state, comment: $comment, environment_ids: $environment_ids}')"

  if ! gh_bot api \
    --method POST \
    -H "Accept: application/vnd.github+json" \
    "/repos/${PKG_REPO}/actions/runs/${run_id}/pending_deployments" \
    --input - >/dev/null 2>&1 <<<"$payload"; then
    echo "Warning: auto-approve call failed for pending deployments on run ${run_id}" >&2
    return 1
  fi

  return 0
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

  LAST_RUN_ID=""
  LAST_RUN_URL=""
  LAST_RUN_CONCLUSION="failure"

  local found=0
  for _ in $(seq 1 100); do
    abort_if_cancelled
    local run_json
    run_json="$(gh_bot run list \
      -R "$PKG_REPO" \
      --workflow .github/workflows/pkg-pr-hook.yml \
      --branch "$pr_branch" \
      --event pull_request \
      --limit 40 \
      --json databaseId,headSha,url,createdAt \
      | jq -c --arg sha "$pr_head_sha" 'map(select(.headSha == $sha)) | sort_by(.createdAt) | last')"

    if [[ "$run_json" != "null" && -n "$run_json" ]]; then
      LAST_RUN_ID="$(jq -r '.databaseId' <<<"$run_json")"
      LAST_RUN_URL="$(jq -r '.url' <<<"$run_json")"
      found=1
      break
    fi

    abort_if_cancelled
    sleep 5
  done

  if [[ "$found" -ne 1 || -z "$LAST_RUN_ID" ]]; then
    return 1
  fi

  wait_for_run_conclusion "$LAST_RUN_ID" 360 5
}

merge_promotion_pr() {
  local pr_number="$1"
  local merge_output

  for _ in $(seq 1 12); do
    abort_if_cancelled
    # If merge command itself succeeds, treat that as final success.
    if merge_output="$(gh_bot pr merge "$pr_number" -R "$PKG_REPO" --merge 2>&1)"; then
      return 0
    fi

    # Idempotent retries: another attempt might have already merged the PR.
    if [[ "$merge_output" == *"already merged"* ]]; then
      return 0
    fi

    # Fallback probe when API metadata is available.
    if [[ "$(gh_bot pr view "$pr_number" -R "$PKG_REPO" --json merged --jq '.merged' 2>/dev/null || echo "false")" == "true" ]]; then
      return 0
    fi
    abort_if_cancelled
    sleep 10
  done

  if [[ -n "${merge_output:-}" ]]; then
    echo "$merge_output" >&2
  fi
  return 1
}

cmd_init() {
  require_cmd jq
  require_cmd gh
  require_cmd git

  if [[ -z "$QLI_CI_REF" ]]; then
    echo "QLI_CI_REF must be set" >&2
    return 1
  fi

  local ref_short temp_branch
  local enable_debian enable_ubuntu
  ref_short="$(printf '%s' "$QLI_CI_REF" | cut -c1-8)"
  enable_debian="$(normalize_bool "$ENABLE_DEBIAN_PATH_RAW")"
  enable_ubuntu="$(normalize_bool "$ENABLE_UBUNTU_PATH_RAW")"

  if [[ -n "$QLI_CI_PR_NUMBER" ]]; then
    temp_branch="ci/qli-loop/pr-${QLI_CI_PR_NUMBER}-${ref_short}"
  else
    temp_branch="ci/qli-loop/run-${RUN_ID_FALLBACK}-${ref_short}"
  fi

  jq -n \
    --arg qli_ref "$QLI_CI_REF" \
    --arg qli_pr "$QLI_CI_PR_NUMBER" \
    --arg temp_branch "$temp_branch" \
    --arg promote_mode "$PROMOTE_MODE" \
    --arg summary "$SUMMARY_FILE" \
    --arg skip "$IS_FORK_PR" \
    --argjson enable_debian "$enable_debian" \
    --argjson enable_ubuntu "$enable_ubuntu" \
    '{
      meta: {
        qli_ci_ref: $qli_ref,
        qli_ci_pr_number: $qli_pr,
        temp_branch: $temp_branch,
        promote_mode: $promote_mode,
        summary_file: $summary,
        skip: ($skip == "true"),
        enable_debian: $enable_debian,
        enable_ubuntu: $enable_ubuntu,
        prepared: false,
        overall_failure: false,
        note: "",
        repo_dir: ""
      },
      lanes: {
        debian: {
          reset: {status: "skipped", url: ""},
          seed: {status: "n/a", url: ""},
          tags: {
            "v1.0.0": {
              promote: {status: "skipped", url: ""},
              sync: {status: "skipped", url: ""},
              prbuild: {status: "skipped", url: ""},
              merge: {status: "skipped", url: ""},
              release: {status: "skipped", url: ""},
              pr: {number: "", url: "", head: "", head_sha: ""}
            },
            "v1.1.0": {
              promote: {status: "skipped", url: ""},
              sync: {status: "skipped", url: ""},
              prbuild: {status: "skipped", url: ""},
              merge: {status: "skipped", url: ""},
              release: {status: "skipped", url: ""},
              pr: {number: "", url: "", head: "", head_sha: ""}
            }
          }
        },
        ubuntu: {
          reset: {status: "skipped", url: ""},
          seed: {status: "skipped", url: ""},
          tags: {
            "v1.0.0": {
              promote: {status: "skipped", url: ""},
              sync: {status: "skipped", url: ""},
              prbuild: {status: "skipped", url: ""},
              merge: {status: "skipped", url: ""},
              release: {status: "skipped", url: ""},
              pr: {number: "", url: "", head: "", head_sha: ""}
            },
            "v1.1.0": {
              promote: {status: "skipped", url: ""},
              sync: {status: "skipped", url: ""},
              prbuild: {status: "skipped", url: ""},
              merge: {status: "skipped", url: ""},
              release: {status: "skipped", url: ""},
              pr: {number: "", url: "", head: "", head_sha: ""}
            }
          }
        }
      }
    }' > "$STATE_FILE"

  if [[ "$IS_FORK_PR" == "true" ]]; then
    state_set_meta_str "note" "Fork PR detected; skipping because required secrets are not available to fork-triggered pull_request runs."
  elif [[ "$enable_debian" != "true" || "$enable_ubuntu" != "true" ]]; then
    state_set_meta_str "note" "Path toggles: ENABLE_DEBIAN_PATH=${enable_debian}, ENABLE_UBUNTU_PATH=${enable_ubuntu}"
  fi

  write_output "temp_branch" "$temp_branch"
  write_output "summary_file" "$SUMMARY_FILE"
  return 0
}

cmd_prepare_temp_branch() {
  ensure_state

  if [[ "$(state_get '.meta.skip')" == "true" ]]; then
    return 0
  fi

  if [[ "$(state_get '.meta.enable_debian')" != "true" && "$(state_get '.meta.enable_ubuntu')" != "true" ]]; then
    return 0
  fi

  if [[ -z "$BOT_TOKEN" ]]; then
    mark_overall_failure "BOT_TOKEN is required to prepare temp branch"
    return 1
  fi

  local temp_branch repo_dir
  temp_branch="$(state_get '.meta.temp_branch')"
  repo_dir="/tmp/pkg-example-e2e-${RUN_ID_FALLBACK}-${RANDOM}"

  rm -rf "$repo_dir"

  if ! git clone "https://x-access-token:${BOT_TOKEN}@github.com/${PKG_REPO}.git" "$repo_dir" >/dev/null 2>&1; then
    mark_overall_failure "Failed to clone ${PKG_REPO}"
    return 1
  fi

  (
    cd "$repo_dir"

    git config user.name "GitHub Service Bot"
    git config user.email "githubservice@qti.qualcomm.com"
    git remote set-url origin "https://x-access-token:${BOT_TOKEN}@github.com/${PKG_REPO}.git"

    git fetch origin "$PKG_BASE_REF" >/dev/null 2>&1
    git checkout -B "$temp_branch" "origin/$PKG_BASE_REF" >/dev/null 2>&1

    local files_to_patch=(
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

    git push origin "HEAD:refs/heads/${temp_branch}" --force >/dev/null 2>&1
  )
  rc=$?

  if [[ "$rc" -ne 0 ]]; then
    mark_overall_failure "Failed preparing temp branch ${temp_branch}"
    return 1
  fi

  state_set_meta_str "repo_dir" "$repo_dir"
  state_set_meta_bool "prepared" "true"
  return 0
}

cmd_reset_lane() {
  ensure_state
  local lane="$1"

  if [[ "$(state_get '.meta.skip')" == "true" ]]; then
    set_lane_phase "$lane" "reset" "skipped" ""
    return 0
  fi

  if [[ "$(lane_is_enabled "$lane")" != "true" ]]; then
    set_lane_phase "$lane" "reset" "skipped" ""
    return 0
  fi

  if [[ "$(state_get '.meta.prepared')" != "true" ]]; then
    set_lane_phase "$lane" "reset" "skipped" ""
    return 0
  fi

  local temp_branch
  temp_branch="$(state_get '.meta.temp_branch')"

  if dispatch_workflow_and_wait .github/workflows/reset-repo.yml "$temp_branch" -f confirmation=true; then
    set_lane_phase "$lane" "reset" "success" "$LAST_RUN_URL"
    return 0
  fi

  set_lane_phase "$lane" "reset" "failure" "$LAST_RUN_URL"
  mark_overall_failure "Reset failed for lane ${lane}"
  return 1
}

cmd_seed_ubuntu() {
  ensure_state

  if [[ "$(state_get '.meta.skip')" == "true" ]]; then
    set_lane_phase "ubuntu" "seed" "skipped" ""
    return 0
  fi

  if [[ "$(lane_is_enabled "ubuntu")" != "true" ]]; then
    set_lane_phase "ubuntu" "seed" "skipped" ""
    return 0
  fi

  if [[ "$(get_lane_phase_status "ubuntu" "reset")" != "success" ]]; then
    set_lane_phase "ubuntu" "seed" "skipped" ""
    return 0
  fi

  local repo_dir
  repo_dir="$(state_get '.meta.repo_dir')"

  if [[ -z "$repo_dir" || ! -d "$repo_dir" ]]; then
    set_lane_phase "ubuntu" "seed" "failure" ""
    mark_overall_failure "Missing local repo clone during ubuntu seed"
    return 1
  fi

  (
    cd "$repo_dir"
    git fetch origin qcom/debian/latest >/dev/null 2>&1
    git checkout -B qcom/ubuntu/resolute origin/qcom/debian/latest >/dev/null 2>&1
    git push origin HEAD:refs/heads/qcom/ubuntu/resolute --force >/dev/null 2>&1
    git checkout "$(state_get '.meta.temp_branch')" >/dev/null 2>&1
  )
  rc=$?

  if [[ "$rc" -eq 0 ]]; then
    set_lane_phase "ubuntu" "seed" "success" "https://github.com/${PKG_REPO}/tree/qcom/ubuntu/resolute"
    return 0
  fi

  set_lane_phase "ubuntu" "seed" "failure" "https://github.com/${PKG_REPO}/branches"
  mark_overall_failure "Failed creating qcom/ubuntu/resolute"
  return 1
}

cmd_seed_prebuilt_fixtures() {
  ensure_state
  local lane="${1:-ubuntu}"

  if [[ "$(state_get '.meta.skip')" == "true" ]]; then
    return 0
  fi

  if [[ "$(lane_is_enabled "$lane")" != "true" ]]; then
    return 0
  fi

  if [[ "$(get_lane_phase_status "$lane" "reset")" != "success" ]]; then
    return 0
  fi

  if [[ "$lane" == "ubuntu" && "$(get_lane_phase_status "ubuntu" "seed")" != "success" ]]; then
    return 0
  fi

  if [[ -z "$BOT_TOKEN" ]]; then
    mark_overall_failure "BOT_TOKEN is required to seed prebuilt fixtures"
    return 1
  fi

  require_cmd tar

  local prebuilt_fixture_cc
  if ! prebuilt_fixture_cc="$(resolve_prebuilt_fixture_compiler)"; then
    mark_overall_failure "Missing arm64 compiler for prebuilt fixture generation (need aarch64-linux-gnu-gcc, or run on arm64 with gcc)"
    return 1
  fi

  local lane_branch_value repo_dir temp_branch initial_tag initial_package
  lane_branch_value="$(lane_branch "$lane")"
  repo_dir="$(state_get '.meta.repo_dir')"
  temp_branch="$(state_get '.meta.temp_branch')"
  initial_tag="bootstrap"
  initial_package="$(prebuilt_package_name_for_tag "${PREBUILT_TAGS[0]}")"

  if [[ -z "$repo_dir" || ! -d "$repo_dir" ]]; then
    mark_overall_failure "Missing local repo clone during prebuilt fixture seed"
    return 1
  fi

  (
    cd "$repo_dir"
    git remote set-url origin "https://x-access-token:${BOT_TOKEN}@github.com/${PKG_REPO}.git"
    git fetch origin "$lane_branch_value" >/dev/null 2>&1
    git checkout -B "$lane_branch_value" "origin/$lane_branch_value" >/dev/null 2>&1

    rm -rf "$PREBUILT_FIXTURE_ROOT"
    for tag in "${PREBUILT_TAGS[@]}"; do
      PREBUILT_FIXTURE_CC="$prebuilt_fixture_cc" create_prebuilt_fixture_archive "$repo_dir" "$tag"
    done

    cat > upstream.conf <<EOF
export ARTIFACTORY="file://\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)/${PREBUILT_FIXTURE_ROOT}"
export TAG="${initial_tag}"
export DISTRO="${PREBUILT_DISTRO}"
export PACKAGE_NAME="${initial_package}"
EOF

    git add upstream.conf "$PREBUILT_FIXTURE_ROOT"
    if ! git diff --cached --quiet; then
      git commit -s -m "ci: seed local prebuilt fixtures for e2e loop" >/dev/null 2>&1 || true
      git push origin "HEAD:refs/heads/${lane_branch_value}" >/dev/null 2>&1
    fi

    git checkout "$temp_branch" >/dev/null 2>&1
  )
  rc=$?

  if [[ "$rc" -ne 0 ]]; then
    mark_overall_failure "Failed prebuilt fixture seed for lane ${lane}"
    return 1
  fi

  return 0
}

cmd_promote_tag() {
  ensure_state
  local lane="$1"
  local tag="$2"
  local mode="${3:-$PROMOTE_MODE}"

  if [[ "$(state_get '.meta.skip')" == "true" ]]; then
    set_tag_phase "$lane" "$tag" "promote" "skipped" ""
    return 0
  fi

  if [[ "$(lane_is_enabled "$lane")" != "true" ]]; then
    set_tag_phase "$lane" "$tag" "promote" "skipped" ""
    return 0
  fi

  if [[ "$(get_lane_phase_status "$lane" "reset")" != "success" ]]; then
    set_tag_phase "$lane" "$tag" "promote" "skipped" ""
    return 0
  fi

  if [[ "$lane" == "ubuntu" && "$(get_lane_phase_status "ubuntu" "seed")" != "success" ]]; then
    set_tag_phase "$lane" "$tag" "promote" "skipped" ""
    return 0
  fi

  local lane_branch promote_start
  lane_branch="$(lane_branch "$lane")"
  promote_start="$(iso_now)"

  if [[ "$mode" == "prebuilt" ]]; then
    local new_package_name new_debian_version
    new_package_name="$(prebuilt_package_name_for_tag "$tag")"
    new_debian_version="$(prebuilt_debian_version_for_tag "$tag")"

    if ! dispatch_workflow_and_wait .github/workflows/pkg-promote-prebuilt.yml "$(state_get '.meta.temp_branch')" -f debian-branch="$lane_branch" -f new-tag="$tag" -f new-package-name="$new_package_name" -f new-debian-version="$new_debian_version"; then
      set_tag_phase "$lane" "$tag" "promote" "failure" "$LAST_RUN_URL"
      mark_overall_failure "Promote (${mode}) failed for ${lane} ${tag}"
      return 1
    fi
  elif [[ "$mode" == "source" ]]; then
    if ! dispatch_workflow_and_wait .github/workflows/pkg-promote.yml "$(state_get '.meta.temp_branch')" -f debian-branch="$lane_branch" -f upstream-tag="$tag"; then
      set_tag_phase "$lane" "$tag" "promote" "failure" "$LAST_RUN_URL"
      mark_overall_failure "Promote (${mode}) failed for ${lane} ${tag}"
      return 1
    fi
  else
    set_tag_phase "$lane" "$tag" "promote" "failure" ""
    mark_overall_failure "Unsupported promote mode '${mode}'"
    return 1
  fi

  set_tag_phase "$lane" "$tag" "promote" "success" "$LAST_RUN_URL"

  if ! find_promotion_pr "$lane_branch" "$promote_start"; then
    set_tag_phase "$lane" "$tag" "promote" "failure" "$LAST_RUN_URL"
    mark_overall_failure "Promotion PR not found for ${lane} ${tag}"
    return 1
  fi

  set_tag_pr_meta "$lane" "$tag" "$LAST_PR_NUMBER" "$LAST_PR_URL" "$LAST_PR_HEAD" "$LAST_PR_HEAD_SHA"
  return 0
}

cmd_sync_pr_hook() {
  ensure_state
  local lane="$1"
  local tag="$2"

  if [[ "$(state_get '.meta.skip')" == "true" ]]; then
    set_tag_phase "$lane" "$tag" "sync" "skipped" ""
    return 0
  fi

  if [[ "$(lane_is_enabled "$lane")" != "true" ]]; then
    set_tag_phase "$lane" "$tag" "sync" "skipped" ""
    return 0
  fi

  if [[ "$(get_tag_phase_status "$lane" "$tag" "promote")" != "success" ]]; then
    set_tag_phase "$lane" "$tag" "sync" "skipped" ""
    return 0
  fi

  local pr_number pr_head repo_dir refreshed_sha_file
  pr_number="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].pr.number")"
  pr_head="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].pr.head")"
  repo_dir="$(state_get '.meta.repo_dir')"

  if [[ -z "$pr_number" || -z "$pr_head" || -z "$repo_dir" || ! -d "$repo_dir" ]]; then
    set_tag_phase "$lane" "$tag" "sync" "failure" ""
    mark_overall_failure "Missing PR metadata for sync ${lane} ${tag}"
    return 1
  fi
  refreshed_sha_file="${repo_dir}/.pkg-example-e2e-pr-head-sha"
  rm -f "$refreshed_sha_file"

  (
    cd "$repo_dir"
    git fetch origin "$pr_head" >/dev/null 2>&1
    git checkout -B "$pr_head" "origin/$pr_head" >/dev/null 2>&1
    patch_qli_ref_file .github/workflows/pkg-pr-hook.yml

    if ! git diff --quiet -- .github/workflows/pkg-pr-hook.yml; then
      git add .github/workflows/pkg-pr-hook.yml
      git commit -s -m "ci: pin pr hook to qli-ci ref ${QLI_CI_REF}" >/dev/null 2>&1 || true
      git push origin "HEAD:refs/heads/${pr_head}" >/dev/null 2>&1
    fi

    # Record the exact head SHA we expect PR-build to run against.
    refreshed_sha_local="$(git rev-parse HEAD)"
    printf '%s' "$refreshed_sha_local" > "$refreshed_sha_file"

    git checkout "$(state_get '.meta.temp_branch')" >/dev/null 2>&1
  )
  rc=$?

  if [[ "$rc" -ne 0 ]]; then
    set_tag_phase "$lane" "$tag" "sync" "failure" "$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].pr.url")"
    mark_overall_failure "Failed sync PR hook for ${lane} ${tag}"
    return 1
  fi

  local refreshed_sha
  if [[ -f "$refreshed_sha_file" ]]; then
    refreshed_sha="$(cat "$refreshed_sha_file")"
    rm -f "$refreshed_sha_file"
  fi

  if [[ -z "$refreshed_sha" ]]; then
    for _ in $(seq 1 12); do
      abort_if_cancelled
      refreshed_sha="$(gh_bot pr view "$pr_number" -R "$PKG_REPO" --json headRefOid --jq '.headRefOid' 2>/dev/null || true)"
      if [[ -n "$refreshed_sha" ]]; then
        break
      fi
      abort_if_cancelled
      sleep 2
    done
  fi

  if [[ -z "$refreshed_sha" ]]; then
    set_tag_phase "$lane" "$tag" "sync" "failure" "$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].pr.url")"
    mark_overall_failure "Unable to read refreshed PR SHA for ${lane} ${tag}"
    return 1
  fi

  set_tag_pr_meta "$lane" "$tag" "$pr_number" "$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].pr.url")" "$pr_head" "$refreshed_sha"
  set_tag_phase "$lane" "$tag" "sync" "success" "$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].pr.url")"
  return 0
}

cmd_wait_pr_build() {
  ensure_state
  local lane="$1"
  local tag="$2"

  if [[ "$(state_get '.meta.skip')" == "true" ]]; then
    set_tag_phase "$lane" "$tag" "prbuild" "skipped" ""
    return 0
  fi

  if [[ "$(lane_is_enabled "$lane")" != "true" ]]; then
    set_tag_phase "$lane" "$tag" "prbuild" "skipped" ""
    return 0
  fi

  if [[ "$(get_tag_phase_status "$lane" "$tag" "sync")" != "success" ]]; then
    set_tag_phase "$lane" "$tag" "prbuild" "skipped" ""
    return 0
  fi

  local pr_head pr_head_sha
  pr_head="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].pr.head")"
  pr_head_sha="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].pr.head_sha")"

  if [[ -z "$pr_head" || -z "$pr_head_sha" ]]; then
    set_tag_phase "$lane" "$tag" "prbuild" "failure" ""
    mark_overall_failure "Missing PR branch/SHA for wait ${lane} ${tag}"
    return 1
  fi

  if wait_for_pr_build "$pr_head" "$pr_head_sha"; then
    set_tag_phase "$lane" "$tag" "prbuild" "success" "$LAST_RUN_URL"
    return 0
  fi

  set_tag_phase "$lane" "$tag" "prbuild" "failure" "${LAST_RUN_URL:-$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].pr.url")}" 
  mark_overall_failure "PR build failed for ${lane} ${tag}"
  return 1
}

cmd_merge_pr() {
  ensure_state
  local lane="$1"
  local tag="$2"

  if [[ "$(state_get '.meta.skip')" == "true" ]]; then
    set_tag_phase "$lane" "$tag" "merge" "skipped" ""
    return 0
  fi

  if [[ "$(lane_is_enabled "$lane")" != "true" ]]; then
    set_tag_phase "$lane" "$tag" "merge" "skipped" ""
    return 0
  fi

  if [[ "$(get_tag_phase_status "$lane" "$tag" "prbuild")" != "success" ]]; then
    set_tag_phase "$lane" "$tag" "merge" "skipped" ""
    return 0
  fi

  local pr_number pr_url
  pr_number="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].pr.number")"
  pr_url="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].pr.url")"

  if [[ -z "$pr_number" ]]; then
    set_tag_phase "$lane" "$tag" "merge" "failure" "$pr_url"
    mark_overall_failure "Missing PR number during merge ${lane} ${tag}"
    return 1
  fi

  if merge_promotion_pr "$pr_number"; then
    set_tag_phase "$lane" "$tag" "merge" "success" "$pr_url"
    return 0
  fi

  set_tag_phase "$lane" "$tag" "merge" "failure" "$pr_url"
  mark_overall_failure "Merge failed for ${lane} ${tag}"
  return 1
}

cmd_release_tag() {
  ensure_state
  local lane="$1"
  local tag="$2"

  if [[ "$(state_get '.meta.skip')" == "true" ]]; then
    set_tag_phase "$lane" "$tag" "release" "skipped" ""
    return 0
  fi

  if [[ "$(lane_is_enabled "$lane")" != "true" ]]; then
    set_tag_phase "$lane" "$tag" "release" "skipped" ""
    return 0
  fi

  if [[ "$(get_tag_phase_status "$lane" "$tag" "merge")" != "success" ]]; then
    set_tag_phase "$lane" "$tag" "release" "skipped" ""
    return 0
  fi

  local lane_branch
  lane_branch="$(lane_branch "$lane")"

  if dispatch_workflow_and_wait .github/workflows/pkg-release.yml "$(state_get '.meta.temp_branch')" --auto-approve-pending-deployments -f debian-branch="$lane_branch"; then
    set_tag_phase "$lane" "$tag" "release" "success" "$LAST_RUN_URL"
    return 0
  fi

  set_tag_phase "$lane" "$tag" "release" "failure" "$LAST_RUN_URL"
  mark_overall_failure "Release failed for ${lane} ${tag}"
  return 1
}

cmd_curate_ubuntu_wip_after_first_release() {
  ensure_state

  if [[ "$(state_get '.meta.skip')" == "true" ]]; then
    return 0
  fi

  if [[ "$(lane_is_enabled "ubuntu")" != "true" ]]; then
    return 0
  fi

  # Only needed between the first and second Ubuntu release in the loop.
  if [[ "$(get_tag_phase_status "ubuntu" "v1.0.0" "release")" != "success" ]]; then
    return 0
  fi

  if [[ -z "$BOT_TOKEN" ]]; then
    mark_overall_failure "BOT_TOKEN is required for ubuntu changelog curation"
    return 1
  fi

  local repo_dir ubuntu_branch temp_branch
  repo_dir="$(state_get '.meta.repo_dir')"
  ubuntu_branch="$(lane_branch "ubuntu")"
  temp_branch="$(state_get '.meta.temp_branch')"

  if [[ -z "$repo_dir" || ! -d "$repo_dir" ]]; then
    mark_overall_failure "Missing local repo clone during ubuntu changelog curation"
    return 1
  fi

  (
    cd "$repo_dir"

    git remote set-url origin "https://x-access-token:${BOT_TOKEN}@github.com/${PKG_REPO}.git"
    git fetch origin "$ubuntu_branch" >/dev/null 2>&1
    git checkout -B "$ubuntu_branch" "origin/$ubuntu_branch" >/dev/null 2>&1

    # The release flow deliberately seeds a WIP reminder entry for human curation.
    # For this automated e2e loop, replace WIP lines in the top changelog stanza.
    if grep -q 'WIP' debian/changelog; then
      awk '
        BEGIN { in_head = 1 }
        {
          if (in_head && $0 ~ /^ -- /) {
            in_head = 0
          }
          if (in_head && $0 ~ /WIP/) {
            sub(/WIP.*/, "New upstream release")
          }
          print
        }
      ' debian/changelog > debian/changelog.new
      mv debian/changelog.new debian/changelog
    fi

    if grep -q 'WIP' debian/changelog; then
      echo "Unable to clear WIP marker from ubuntu changelog entry" >&2
      git checkout "$temp_branch" >/dev/null 2>&1
      exit 1
    fi

    if ! git diff --quiet -- debian/changelog; then
      git add debian/changelog
      git commit -s -m "debian/changelog: curate post-release entry for e2e loop" >/dev/null 2>&1 || true
      git push origin "HEAD:refs/heads/${ubuntu_branch}" >/dev/null 2>&1
    fi

    git checkout "$temp_branch" >/dev/null 2>&1
  )
  rc=$?

  if [[ "$rc" -ne 0 ]]; then
    mark_overall_failure "Failed ubuntu changelog curation after v1.0.0 release"
    return 1
  fi

  return 0
}

cmd_write_summary() {
  ensure_state

  local overall_status
  if [[ "$(state_get '.meta.skip')" == "true" ]]; then
    overall_status="skipped"
  elif [[ "$(state_get '.meta.overall_failure')" == "true" ]]; then
    overall_status="failure"
  else
    overall_status="success"
  fi

  local qli_ci_ref qli_ci_pr_number temp_branch note promote_mode
  local enable_debian enable_ubuntu
  qli_ci_ref="$(state_get '.meta.qli_ci_ref')"
  qli_ci_pr_number="$(state_get '.meta.qli_ci_pr_number')"
  temp_branch="$(state_get '.meta.temp_branch')"
  promote_mode="$(state_get '.meta.promote_mode')"
  note="$(state_get '.meta.note')"
  enable_debian="$(state_get '.meta.enable_debian')"
  enable_ubuntu="$(state_get '.meta.enable_ubuntu')"

  {
    echo "## pkg-example e2e loop"
    echo
    echo "- qli-ci ref under test: \`$qli_ci_ref\`"
    if [[ -n "$qli_ci_pr_number" ]]; then
      echo "- qli-ci PR: #$qli_ci_pr_number"
    fi
    echo "- pkg-example temp branch: \`$temp_branch\`"
    echo "- promote mode: \`$promote_mode\`"
    echo "- path toggles: debian=${enable_debian}, ubuntu=${enable_ubuntu}"
    echo "- result: **$overall_status**"
    if [[ -n "$note" ]]; then
      echo "- note: $note"
    fi
    echo
    echo "| Lane | Tag | Reset | Seed Ubuntu | Promote | PR Build | Merge PR | Release |"
    echo "| --- | --- | --- | --- | --- | --- | --- | --- |"

    for lane in debian ubuntu; do
      local_reset_status="$(state_get ".lanes[\"$lane\"].reset.status")"
      local_reset_url="$(state_get ".lanes[\"$lane\"].reset.url")"
      local_seed_status="$(state_get ".lanes[\"$lane\"].seed.status")"
      local_seed_url="$(state_get ".lanes[\"$lane\"].seed.url")"

      for tag_index in "${!TAGS[@]}"; do
        tag="${TAGS[$tag_index]}"
        promote_status="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].promote.status")"
        promote_url="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].promote.url")"
        prbuild_status="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].prbuild.status")"
        prbuild_url="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].prbuild.url")"
        merge_status="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].merge.status")"
        merge_url="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].merge.url")"
        release_status="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].release.status")"
        release_url="$(state_get ".lanes[\"$lane\"].tags[\"$tag\"].release.url")"

        if [[ "$tag_index" -eq 0 ]]; then
          reset_cell="$(format_cell "$local_reset_status" "$local_reset_url")"
          seed_cell="$(format_cell "$local_seed_status" "$local_seed_url")"
        else
          reset_cell="$(format_cell "n/a" "")"
          seed_cell="$(format_cell "n/a" "")"
        fi

        echo "| $lane | $tag | $reset_cell | $seed_cell | $(format_cell "$promote_status" "$promote_url") | $(format_cell "$prbuild_status" "$prbuild_url") | $(format_cell "$merge_status" "$merge_url") | $(format_cell "$release_status" "$release_url") |"
      done
    done

    echo
    echo "Generated: $(iso_now)"
  } > "$SUMMARY_FILE"

  write_output "overall_status" "$overall_status"
  write_output "summary_file" "$SUMMARY_FILE"
  return 0
}

cmd_cleanup() {
  ensure_state

  local repo_dir temp_branch
  repo_dir="$(state_get '.meta.repo_dir')"
  temp_branch="$(state_get '.meta.temp_branch')"

  if [[ -n "$BOT_TOKEN" && -n "$temp_branch" && "$(state_get '.meta.skip')" != "true" ]]; then
    if [[ -n "$repo_dir" && -d "$repo_dir" ]]; then
      (
        cd "$repo_dir"
        git remote set-url origin "https://x-access-token:${BOT_TOKEN}@github.com/${PKG_REPO}.git"
        git push origin --delete "$temp_branch" >/dev/null 2>&1 || true
      ) || true
    fi
  fi

  if [[ -n "$repo_dir" && -d "$repo_dir" ]]; then
    rm -rf "$repo_dir"
  fi

  return 0
}

cmd_fail_if_needed() {
  ensure_state

  if [[ "$(state_get '.meta.skip')" == "true" ]]; then
    return 0
  fi

  if [[ "$(state_get '.meta.overall_failure')" == "true" ]]; then
    return 1
  fi

  return 0
}

usage() {
  cat <<USAGE
Usage:
  $0 init
  $0 prepare-temp-branch
  $0 reset-lane <debian|ubuntu>
  $0 seed-ubuntu
  $0 seed-prebuilt-fixtures [ubuntu]
  $0 promote-tag <debian|ubuntu> <tag> [source|prebuilt]
  $0 sync-pr-hook <debian|ubuntu> <tag>
  $0 wait-pr-build <debian|ubuntu> <tag>
  $0 merge-pr <debian|ubuntu> <tag>
  $0 release-tag <debian|ubuntu> <tag>
  $0 curate-ubuntu-wip-after-first-release
  $0 write-summary
  $0 cleanup
  $0 fail-if-needed
USAGE
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    init)
      cmd_init
      ;;
    prepare-temp-branch)
      cmd_prepare_temp_branch
      ;;
    reset-lane)
      shift
      cmd_reset_lane "${1:-}"
      ;;
    seed-ubuntu)
      cmd_seed_ubuntu
      ;;
    seed-prebuilt-fixtures)
      shift
      cmd_seed_prebuilt_fixtures "${1:-ubuntu}"
      ;;
    promote-tag)
      shift
      cmd_promote_tag "${1:-}" "${2:-}" "${3:-}"
      ;;
    sync-pr-hook)
      shift
      cmd_sync_pr_hook "${1:-}" "${2:-}"
      ;;
    wait-pr-build)
      shift
      cmd_wait_pr_build "${1:-}" "${2:-}"
      ;;
    merge-pr)
      shift
      cmd_merge_pr "${1:-}" "${2:-}"
      ;;
    release-tag)
      shift
      cmd_release_tag "${1:-}" "${2:-}"
      ;;
    curate-ubuntu-wip-after-first-release)
      cmd_curate_ubuntu_wip_after_first_release
      ;;
    write-summary)
      cmd_write_summary
      ;;
    cleanup)
      cmd_cleanup
      ;;
    fail-if-needed)
      cmd_fail_if_needed
      ;;
    *)
      usage
      return 1
      ;;
  esac
}

main "$@"
