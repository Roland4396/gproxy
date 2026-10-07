//! Narrow v3 wire compatibility for existing private management clients.
//! This translates representation only. Native authentication, same-origin,
//! tenant scoping, secret disclosure and audit middleware still execute.
use std::collections::{BTreeMap, BTreeSet};

use axum::{
    body::{Body, to_bytes},
    extract::Request,
    middleware::Next,
    response::{IntoResponse, Response},
};
use http::{HeaderValue, Method, StatusCode, header};
use serde_json::{Value, json};

use crate::{MAX_BODY_BYTES, SIGN_IN_BODY_BYTES, session};

const CLIENT_COOKIE: &str = "gproxy_v3_client";

#[derive(Clone, Copy, PartialEq, Eq)]
enum Projection {
    Native,
    Collection,
    Reveal,
    Probe,
    Usage,
}

fn numeric_id(value: &str) -> bool {
    !value.is_empty() && value.bytes().all(|b| b.is_ascii_digit())
}

fn old_id(value: &Value) -> Value {
    let Some(text) = value.as_str() else {
        return value.clone();
    };
    for prefix in ["v3-credentials-", "v3-providers-"] {
        if let Some(id) = text
            .strip_prefix(prefix)
            .and_then(|s| s.parse::<u64>().ok())
        {
            return json!(id);
        }
    }
    value.clone()
}

fn snake(name: &str) -> String {
    let mut result = String::new();
    for c in name.chars() {
        if c.is_ascii_uppercase() {
            result.push('_');
            result.push(c.to_ascii_lowercase());
        } else {
            result.push(c);
        }
    }
    result
}

fn old_row(row: &Value) -> Value {
    let Some(object) = row.as_object() else {
        return row.clone();
    };
    Value::Object(
        object
            .iter()
            .map(|(key, value)| {
                let value = if matches!(key.as_str(), "id" | "providerId") {
                    old_id(value)
                } else {
                    value.clone()
                };
                // Opaque config/metadata values are never recursively rewritten.
                (snake(key), value)
            })
            .collect(),
    )
}

fn seconds(value: &Value) -> Value {
    value
        .as_i64()
        .map(|n| json!(n / 1000))
        .unwrap_or(Value::Null)
}

fn old_source(id: &Value, source: &Value) -> Value {
    // v4 gives Antigravity each bucket its own source id; the existing
    // private scheduler groups all four under v3's subscription capability.
    if id == source
        && matches!(
            id.as_str(),
            Some("gemini-5h" | "gemini-weekly" | "3p-5h" | "3p-weekly")
        )
    {
        json!("subscription")
    } else {
        source.clone()
    }
}

/// Native DTO entry -> the old tagged `value` and second-based boundaries.
fn dto_entry(entry: &Value) -> Value {
    let allowance = &entry["allowance"];
    let mut value = json!({"kind":entry["kind"]});
    if let Some(object) = allowance.as_object() {
        for (key, v) in object {
            let (key, v) = match key.as_str() {
                "periodStartMs" => ("period_start".to_owned(), seconds(v)),
                "periodEndMs" => ("period_end".to_owned(), seconds(v)),
                _ => (snake(key), v.clone()),
            };
            value[&key] = v;
        }
    } else if entry["kind"] == "breakdown" {
        value["rows"] = entry["breakdown"].clone();
    } else if entry["kind"] == "balance" {
        value["remaining"] = entry["balance"]["remaining"].clone();
        value["unit"] = entry["balance"]["unit"].clone();
    }
    json!({"id":entry["id"],"source_id":old_source(&entry["id"], &entry["sourceId"]),
        "label":entry["label"],"subject":entry["subject"],
        "model_scope":entry["modelScope"],"value":value})
}

fn sources(entries: &[Value], observed: i64) -> Vec<Value> {
    entries
        .iter()
        .filter_map(|e| e["source_id"].as_str())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .map(|id| {
            json!({"capability":{"id":id},
            "observed_at_ms":observed,"error":null})
        })
        .collect()
}

fn old_snapshot(snapshot: &Value) -> Value {
    let entries: Vec<_> = snapshot["entries"]
        .as_array()
        .into_iter()
        .flatten()
        .map(dto_entry)
        .collect();
    json!({"sources":sources(&entries, snapshot["observedAtMs"].as_i64().unwrap_or(0)),
        "entries":entries})
}

