#!/usr/bin/env python3
"""Offline complement to the native v3 importer.

The native importer moves configuration, identities and usage, but intentionally
leaves the capture and administrative history behind. This tool copies every
original table into a namespaced, lossless archive and also projects captures
and audit events into the v4 console's native tables. It never decrypts a secret,
opens a network connection, or changes the v3 source. Run only on a stopped,
isolated v4 database after the native configuration/usage import has succeeded.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote, urlsplit

ARCHIVE = "gproxy_v3_archive_"


def ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def v3_id(table: str, value: int | None) -> str | None:
    return None if value is None else f"v3-{table}-{value}"


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def fixed(value) -> int | None:
    if value is None:
        return None
    atoms = int((Decimal(str(value)) * Decimal(10**9)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
    if not -(2**63) <= atoms < 2**63:
        raise ValueError("historical decimal is outside v4's fixed-decimal range")
    return atoms


def millis(value: int | None) -> int | None:
    return None if value is None else int(value) * 1000


def scope(value) -> object:
    """v3's tagged scope -> v4's externally tagged scope, with no inference."""
    if value is None:
        return "unknown"
    if not isinstance(value, dict) or "kind" not in value:
        return value
    kind = value["kind"]
    if kind in ("all", "unknown"):
        return kind
    if kind in ("models", "model_prefixes", "except_models"):
        return {kind: value.get("models", [])}
    raise ValueError(f"unsupported historical quota scope {kind!r}")


def headers(text: str | None) -> str | None:
    if text is None:
        return None
    value = json.loads(text)
    if isinstance(value, list):
        if not all(isinstance(p, list) and len(p) == 2 for p in value):
            raise ValueError("malformed historical header-pair list")
        return json_text(value)
    if not isinstance(value, dict):
        raise ValueError("historical headers are neither an object nor pairs")
    pairs = []
    for name, values in value.items():
        for v in values if isinstance(values, list) else [values]:
            if not isinstance(v, str):
                raise ValueError("historical header value is not text")
            pairs.append([name, v])
    return json_text(pairs)


def rows(db: sqlite3.Connection, table: str):
    db.row_factory = sqlite3.Row
    return db.execute(f"SELECT * FROM {ident(table)} ORDER BY id")


def insert(db: sqlite3.Connection, table: str, values: dict):
    names = list(values)
    db.execute(
        f"INSERT INTO {ident(table)} ({','.join(map(ident, names))}) VALUES ({','.join('?' for _ in names)})",
        [values[n] for n in names],
    )


def archive(db: sqlite3.Connection, source_uri: str) -> dict[str, int]:
    db.execute("ATTACH DATABASE ? AS legacy_source", (source_uri,))
    tables = db.execute(
        "SELECT name FROM legacy_source.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    counts = {}
    for (name,) in tables:
        target = ARCHIVE + name
        # CREATE TABLE AS preserves every value and column name, while avoiding
        # old global index names, foreign-key targets and migration ledgers.
        db.execute(f"CREATE TABLE {ident(target)} AS SELECT * FROM legacy_source.{ident(name)}")
        counts[name] = db.execute(f"SELECT count(*) FROM {ident(target)}").fetchone()[0]
    return counts


def captures(source: sqlite3.Connection, target: sqlite3.Connection, tables: set[str]) -> dict:
    usage = {r["request_id"]: dict(r) for r in rows(source, "usage_rows")} if "usage_rows" in tables else {}
    downstream_ids = {}
    counts = {}
    for old_table, new_table, upstream in (
        ("request_logs", "downstream_records", False),
        ("wire_logs", "upstream_records", True),
    ):
        count = 0
        if old_table not in tables:
            continue
        for raw in rows(source, old_table):
            row = dict(raw)
            original_id = row["request_id"]
            u = usage.get(original_id)
            request_id = (
                v3_id("usage_rows", u["id"]) if u else
                downstream_ids.get(original_id) or (
                    "v3-request-" + hashlib.sha256(original_id.encode()).hexdigest()
                    if upstream else v3_id("request_logs", row["id"])
                )
            )
            record_id = v3_id("wire_logs", row["id"]) if upstream else request_id
            started = millis(row["at"])
            ended = None
            if u:
                ended = (u["upstream_started_at_ms"] + u["latency_ms"]
                         if u.get("upstream_started_at_ms") is not None else millis(u["at"]))
            # v3 did not retain stream event timestamps. Preserve the exact
            # byte blob as a buffered legacy capture, not invented SSE events.
            complete = u is not None and u["ended"] == "complete"
            state = "completed" if complete else "failed"
            url = row.get("upstream_url") if upstream else row.get("path")
            query = row.get("query")
            if upstream and url:
                split = urlsplit(url)
                query = split.query or None
                url = url.split("?", 1)[0]
            data = {
                "id": record_id, "started_at_ms": started,
                "initiator_request_id": request_id, "kind": "http",
                "provider_id": v3_id("providers", row.get("provider_id")),
                "credential_id": v3_id("credentials", row.get("credential_id")),
                "user_id": v3_id("users", u.get("user_id")) if u else None,
                "api_key_id": v3_id("user_keys", u.get("user_key_id")) if u else None,
                "model": u.get("upstream_model") if u else None,
                "operation": u.get("operation") if u else None,
                "request_method": row.get("request_method") if upstream else row.get("method"),
                "request_url": url, "request_query": query,
                "request_headers": headers(row.get("request_headers")),
                "response_headers": headers(row.get("response_headers")),
                "request_body": row.get("request_body"), "response_body": row.get("response_body"),
                "request_body_encoding": "identity", "response_body_encoding": "identity",
                "request_framing": "buffered", "response_framing": "buffered",
                "request_body_state": "complete" if row.get("request_body") is not None else "not_captured",
                "response_body_state": ("complete" if complete else "partial") if row.get("response_body") is not None else "not_captured",
                "response_status": row.get("response_status"),
                "client_ip": row.get("client_ip"), "state": state,
                "error": row.get("error_kind") or (None if complete else "Legacy capture end state was not complete or was not recorded"),
                "ended_at_ms": ended,
                "metrics": json_text({"v3": {"table": old_table, "id": row["id"], "request_id": original_id, "at": row["at"], "ended": u.get("ended") if u else None}}),
            }
            insert(target, new_table, data)
            count += 1
            if upstream:
                # Associations intentionally have no FKs in v4: downstream
                # logging can have been disabled while usage was still kept.
                insert(target, "capture_links", {"downstream_id": downstream_ids.get(original_id, request_id), "upstream_id": record_id})
            else:
                downstream_ids[original_id] = record_id
        counts[new_table] = count
    return counts


def audit(source: sqlite3.Connection, target: sqlite3.Connection, tables: set[str]) -> int:
    if "admin_audit_events" not in tables:
        return 0
    count = 0
    for raw in rows(source, "admin_audit_events"):
        row = dict(raw)
        detail = json.loads(row["details_json"]) if row.get("details_json") else None
        # v3 had no explicit outcome column. Do not silently manufacture "ok".
        insert(target, "audit_events", {
            "id": v3_id("admin_audit_events", row["id"]),
            "actor_user_id": v3_id("users", row.get("actor_user_id")),
            "source_ip": row.get("client_ip"), "action": row["action"],
            "entity_kind": row.get("target_kind"),
            "entity_id": v3_id(row["target_kind"], row.get("target_id")) if row.get("target_kind") else None,
            "outcome": "unknown",
            "detail": json_text({"v3": {"id": row["id"], "target_id": row.get("target_id"), "details": detail}}),
            "created_at_ms": millis(row["at"]),
        })
        count += 1
    return count


def quota_snapshot(entry: dict) -> dict:
    """Flatten a v3 observation into core's persisted v4 observation shape."""
    value = entry.get("value", {})
    out = {k: entry.get(k) for k in ("id", "source_id", "label", "subject")}
    out.update({k: value.get(k) for k in ("kind", "used", "limit", "remaining", "used_percent", "unlimited", "unit")})
    out["period_start_ms"] = millis(value.get("period_start"))
    out["period_end_ms"] = millis(value.get("period_end"))
    out["reset_behavior"] = value.get("reset_behavior", "unknown")
    if value.get("kind") == "breakdown":
        out["breakdown"] = value.get("rows", [])
    return out


