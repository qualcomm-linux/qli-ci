# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Check and apply GitHub repository rulesets against repo-configs.json."""

import json
import tempfile
from typing import Any, Dict, List, Optional, Set

from gh_helpers import get_user_id, run_gh_command


def build_packaging_branch_ref_patterns(schema: Dict[str, Any]) -> List[str]:
    """
    Derive concrete refs/heads/... glob patterns from a packaging_branch_schema
    config, for use in a GitHub ruleset's ref_name conditions. This mirrors
    build_packaging_branch_pattern() in configure-repo (which builds a regex
    for reporting), but emits literal glob patterns instead: each shape's
    'family/suite' pair expands to one pattern per (family, suite)
    combination, and '*' is passed through as-is for GitHub to match (GitHub
    ref_name patterns use fnmatch with FNM_PATHNAME, so '*' never crosses
    '/').
    """
    families = schema["families"]
    family_suite_pairs = [
        (family, suite) for family, suites in families.items() for suite in suites
    ]

    patterns = []
    for shape in schema["shapes"]:
        segments = shape.split("/")
        for family, suite in family_suite_pairs:
            parts = []
            i = 0
            while i < len(segments):
                if segments[i:i + 2] == ["family", "suite"]:
                    parts.extend([family, suite])
                    i += 2
                elif segments[i] == "*":
                    parts.append("*")
                    i += 1
                else:
                    raise ValueError(f"unknown packaging branch shape segment: {segments[i]!r}")
            patterns.append("refs/heads/" + "/".join(parts))
    return patterns


def resolve_ruleset_target_patterns(tag: str, config: Dict[str, Any]) -> List[str]:
    """Dispatch a rulesets[...]['target_branches'] tag to concrete ref patterns."""
    if tag == "packaging_branch_schema":
        return build_packaging_branch_ref_patterns(config["packaging_branch_schema"])
    if tag == "default_branch":
        return [f"refs/heads/{config['default_branch']}"]
    raise ValueError(f"unknown ruleset target_branches tag: {tag!r}")