/// The latest real observation of each entry; never infer a reset/start or
/// freshness from local wall clock. The caller has already scoped these rows.
pub(crate) fn cached_snapshot(rows: &[gproxy_sdk::dto::QuotaObservationDto]) -> Value {
    let mut entries = BTreeMap::new();
    let mut observations: BTreeMap<String, i64> = BTreeMap::new();
    for row in rows {
        let s = &row.snapshot;
        let Some(id) = s["id"].as_str() else { continue };
        let source_value = old_source(&s["id"], &s["source_id"]);
        let Some(source) = source_value.as_str() else {
            continue;
        };
        let key = (source.to_owned(), id.to_owned());
        if entries.contains_key(&key) {
            continue;
        }
        let mut value = json!({"kind":s["kind"]});
        for k in [
            "used",
            "limit",
            "remaining",
            "used_percent",
            "unlimited",
            "unit",
            "reset_behavior",
        ] {
            value[k] = s[k].clone();
        }
        value["period_start"] = seconds(&s["period_start_ms"]);
        value["period_end"] = seconds(&s["period_end_ms"]);
        if s["kind"] == "breakdown" {
            value["rows"] = s["breakdown"].clone();
        }
        entries.insert(
            key,
            json!({"id":id,"source_id":source,"label":s["label"],
            "subject":s["subject"],"model_scope":row.scope,"value":value}),
        );
        observations
            .entry(source.to_owned())
            .and_modify(|n| *n = (*n).max(row.observed_at_ms))
            .or_insert(row.observed_at_ms);
    }
    json!({"entries":entries.into_values().collect::<Vec<_>>(),
        "sources":observations.into_iter().map(|(id,at)| json!({
            "capability":{"id":id},"observed_at_ms":at,"error":null})).collect::<Vec<_>>()})
}

fn project(kind: Projection, value: Value) -> Result<Value, &'static str> {
    Ok(match kind {
        Projection::Native => value,
        Projection::Collection => {
            let items = value["items"].as_array().ok_or("invalid native page")?;
            if value["total"].as_u64().unwrap_or(0) > items.len() as u64 {
                return Err("v3 client list exceeds compatibility page; use paged v4 API");
            }
            Value::Array(items.iter().map(old_row).collect())
        }
        Projection::Reveal => json!({"secret":value}),
        Projection::Probe => json!({
            "snapshot":if value["snapshot"].is_object() {old_snapshot(&value["snapshot"])} else {json!({"sources":[],"entries":[]})},
            // Actual redacted upstream evidence, not a fabricated success flag.
            "raw":value["responses"],"error":value["error"]}),
        Projection::Usage => {
            let windows:Vec<_> = value["entries"].as_array().into_iter().flatten()
                .filter(|e| e["value"]["kind"]=="window")
                .map(|e| {
                    let reset=e["value"]["period_end"].as_i64()
                        .and_then(|s| time::OffsetDateTime::from_unix_timestamp(s).ok())
                        .and_then(|t| t.format(&time::format_description::well_known::Rfc3339).ok());
                    json!({"name":e["id"],"used_percent":e["value"]["used_percent"],"resets_at":reset})
                }).collect();
            json!({"windows":windows})
        }
    })
}

fn failure(status: StatusCode, message: &str) -> Response {
    (
        status,
        axum::Json(json!({"error":{"code":"legacy_compatibility","message":message}})),
    )
        .into_response()
}