def quota_history(source: sqlite3.Connection, target: sqlite3.Connection, tables: set[str]) -> dict:
    """Preserve durable reset ordering, historical cycles and known blocks.

    Raw originals remain in the archive. v3 did not store the USD at each
    observation, so native observation/sample costs stay null rather than
    producing a fabricated allowance estimate.
    """
    cycles = {r["id"]: dict(r) for r in rows(source, "credential_quota_cycles")} if "credential_quota_cycles" in tables else {}
    source_ids = {}
    if "credential_quota_sources" in tables:
        for r in source.execute("SELECT credential_id,source_id FROM credential_quota_sources"):
            source_ids.setdefault(r[0], set()).add(r[1])
    for row in cycles.values():
        tracking = json.loads(row["tracking_json"])
        metrics = json.loads(row["metrics_json"])
        credential_id = v3_id("credentials", row["credential_id"])
        window = row["window_key"]
        sample_at = tracking.get("sample", {}).get("received_at_ms") or millis(row["last_observed_at"])
        closed = row["accounting_end_ms"] if row["status"] == "closed" else None
        if row["status"] == "closed" and closed is None:
            raise ValueError(f"closed v3 quota cycle {row['id']} has no recorded closing time")
        insert(target, "credential_cycles", {
            "id": v3_id("credential_quota_cycles", row["id"]),
            "credential_id": credential_id, "closed_at_ms": closed,
            "open_key": f"{len(credential_id)}:{credential_id}:{window}" if closed is None else None,
            "window_id": window, "dimension_id": window,
            "scope": json_text(scope(tracking.get("scope"))),
            "starts_at_ms": row["accounting_start_ms"],
            "ends_at_ms": millis(row["period_end"]),
            "boundary": "observed" if row["boundary_source"] == "upstream" and not tracking.get("local_boundary") else "local",
            "opened_by": "first_use",
            "cost_usd": fixed(metrics.get("cost", "0")),
            "sample_used_percent": fixed(row.get("used_percent")),
            "sample_used": fixed(row.get("upstream_used")),
            "sample_limit": fixed(row.get("upstream_limit")),
            "sample_cost_usd": None, "sample_at_ms": sample_at,
        })
    observation_count = 0
    if "credential_quota_observations" in tables:
        for raw in rows(source, "credential_quota_observations"):
            row = dict(raw)
            cycle = cycles.get(row["cycle_id"])
            if cycle is None:
                raise ValueError(f"v3 quota observation {row['id']} has no parent cycle")
            snapshot = json.loads(row["snapshot_json"])
            original = snapshot.get("raw", {})
            candidates = source_ids.get(cycle["credential_id"], set())
            source_id = next(iter(candidates)) if len(candidates) == 1 else "legacy"
            entry = {
                "id": cycle["window_key"], "source_id": source_id,
                "label": cycle.get("label"), "subject": "unknown",
                "value": {
                    "kind": "window", "used": snapshot.get("upstream_used"),
                    "limit": snapshot.get("upstream_limit"),
                    "used_percent": snapshot.get("used_percent"),
                    "unit": snapshot.get("unit"),
                    "period_start": original.get("period_start"),
                    "period_end": original.get("period_end"),
                    "reset_behavior": original.get("reset_behavior", "unknown"),
                },
            }
            flattened = quota_snapshot(entry)
            flattened["v3"] = snapshot
            insert(target, "credential_quota_cycles", {
                "id": v3_id("credential_quota_observations", row["id"]),
                "credential_id": v3_id("credentials", cycle["credential_id"]),
                "observed_at_ms": row["observed_at_ms"],
                "scope": json_text(scope(snapshot.get("scope") or original.get("scope"))),
                "snapshot": json_text(flattened),
                "starts_at_ms": flattened["period_start_ms"],
                "resets_at_ms": flattened["period_end_ms"],
                "credential_cycle_id": v3_id("credential_quota_cycles", row["cycle_id"]),
                "cycle_cost_usd": None,
            })
            observation_count += 1
    # Old raw observation pages do not always contain the latest cached read.
    # Project the authoritative cache explicitly; no live upstream is queried.
    cached_count = blocked_count = 0
    now = int(time.time() * 1000)
    if "credential_quota_sources" in tables:
        for raw in source.execute("SELECT * FROM credential_quota_sources"):
            row = dict(raw)
            credential_id = v3_id("credentials", row["credential_id"])
            if not target.execute("SELECT 1 FROM credentials WHERE id=?", (credential_id,)).fetchone():
                continue
            for entry in json.loads(row["entries_json"]):
                flattened = quota_snapshot(entry)
                obs_id = f"v3-quota-source-{row['credential_id']}-{row['source_id']}-{entry['id']}"
                flattened["v3"] = entry
                observed_at = entry.get("observed_at_ms") or row.get("observed_at_ms")
                if observed_at is None:
                    raise ValueError("cached v3 quota entry has no observation timestamp")
                parent = next((c for c in cycles.values() if c["credential_id"] == row["credential_id"] and c["window_key"] == entry["id"] and c["status"] == "open"), None)
                insert(target, "credential_quota_cycles", {
                    "id": obs_id, "credential_id": credential_id,
                    "observed_at_ms": observed_at,
                    "scope": json_text(scope(entry.get("model_scope"))),
                    "snapshot": json_text(flattened),
                    "starts_at_ms": flattened["period_start_ms"],
                    "resets_at_ms": flattened["period_end_ms"],
                    "credential_cycle_id": v3_id("credential_quota_cycles", parent["id"]) if parent else None,
                    "cycle_cost_usd": None,
                })
                cached_count += 1
                used = flattened.get("used_percent")
                end = flattened.get("period_end_ms")
                if (entry.get("label") != "antigravity_disabled" and used is not None
                        and Decimal(str(used)) >= 100 and end is not None and end > now):
                    insert(target, "credential_blocks", {
                        "id": "v3-block-" + hashlib.sha256(obs_id.encode()).hexdigest(),
                        "credential_id": credential_id,
                        "scope": json_text(scope(entry.get("model_scope"))), "operation": None,
                        "until_ms": end, "observed_at_ms": observed_at,
                        "source": json_text({"kind": "quota_exhausted", "dimension": entry["id"], "cycle_id": obs_id}),
                    })
                    blocked_count += 1
    return {"credential_cycles": len(cycles), "quota_observations": observation_count,
            "cached_quota_observations": cached_count, "quota_blocks": blocked_count}


