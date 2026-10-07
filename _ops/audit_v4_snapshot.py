#!/usr/bin/env python3
"""Read-only parity audit after native import and offline history projection.

No credentials are opened and no values from sensitive columns are printed.
Use closed snapshots, not a live database whose WAL could change mid-audit.
"""
from collections import Counter
from decimal import Decimal
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

from migrate_history_v3 import ARCHIVE, fixed, ident, v3_id, headers


def closed(path):
    path = path.resolve()
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("audit requires a closed self-contained database")
    db = sqlite3.connect(f"file:{quote(str(path))}?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    return db


def cell(value):
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    return [type(value).__name__, value]


def table_digest(db, table):
    # A multiset, not a set: dropping duplicate rows must fail parity too.
    rows = Counter()
    for row in db.execute(f"SELECT * FROM {ident(table)}"):
        encoded = json.dumps([cell(v) for v in row], ensure_ascii=False,
                             separators=(",", ":")).encode()
        rows[hashlib.sha256(encoded).hexdigest()] += 1
    digest = hashlib.sha256(json.dumps(sorted(rows.items()), separators=(",", ":")).encode()).hexdigest()
    return sum(rows.values()), digest


def equal(actual, expected, context):
    if actual != expected:
        # Names locate the defect without exposing either compared value.
        raise AssertionError(f"parity mismatch: {context}")


def row_by_id(db, table, row_id, key="id"):
    row = db.execute(f"SELECT * FROM {ident(table)} WHERE {ident(key)}=?", (row_id,)).fetchone()
    if row is None:
        raise AssertionError(f"missing native row: {table}/{row_id}")
    return row


def audit_model_metadata(source, target, checked):
    strings = ["display_name", "description", "instructions", "default_reasoning_level",
               "default_service_tier", "shell_type", "default_verbosity",
               "default_reasoning_summary", "apply_patch_tool_type", "web_search_tool_type", "truncation_mode"]
    numbers = ["context_window", "max_output_tokens", "max_context_window", "truncation_limit",
               "auto_compact_token_limit", "effective_context_window_percent"]
    flags = {k: k for k in ["thinking_supported", "thinking_adaptive_supported", "thinking_enabled_supported",
                           "support_verbosity", "batch_supported", "citations_supported", "code_execution_supported",
                           "context_management_supported", "structured_outputs_supported", "pdf_input_supported"]}
    flags.update(reasoning_summary_supported="supports_reasoning_summary_parameter",
                 image_detail_original_supported="supports_image_detail_original", search_supported="supports_search_tool")
    known = {"input_modalities_known": "input_modalities", "output_modalities_known": "output_modalities",
             "parameters_known": "supported_parameters", "reasoning_levels_known": "reasoning_levels",
             "service_tiers_known": "service_tiers", "generation_methods_known": "generation_methods",
             "supported_actions_known": "supported_actions"}
    for old in source.execute("SELECT * FROM provider_models"):
        new = row_by_id(target, "provider_models", v3_id("provider_models", old["id"]))
        for field, expected in [("provider_id", v3_id("providers", old["provider_id"])),
                                ("upstream_name", old["model_id"]), ("enabled", old["enabled"])]:
            equal(new[field], expected, f"provider_model/{old['id']}/{field}")
        actual = json.loads(new["metadata"])
        expected = {}
        for field in strings + numbers:
            if old[field] is not None and old[field] != "":
                expected[field] = old[field]
        for column, key in flags.items():
            if old[column] is not None:
                expected[key] = bool(old[column])
        for column, key in known.items():
            if old[column]:
                expected[key] = []
        selector = (old["provider_id"], old["model_id"])
        for table, convert in [
            ("provider_model_modalities", lambda r: (r["direction"] + "_modalities", r["modality"])),
            ("provider_model_parameters", lambda r: ("supported_parameters", r["parameter"])),
            ("provider_model_reasoning_levels", lambda r: ("reasoning_levels", {"effort": r["effort"], "description": r["description"] or ""})),
            ("provider_model_service_tiers", lambda r: ("service_tiers", {"id": r["tier_id"], "name": r["name"] or "", "description": r["description"] or ""})),
            ("provider_model_methods", lambda r: ("generation_methods" if r["kind"] == "generation" else "supported_actions", r["method"])),
        ]:
            for row in source.execute(f"SELECT * FROM {ident(table)} WHERE provider_id=? AND model_id=? ORDER BY sort_order,id", selector):
                key, value = convert(row)
                expected.setdefault(key, []).append(value)
                checked[table] += 1
        for key, value in expected.items():
            equal(actual.get(key), value, f"provider_model/{old['id']}/metadata/{key}")
        variants = json.loads(old["variants_json"]) if old["variants_json"] else None
        names = variants if isinstance(variants, list) else variants.get("variants") if isinstance(variants, dict) else []
        for variant in names or []:
            if variant not in actual.get("variants", []):
                raise AssertionError(f"missing original model variant: {old['id']}")
        if names and isinstance(variants, dict) and "expose_base" in variants:
            equal(actual.get("expose_base"), variants["expose_base"], f"provider_model/{old['id']}/expose_base")
        checked["provider_models"] += 1


def audit(source_path, target_path):
    source, target = closed(source_path), closed(target_path)
    try:
        for name, db in [("source", source), ("target", target)]:
            equal(db.execute("PRAGMA integrity_check").fetchone()[0], "ok", name + " integrity")
        equal(target.execute("PRAGMA foreign_key_check").fetchall(), [], "target foreign keys")
        tables = [r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        archives = {}
        for table in tables:
            expected = table_digest(source, table)
            equal(table_digest(target, ARCHIVE + table), expected, table + " complete archive")
            archives[table] = {"rows": expected[0], "digest": expected[1]}
        checked = Counter()
        for old in source.execute("SELECT * FROM providers"):
            new = row_by_id(target, "providers", v3_id("providers", old["id"]))
            for field in ("name", "enabled"):
                equal(new[field], old[field], f"provider/{old['id']}/{field}")
            equal(new["display_name"], old["label"], f"provider/{old['id']}/label")
            config = json.loads(new["config"])
            equal(config.get("credential_strategy", "round_robin"), old["credential_strategy"] or "round_robin", f"provider/{old['id']}/strategy")
            settings = json.loads(old["settings_json"])
            if settings.get("base_url"):
                equal(new["base_url"], settings["base_url"], f"provider/{old['id']}/base_url")
            checked["providers"] += 1
        for old in source.execute("SELECT * FROM credentials"):
            new = row_by_id(target, "credentials", v3_id("credentials", old["id"]))
            for field in ("enabled", "label"):
                equal(new[field], old[field], f"credential/{old['id']}/{field}")
            equal(new["provider_id"], v3_id("providers", old["provider_id"]), f"credential/{old['id']}/provider")
            equal(new["auth_kind"], "oauth" if old["kind"] == "oauth_tokens" else old["kind"], f"credential/{old['id']}/kind")
            if not new["secret"]:
                raise AssertionError(f"missing sealed secret: credential/{old['id']}")
            # Nondefault weighting/TPM needs an explicit implementation, not an archive.
            if old["weight"] not in (0, 100) or old["tpm_limit"] is not None:
                raise AssertionError(f"unmapped effective credential policy: {old['id']}")
            checked["credentials"] += 1
        for old in source.execute("SELECT * FROM users"):
            new = row_by_id(target, "users", v3_id("users", old["id"]))
            for field in ("name", "password_hash", "enabled"):
                equal(new[field], old[field], f"user/{old['id']}/{field}")
            checked["users"] += 1
        for old in source.execute("SELECT * FROM user_keys"):
            new = row_by_id(target, "api_keys", v3_id("user_keys", old["id"]))
            digest = old["digest"]
            expected = digest.hex() if isinstance(digest, bytes) else digest
            equal(new["key_hash"], expected, f"key/{old['id']}/digest")
            equal(new["enabled"], old["enabled"], f"key/{old['id']}/enabled")
            checked["api_keys"] += 1
        for old in source.execute("SELECT * FROM price_rules"):
            new = row_by_id(target, "price_rules", v3_id("price_rules", old["id"]))
            for field in ("model_pattern", "priority", "enabled"):
                equal(new[field], old[field], f"price_rule/{old['id']}/{field}")
            equal(new["provider_id"], v3_id("providers", old["provider_id"]), f"price_rule/{old['id']}/provider")
            equal(new["currency"], "USD", f"price_rule/{old['id']}/currency")
            checked["price_rules"] += 1
            for index, tier in enumerate(json.loads(old["tiers_json"] or "[]")):
                new_tier = row_by_id(target, "price_tiers", v3_id("price_rules", old["id"]) + f"-tier-{index}")
                equal(new_tier["service_tier"], tier.get("service_tier"), f"price_tier/{old['id']}/{index}/service_tier")
                equal(new_tier["min_prompt_tokens"], tier.get("min_prompt_tokens", 0), f"price_tier/{old['id']}/{index}/threshold")
                equal(new_tier["priority"], index, f"price_tier/{old['id']}/{index}/priority")
                for field in ["multiplier", "input", "output", "cache_read", "cache_creation_5m", "cache_creation_30m", "cache_creation_1h", "image_output"]:
                    column = field if field == "multiplier" else field + "_per_million"
                    expected = tier.get(field) if field in tier else tier.get(field + "_price")
                    equal(new_tier[column], fixed(expected), f"price_tier/{old['id']}/{index}/{field}")
                checked["price_tiers"] += 1
        for old in source.execute("SELECT * FROM price_rates"):
            new = row_by_id(target, "price_rates", v3_id("price_rates", old["id"]))
            for field in ("metric", "priority"):
                equal(new[field], old[field], f"price_rate/{old['id']}/{field}")
            equal(int(new["value"]), fixed(old["price"]), f"price_rate/{old['id']}/value")
            equal(int(new["unit_quantity"]), fixed(old["unit_size"]), f"price_rate/{old['id']}/unit")
            equal(new["price_rule_id"], v3_id("price_rules", old["rule_id"]), f"price_rate/{old['id']}/rule")
            equal(json.loads(new["conditions"]) if new["conditions"] else None,
                  json.loads(old["conditions_json"]) if old["conditions_json"] else None,
                  f"price_rate/{old['id']}/conditions")
            checked["price_rates"] += 1
        for old in source.execute("SELECT * FROM usage_rows"):
            new = row_by_id(target, "usage_records", v3_id("usage_rows", old["id"]), "request_id")
            equal(new["input_tokens"], max(0, old["input_tokens"] - old["cached_input_tokens"]), f"usage/{old['id']}/input")
            for field in ("output_tokens", "cached_input_tokens"):
                equal(new[field], old[field], f"usage/{old['id']}/{field}")
            equal(int(new["cost"]), fixed(old["cost"]), f"usage/{old['id']}/cost")
            original = json.loads(new["metrics"])["v3"]
            equal(original["request_id"], old["request_id"], f"usage/{old['id']}/original_request")
            equal(Decimal(original["cost"]), Decimal(old["cost"]), f"usage/{old['id']}/unrounded_cost")
            checked["usage_records"] += 1
        exposed = {r["route_id"]: r for r in source.execute("SELECT * FROM exposed_models ORDER BY id")}
        for old in source.execute("SELECT * FROM routes"):
            new = row_by_id(target, "routes", v3_id("routes", old["id"]))
            public = exposed.get(old["id"])
            equal(new["name"], public["name"] if public else old["name"], f"route/{old['id']}/name")
            equal(new["enabled"], bool(old["enabled"]) and (bool(public["enabled"]) if public else True), f"route/{old['id']}/enabled")
            for field in ("max_attempts", "strategy"):
                equal(new[field], old[field], f"route/{old['id']}/{field}")
            checked["routes"] += 1
        for old in source.execute("SELECT * FROM route_members"):
            new = row_by_id(target, "route_members", v3_id("route_members", old["id"]))
            for field in ("upstream_model", "tier", "weight", "enabled"):
                equal(new[field], old[field], f"route_member/{old['id']}/{field}")
            for field, table in [("provider_id", "providers"), ("route_id", "routes")]:
                equal(new[field], v3_id(table, old[field]), f"route_member/{old['id']}/{field}")
            if old["credential_id"] is not None or old["priority"] != 0:
                raise AssertionError(f"unmapped effective route-member policy: {old['id']}")
            checked["route_members"] += 1
        dialect = {"openai_responses": "openai", "claude_messages": "claude", "gemini_generate_content": "gemini"}
        operation_rows = target.execute("SELECT * FROM operation_rules WHERE action='routing'").fetchall()
        rules = {(r["provider_id"], r["operation"]): json.loads(r["target"]) for r in operation_rows}
        seen = set()
        for old in source.execute("SELECT * FROM routing_rules WHERE origin='operator' AND enabled=1 ORDER BY sort_order,id"):
            src = dialect.get(old["kind"], old["kind"])
            key = (v3_id("providers", old["provider_id"]), old["operation"])
            if (*key, src) in seen:
                continue
            seen.add((*key, src))
            route = {"implementation": old["implementation"]}
            if old["implementation"] in ("transform", "transform_to"):
                route = {"implementation": "transform_to", "target": {
                    "operation": old["dest_operation"], "dialect": dialect.get(old["dest_kind"], old["dest_kind"])}}
            equal(rules.get(key, {}).get(src), route, f"operator_routing/{old['id']}")
            checked["operator_routing_mappings"] += 1
        for old in source.execute("SELECT * FROM rules"):
            if old["kind"] != "rewrite":
                raise AssertionError("this audit needs an explicit check for the source rule kind")
            new = row_by_id(target, "rewrite_rules", v3_id("rules", old["id"]) + "-0")
            cfg = json.loads(old["config_json"])
            equal(new["action"], cfg["action"], f"rewrite/{old['id']}/action")
            equal(json.loads(new["paths"]), [cfg["path"]], f"rewrite/{old['id']}/path")
            equal(json.loads(new["replacement"]), cfg["value_json"], f"rewrite/{old['id']}/value")
            for field in ("filter_model_pattern", "filter_header_pattern", "sort_order", "enabled"):
                equal(new[field], old[field], f"rewrite/{old['id']}/{field}")
            ops = json.loads(old["filter_operations_json"]) if old["filter_operations_json"] else []
            expected = {(o, d) for o in ops for d in ("openai", "openai_chat", "openai_responses_websocket", "claude", "gemini")}
            actual = {(r["operation"], r["dialect"]) for r in json.loads(new["filter_operation_keys"] or "[]")}
            equal(actual, expected, f"rewrite/{old['id']}/operations")
            checked["rewrite_rules"] += 1
        for old in source.execute("SELECT * FROM rule_sets"):
            new = row_by_id(target, "rewrite_rule_sets", v3_id("rule_sets", old["id"]))
            for field in ("name", "description", "enabled"):
                equal(new[field], old[field], f"rule_set/{old['id']}/{field}")
            checked["rewrite_rule_sets"] += 1
        for old in source.execute("SELECT * FROM provider_rule_sets"):
            new = row_by_id(target, "provider_rewrite_rule_sets", v3_id("provider_rule_sets", old["id"]))
            for field, table in [("provider_id", "providers"), ("rule_set_id", "rule_sets")]:
                equal(new[field], v3_id(table, old[field]), f"rule_attachment/{old['id']}/{field}")
            for field in ("sort_order", "enabled"):
                if field in old.keys():
                    equal(new[field], old[field], f"rule_attachment/{old['id']}/{field}")
            checked["provider_rewrite_rule_sets"] += 1
        for old in source.execute("SELECT * FROM aliases WHERE enabled=1 ORDER BY priority,id"):
            if old["provider_id"] is None:
                raise AssertionError("this audit requires a dedicated global-alias check")
            candidates = target.execute("SELECT metadata FROM provider_models WHERE provider_id=? AND upstream_name=?",
                                        (v3_id("providers", old["provider_id"]), old["target"])).fetchall()
            variants = [v for r in candidates for v in json.loads(r[0]).get("variants", [])]
            if not any((v.get("name") if isinstance(v, dict) else v) == old["alias"] for v in variants):
                raise AssertionError(f"missing provider alias: {old['id']}")
            checked["aliases"] += 1
        audit_model_metadata(source, target, checked)
        for table, native in [("wire_logs", "upstream_records"), ("request_logs", "downstream_records")]:
            for old in source.execute(f"SELECT * FROM {ident(table)}"):
                # Upstream ids are stable; downstream ids may be linked to usage.
                candidates = target.execute(f"SELECT * FROM {ident(native)} WHERE json_extract(metrics,'$.v3.id')=?", (old["id"],)).fetchall()
                equal(len(candidates), 1, f"capture/{table}/{old['id']}/count")
                new = candidates[0]
                for field in ("request_body", "response_body"):
                    equal(new[field], old[field], f"capture/{table}/{old['id']}/{field}")
                for field in ("request_headers", "response_headers"):
                    equal(new[field], headers(old[field]), f"capture/{table}/{old['id']}/{field}")
                checked[native] += 1
        for old in source.execute("SELECT * FROM credential_quota_cycles"):
            new = row_by_id(target, "credential_cycles", v3_id("credential_quota_cycles", old["id"]))
            equal(new["credential_id"], v3_id("credentials", old["credential_id"]), f"quota_cycle/{old['id']}/credential")
            equal(new["window_id"], old["window_key"], f"quota_cycle/{old['id']}/window")
            equal(new["cost_usd"], fixed(json.loads(old["metrics_json"]).get("cost", "0")), f"quota_cycle/{old['id']}/cost")
            equal(new["closed_at_ms"], old["accounting_end_ms"] if old["status"] == "closed" else None, f"quota_cycle/{old['id']}/closed")
            checked["credential_cycles"] += 1
        # Old health is model/version-specific. A stale dead observation must
        # not retire a newer token; a current one requires an explicit port.
        current_dead = source.execute("SELECT count(*) FROM credential_health h JOIN credentials c ON c.id=h.credential_id WHERE h.state='dead' AND h.credential_version=c.version").fetchone()[0]
        if current_dead:
            raise AssertionError("current v3 dead credential state requires a native lifecycle port")
        policy = dict(source.execute("SELECT key,value_json FROM settings"))
        settings = row_by_id(target, "settings", 1)
        for field in ("retention_days", "max_database_size_mb", "enable_auto_update_check", "enable_usage", "enable_downstream_log", "enable_downstream_log_body", "enable_upstream_log", "enable_upstream_log_body", "disable_log_redaction"):
            if field in policy:
                equal(settings[field], json.loads(policy[field]), f"settings/{field}")
        if "retention_days" in policy:
            for field in ("capture_payload_retention_days", "quota_observation_retention_days"):
                equal(settings[field], json.loads(policy["retention_days"]), f"settings/{field}")
        equal(settings["capture_payload_max_mb"], None, "settings/payload_budget")
        return {"passed": True, "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                "archives": archives, "native_checks": dict(checked), "integrity": "ok", "foreign_key_errors": 0}
    finally:
        source.close()
        target.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--target", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(audit(args.source, args.target), indent=2))


if __name__ == "__main__":
    main()