pub(crate) async fn compat(mut request: Request, next: Next) -> Response {
    let original = request.uri().path().to_owned();
    // Fast no-buffer path for inference, streaming, sockets and native UI.
    if !original.starts_with("/admin/") {
        return next.run(request).await;
    }
    let login = matches!(original.as_str(), "/admin/api/login" | "/admin/login")
        && request.method() == Method::POST;
    let legacy = session::cookie(request.headers(), CLIENT_COOKIE) == Some("1");
    let mut path = original.clone();
    let mut projection = Projection::Native;
    let mut query: Vec<(String, String)> =
        serde_urlencoded::from_str(request.uri().query().unwrap_or("")).unwrap_or_default();
    if login {
        let (mut parts, body) = request.into_parts();
        let bytes = match to_bytes(body, SIGN_IN_BODY_BYTES).await {
            Ok(bytes) => bytes,
            Err(_) => return failure(StatusCode::PAYLOAD_TOO_LARGE, "login body exceeds limit"),
        };
        let mut value: Value = match serde_json::from_slice(&bytes) {
            Ok(value) => value,
            Err(_) => return failure(StatusCode::BAD_REQUEST, "invalid login JSON"),
        };
        if value.get("name").is_none() {
            value["name"] = value["username"].clone();
        }
        let bytes = serde_json::to_vec(&value).expect("JSON value serializes");
        parts.headers.remove(header::CONTENT_LENGTH);
        request = Request::from_parts(parts, Body::from(bytes));
        path = "/portal/api/login".into();
    } else {
        let normalized = original
            .strip_prefix("/admin/api/")
            .or_else(|| original.strip_prefix("/admin/"));
        let segments: Vec<_> = normalized.unwrap_or("").split('/').collect();
        match segments.as_slice() {
            [family]
                if legacy
                    && request.method() == Method::GET
                    && matches!(*family, "providers" | "credentials") =>
            {
                path = format!("/admin/api/{family}");
                projection = Projection::Collection;
            }
            ["providers", id, "credentials"]
                if numeric_id(id) && request.method() == Method::GET =>
            {
                path = "/admin/api/credentials".into();
                projection = Projection::Collection;
                query.push(("providerId".into(), format!("v3-providers-{id}")));
            }
            [family, id, rest @ ..]
                if numeric_id(id) && matches!(*family, "credentials" | "providers") =>
            {
                path = format!("/admin/api/{family}/v3-{family}-{id}");
                if *family == "credentials" {
                    match rest {
                        ["quota"] if request.method() == Method::GET => path.push_str("/v3-quota"),
                        ["usage"] if request.method() == Method::GET => {
                            path.push_str("/v3-quota");
                            projection = Projection::Usage;
                        }
                        ["quota-probe"] if request.method() == Method::POST => {
                            path.push_str("/quota-diagnostics");
                            projection = Projection::Probe;
                        }
                        ["reveal"] if request.method() == Method::POST => {
                            path.push_str("/reveal");
                            projection = Projection::Reveal;
                        }
                        _ => {
                            for segment in rest {
                                path.push('/');
                                path.push_str(segment);
                            }
                        }
                    }
                } else {
                    for segment in rest {
                        path.push('/');
                        path.push_str(segment);
                    }
                }
            }
            _ => {}
        }
    }
    if projection == Projection::Collection {
        query.retain(|(key, _)| !matches!(key.as_str(), "page" | "pageSize" | "page_size"));
        query.push(("pageSize".into(), "500".into()));
    }
    if path != original || projection != Projection::Native {
        let query = serde_urlencoded::to_string(query).expect("string query serializes");
        *request.uri_mut() = format!(
            "{path}{}",
            if query.is_empty() {
                String::new()
            } else {
                format!("?{query}")
            }
        )
        .parse()
        .expect("fixed translated URI");
    }
    let mut response = next.run(request).await;
    if !response.status().is_success() {
        return response;
    }
    if login {
        response.headers_mut().append(
            header::SET_COOKIE,
            HeaderValue::from_static(
                "gproxy_v3_client=1; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400",
            ),
        );
    }
    if projection == Projection::Native {
        return response;
    }
    let (mut parts, body) = response.into_parts();
    let bytes = match to_bytes(body, MAX_BODY_BYTES).await {
        Ok(bytes) => bytes,
        Err(_) => {
            return failure(
                StatusCode::BAD_GATEWAY,
                "native compatibility response exceeds limit",
            );
        }
    };
    let value = match serde_json::from_slice(&bytes)
        .ok()
        .and_then(|v| project(projection, v).ok())
    {
        Some(value) => value,
        None => {
            return failure(
                StatusCode::BAD_GATEWAY,
                "native compatibility response could not be projected",
            );
        }
    };
    parts.headers.remove(header::CONTENT_LENGTH);
    parts.headers.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/json"),
    );
    parts
        .headers
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    Response::from_parts(
        parts,
        Body::from(serde_json::to_vec(&value).expect("JSON value serializes")),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn imported_ids_and_top_level_fields_only() {
        let row = old_row(
            &json!({"id":"v3-credentials-30","providerId":"v3-providers-1",
            "enabled":true,"metadata":{"accessTokenKeyName":"opaque"}}),
        );
        assert_eq!(row["id"], 30);
        assert_eq!(row["provider_id"], 1);
        assert_eq!(row["metadata"]["accessTokenKeyName"], "opaque");
    }

    #[test]
    fn quota_projection_preserves_paused_zero_and_missing_boundaries() {
        let e = dto_entry(
            &json!({"id":"3p-5h","sourceId":"subscription","kind":"window",
            "label":"antigravity_disabled","modelScope":"all","allowance":{
                "usedPercent":"0","periodStartMs":null,"periodEndMs":1791411621000_i64}}),
        );
        assert_eq!(e["value"]["used_percent"], "0");
        assert_eq!(e["value"]["period_end"], 1791411621_i64);
        assert!(e["value"]["period_start"].is_null());
        assert_eq!(e["label"], "antigravity_disabled");
    }

    #[test]
    fn diagnostics_never_invents_success_evidence() {
        let result = project(
            Projection::Probe,
            json!({"snapshot":null,"responses":[],"error":"failed"}),
        )
        .unwrap();
        assert_eq!(result["raw"], json!([]));
        assert_eq!(result["snapshot"]["sources"], json!([]));
    }

    #[test]
    fn fresh_v4_antigravity_sources_keep_the_private_subscription_contract() {
        let result = old_snapshot(&json!({"observedAtMs":1791390000000_i64,"entries":[
            {"id":"3p-weekly","sourceId":"3p-weekly","kind":"window","allowance":{"usedPercent":"100"}},
            {"id":"3p-5h","sourceId":"3p-5h","kind":"window","label":"antigravity_disabled","allowance":{"usedPercent":null}}
        ]}));
        assert_eq!(result["sources"].as_array().unwrap().len(), 1);
        assert_eq!(result["sources"][0]["capability"]["id"], "subscription");
        assert_eq!(result["sources"][0]["observed_at_ms"], 1791390000000_i64);
        assert_eq!(result["entries"][0]["source_id"], "subscription");
        assert!(result["entries"][1]["value"]["used_percent"].is_null());
    }

    #[test]
    fn an_incomplete_list_fails_instead_of_silently_losing_accounts() {
        assert!(project(Projection::Collection, json!({"items":[],"total":1})).is_err());
    }
}