def migrate(source_path: Path, target_path: Path) -> dict:
    source_path, target_path = source_path.resolve(), target_path.resolve()
    if source_path == target_path:
        raise ValueError("source and destination must be different databases")
    if not source_path.is_file() or not target_path.is_file():
        raise ValueError("both databases must already exist")
    # The operator must supply the online-backup snapshot, not a WAL-backed
    # live source whose bytes could change during the archive phase.
    wal = Path(str(source_path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("use a closed, self-contained v3 backup snapshot")
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    # A backup may have harmless empty WAL/SHM sidecars from an earlier
    # read-only inspection. Immutable mode neither reads those nor creates new
    # ones; a nonempty WAL was refused above instead of losing its commits.
    source_uri = f"file:{quote(str(source_path))}?mode=ro&immutable=1"
    source = sqlite3.connect(source_uri, uri=True)
    target = sqlite3.connect(target_path, uri=True)
    try:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("v3 snapshot integrity check failed")
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"providers", "credentials", "usage_records", "upstream_records", "downstream_records", "audit_events", "capture_links", "credential_cycles", "credential_quota_cycles", "credential_blocks"}
        actual = {r[0] for r in target.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= actual or "usage_rows" in actual:
            raise ValueError("destination is not an initialized v4 database")
        marker = ARCHIVE + "manifest"
        if marker in actual:
            stored = target.execute(f"SELECT source_sha256,report_json FROM {ident(marker)}").fetchone()
            if not stored or stored[0] != digest:
                raise ValueError("destination already contains a different history snapshot")
            return {**json.loads(stored[1]), "already_imported": True}
        # Ensure this is the SAME snapshot imported by the native migration.
        for table in ("providers", "credentials"):
            for (old_id,) in source.execute(f"SELECT id FROM {ident(table)}"):
                if not target.execute(f"SELECT 1 FROM {ident(table)} WHERE id=?", (v3_id(table, old_id),)).fetchone():
                    raise ValueError(f"native configuration import is missing {table} id {old_id}")
        if "usage_rows" in tables:
            for (old_id,) in source.execute("SELECT id FROM usage_rows"):
                if not target.execute("SELECT 1 FROM usage_records WHERE request_id=?", (v3_id("usage_rows", old_id),)).fetchone():
                    raise ValueError(f"native usage import is missing historical row {old_id}")
        target.execute("BEGIN IMMEDIATE")
        report = {"source_sha256": digest, "archived_tables": archive(target, source_uri)}
        report.update(captures(source, target, tables))
        report["audit_events"] = audit(source, target, tables)
        report.update(quota_history(source, target, tables))
        target.execute(f"CREATE TABLE {ident(marker)} (source_sha256 TEXT PRIMARY KEY, report_json TEXT NOT NULL)")
        target.execute(f"INSERT INTO {ident(marker)} VALUES (?,?)", (digest, json_text(report)))
        target.commit()
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("v4 history integrity check failed")
        if target.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("v4 history foreign-key check failed")
        return report
    except BaseException:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.target), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