def resolve_bypass_actors(bypass_specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Resolve repo-configs.json bypass actor specs into GitHub API bypass_actors entries."""
    resolved = []
    for spec in bypass_specs:
        if spec["type"] != "user":
            raise ValueError(f"unknown bypass actor type: {spec['type']!r}")
        resolved.append({
            "actor_type": "User",
            "actor_id": get_user_id(spec["login"]),
            "bypass_mode": spec["bypass_mode"],
        })
    return resolved


def get_ruleset(repo: str, name: str) -> Optional[Dict[str, Any]]:
    """Get full ruleset detail by name, or None if no ruleset with that name exists."""
    stdout = run_gh_command(["api", f"repos/{repo}/rulesets"])
    for entry in json.loads(stdout):
        if entry["name"] == name:
            stdout = run_gh_command(["api", f"repos/{repo}/rulesets/{entry['id']}"])
            return json.loads(stdout)
    return None


# GitHub's ruleset write API (create/update) requires a complete parameters
# object for rule types that have required sub-fields - unlike the read/verify
# path, there's no partial-object support. Rule types not listed here either
# take no parameters (e.g. non_fast_forward) or have none of the config we
# specify overlapping with a GitHub-required field. Only the sub-fields not
# already covered by repo-configs.json need a default here.
RULE_TYPE_PARAMETER_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "pull_request": {
        "dismiss_stale_reviews_on_push": False,
        "require_code_owner_review": False,
        "require_last_push_approval": False,
        "required_review_thread_resolution": False,
    },
}


def _rule_object(rule_type: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged_parameters = {**RULE_TYPE_PARAMETER_DEFAULTS.get(rule_type, {}), **parameters}
    if merged_parameters:
        return {"type": rule_type, "parameters": merged_parameters}
    return {"type": rule_type}


def _rule_matches(existing_rule: Dict[str, Any], expected_params: Dict[str, Any]) -> bool:
    existing_params = existing_rule.get("parameters", {})
    return all(existing_params.get(key) == value for key, value in expected_params.items())


def _bypass_actor_matches(existing: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    return (
        existing.get("actor_type") == expected["actor_type"]
        and existing.get("actor_id") == expected["actor_id"]
        and existing.get("bypass_mode") == expected["bypass_mode"]
    )


def _put_ruleset(
    repo: str,
    ruleset_id: Optional[int],
    name: str,
    enforcement: str,
    ref_patterns: Set[str],
    bypass_actors: List[Dict[str, Any]],
    rules: List[Dict[str, Any]],
) -> None:
    payload = {
        "name": name,
        "target": "branch",
        "enforcement": enforcement,
        "conditions": {"ref_name": {"include": sorted(ref_patterns), "exclude": []}},
        "bypass_actors": bypass_actors,
        "rules": rules,
    }
    method = "POST" if ruleset_id is None else "PUT"
    path = f"repos/{repo}/rulesets" if ruleset_id is None else f"repos/{repo}/rulesets/{ruleset_id}"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
        json.dump(payload, f)
        f.flush()
        run_gh_command(["api", path, "-X", method, "--input", f.name])


def check_ruleset(
    repo: str, name: str, ruleset_config: Dict[str, Any], config: Dict[str, Any], dry_run: bool
) -> bool:
    """
    Check (and, unless dry_run, create/fix) a single named ruleset. Returns
    True if changes are needed (dry_run) or were made (not dry_run).

    Only the fields specified in ruleset_config are verified/enforced: rule
    sub-fields not present in our spec are ignored (GitHub returns many
    implicit defaulted sub-fields for rule types we don't fully specify), and
    on fix, existing-but-unmanaged rule types and ref patterns/bypass actors
    are preserved, not removed (same additive philosophy as
    ensure_required_reviewers).
    """
    changes_needed = False

    expected_patterns = set(resolve_ruleset_target_patterns(ruleset_config["target_branches"], config))
    expected_bypass_actors = resolve_bypass_actors(ruleset_config["bypass_actors"])
    expected_enforcement = ruleset_config["enforcement"]
    expected_rules = ruleset_config["rules"]

    existing = get_ruleset(repo, name)

    if existing is None:
        print(f"❌ Ruleset '{name}' does not exist")
        changes_needed = True
        if not dry_run:
            rules = [_rule_object(rule_type, params) for rule_type, params in expected_rules.items()]
            _put_ruleset(repo, None, name, expected_enforcement, expected_patterns, expected_bypass_actors, rules)
            print(f"✓ Created ruleset '{name}'")
        return changes_needed

    print(f"✓ Ruleset '{name}' exists")

    if existing.get("enforcement") != expected_enforcement:
        print(f"❌ Ruleset '{name}' enforcement is '{existing.get('enforcement')}', must be '{expected_enforcement}'")
        changes_needed = True
    else:
        print(f"✓ Ruleset '{name}' enforcement is '{expected_enforcement}'")

    existing_patterns = set(existing.get("conditions", {}).get("ref_name", {}).get("include", []))
    missing_patterns = expected_patterns - existing_patterns
    if missing_patterns:
        print(f"❌ Ruleset '{name}' is missing ref patterns: {', '.join(sorted(missing_patterns))}")
        changes_needed = True
    else:
        print(f"✓ Ruleset '{name}' targets all expected ref patterns")

    existing_rules_by_type = {r["type"]: r for r in existing.get("rules", [])}
    for rule_type, expected_params in expected_rules.items():
        existing_rule = existing_rules_by_type.get(rule_type)
        if existing_rule is None:
            print(f"❌ Ruleset '{name}' is missing rule '{rule_type}'")
            changes_needed = True
        elif not _rule_matches(existing_rule, expected_params):
            print(f"❌ Ruleset '{name}' rule '{rule_type}' does not match expected parameters")
            changes_needed = True
        else:
            print(f"✓ Ruleset '{name}' rule '{rule_type}' is correctly configured")

    existing_bypass_actors = existing.get("bypass_actors", [])
    missing_bypass_actors = [
        actor for actor in expected_bypass_actors
        if not any(_bypass_actor_matches(existing_actor, actor) for existing_actor in existing_bypass_actors)
    ]
    if missing_bypass_actors:
        print(f"❌ Ruleset '{name}' is missing bypass actors: {missing_bypass_actors}")
        changes_needed = True
    else:
        print(f"✓ Ruleset '{name}' has all expected bypass actors")

    if changes_needed and not dry_run:
        merged_patterns = existing_patterns | expected_patterns
        merged_bypass_actors = existing_bypass_actors + missing_bypass_actors
        merged_rules_by_type = dict(existing_rules_by_type)
        for rule_type, expected_params in expected_rules.items():
            merged_rules_by_type[rule_type] = _rule_object(rule_type, expected_params)
        _put_ruleset(
            repo,
            existing["id"],
            name,
            expected_enforcement,
            merged_patterns,
            merged_bypass_actors,
            list(merged_rules_by_type.values()),
        )
        print(f"✓ Updated ruleset '{name}'")

    return changes_needed


def check_rulesets(repo: str, config: Dict[str, Any], dry_run: bool) -> bool:
    """Check (and, unless dry_run, fix) all rulesets defined in config['rulesets']."""
    changes_needed = False
    for index, (name, ruleset_config) in enumerate(config["rulesets"].items()):
        if index > 0:
            print()
        if check_ruleset(repo, name, ruleset_config, config, dry_run):
            changes_needed = True
    return changes_needed
