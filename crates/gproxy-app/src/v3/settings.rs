//! v3's instance settings as a v4 settings patch.
//!
//! Only the database route has them: v3's export never carried its
//! `settings` table. They are applied as a **patch** after the import rather
//! than as the document's `settings`, because v4's settings document is a
//! complete row and v3 has nothing to say about most of it (quota observation
//! retention, tracing, the tokenizer's vocabulary file); a patch changes what
//! v3 set and leaves the destination's own values for the rest.
//!
//! Renamed on the way: `update_channel` (`releases` → `release`, `staging` →
//! `beta`) and `traffic_blacklist`, whose three lists are three v4 fields.
//! Reported: the settings v4 has no field for.

use std::collections::BTreeMap;

use gproxy_sdk::dto::{InstanceSettingsPatch, LoggingSettingsPatch, SettingsPatch};
use serde_json::{Value, json};

use super::Report;

/// v3 settings v4 has no place for, with what they controlled.
const UNMAPPED: [(&str, &str); 3] = [
    ("inherit_system_proxy", "v4 uses only an explicit proxy"),
    (
        "file_upload_max_in_flight",
        "v4 has no upload concurrency limit",
    ),
    (
        "max_in_flight",
        "v4 has no gateway-wide request concurrency limit",
    ),
];

pub fn patch(settings: &BTreeMap<String, Value>, report: &mut Report) -> Option<SettingsPatch> {
    let get = |key: &str| settings.get(key).filter(|value| !value.is_null());
    let flag = |key: &str| get(key).and_then(Value::as_bool);
    let text = |key: &str| {
        get(key)
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|text| !text.is_empty())
            .map(str::to_owned)
    };
    let count = |key: &str| get(key).and_then(Value::as_u64);
    let list = |key: &str| get(key).filter(|value| value.is_array()).cloned();
    // A stored null is an explicit unlimited policy, not an absent setting.
    // In particular, do not replace v3's unlimited history with v4's new
    // seven-day/two-GiB payload defaults during an upgrade.
    let nullable_count = |key: &str| {
        settings.get(key).and_then(|value| {
            if value.is_null() {
                Some(None)
            } else {
                value.as_u64().map(Some)
            }
        })
    };
    let retention = nullable_count("retention_days")
        .and_then(|value| value.map(u32::try_from).transpose().ok());

    let update_channel = text("update_channel").and_then(|channel| {
        match channel.to_ascii_lowercase().as_str() {
            "releases" | "release" | "stable" => Some("release".to_owned()),
            "staging" => Some("beta".to_owned()),
            "dev" | "development" => Some("dev".to_owned()),
            other => {
                report.warn(format!(
                    "setting update_channel `{other}` is not a v4 channel; the destination's was kept"
                ));
                None
            }
        }
    });
    let instance = InstanceSettingsPatch {
        instance_name: text("instance_name"),
        proxy: text("proxy").map(|url| Some(json!({"mode": "explicit", "url": url}))),
        cors_origins: list("cors_origins"),
        trusted_proxies: list("trusted_proxies"),
        max_attempts: count("max_attempts")
            .and_then(|n| u32::try_from(n).ok())
            .filter(|n| *n > 0),
        enable_usage: flag("enable_usage"),
        enable_tokenizer_vocabs: flag("enable_tokenizer_vocabs"),
        enable_tokenizer_download: flag("enable_tokenizer_download"),
        retention_days: retention,
        capture_payload_retention_days: retention,
        quota_observation_retention_days: retention,
        // v3 had only the whole-database budget, retained below. Adding a
        // second, independent payload budget would delete previously kept
        // bodies even when the database remains within its original budget.
        capture_payload_max_mb: settings
            .contains_key("max_database_size_mb")
            .then_some(None),
        max_database_size_mb: nullable_count("max_database_size_mb")
            .and_then(|value| value.map(i64::try_from).transpose().ok()),
        update_channel: update_channel.map(Some),
        enable_auto_update_check: flag("enable_auto_update_check"),
        ..InstanceSettingsPatch::default()
    };
    let blacklist = get("traffic_blacklist");
    let blacklist_list = |key: &str| {
        blacklist
            .and_then(|value| value.get(key))
            .filter(|value| value.is_array())
            .cloned()
    };
    let logging = LoggingSettingsPatch {
        enable_downstream_log: flag("enable_downstream_log"),
        enable_downstream_log_body: flag("enable_downstream_log_body"),
        enable_upstream_log: flag("enable_upstream_log"),
        enable_upstream_log_body: flag("enable_upstream_log_body"),
        disable_log_redaction: flag("disable_log_redaction"),
        log_level: text("log_level"),
        log_format: text("log_format"),
        request_header_blacklist: blacklist_list("request_headers"),
        response_header_blacklist: blacklist_list("response_headers"),
        query_parameter_blacklist: blacklist_list("request_query"),
        ..LoggingSettingsPatch::default()
    };
    for (key, why) in UNMAPPED {
        if let Some(value) = get(key).filter(|value| !is_default(key, value)) {
            report.drop_row("settings", format!("{key} = {value}"), why);
        }
    }
    // Option<Option<T>> serializes both absent and explicit-null as null.
    // Do not let the JSON equality check erase a null-only policy update.
    let has_nullable_edits = instance.retention_days.is_some()
        || instance.capture_payload_retention_days.is_some()
        || instance.capture_payload_max_mb.is_some()
        || instance.quota_observation_retention_days.is_some()
        || instance.max_database_size_mb.is_some();
    let empty = !has_nullable_edits
        && serde_json::to_value(&instance).ok()
            == serde_json::to_value(InstanceSettingsPatch::default()).ok()
        && serde_json::to_value(&logging).ok()
            == serde_json::to_value(LoggingSettingsPatch::default()).ok();
    (!empty).then_some(SettingsPatch {
        instance: Some(instance),
        logging: Some(logging),
    })
}

/// A value v3 wrote for a setting nobody changed: v3 saved every runtime
/// setting together, defaults included.
fn is_default(key: &str, value: &Value) -> bool {
    match key {
        "max_in_flight" => value.as_u64() == Some(1024),
        _ => matches!(value, Value::Bool(false)) || value.as_u64() == Some(0),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explicit_unlimited_history_does_not_inherit_new_retention_defaults() {
        let settings = serde_json::from_value(json!({
            "retention_days": null, "max_database_size_mb": null,
        }))
        .unwrap();
        let instance = patch(&settings, &mut Report::default())
            .unwrap()
            .instance
            .unwrap();
        assert_eq!(instance.retention_days, Some(None));
        assert_eq!(instance.capture_payload_retention_days, Some(None));
        assert_eq!(instance.quota_observation_retention_days, Some(None));
        assert_eq!(instance.capture_payload_max_mb, Some(None));
        assert_eq!(instance.max_database_size_mb, Some(None));
    }

    #[test]
    fn v3_settings_become_a_patch_under_v4s_names() {
        let settings: BTreeMap<String, Value> = serde_json::from_value(json!({
            "instance_name": "prod", "enable_upstream_log": true, "retention_days": 7,
            "update_channel": "staging", "proxy": "http://p:1", "max_attempts": 3,
            "traffic_blacklist": {"request_headers": ["x-a"], "response_headers": [], "request_query": ["k"]},
            "inherit_system_proxy": true, "file_upload_max_in_flight": 0, "max_in_flight": 1024,
            "master_key_fingerprint": "f"
        }))
        .unwrap();
        let mut report = Report::default();
        let patch = patch(&settings, &mut report).unwrap();
        let instance = patch.instance.unwrap();
        assert_eq!(instance.instance_name.as_deref(), Some("prod"));
        assert_eq!(instance.update_channel, Some(Some("beta".into())));
        assert_eq!(instance.retention_days, Some(Some(7)));
        assert_eq!(instance.max_attempts, Some(3));
        assert_eq!(
            instance.proxy,
            Some(Some(json!({"mode": "explicit", "url": "http://p:1"})))
        );
        let logging = patch.logging.unwrap();
        assert_eq!(logging.enable_upstream_log, Some(true));
        assert_eq!(
            logging.enable_downstream_log, None,
            "unset stays the destination's"
        );
        assert_eq!(logging.query_parameter_blacklist, Some(json!(["k"])));
        assert_eq!(
            report.dropped.len(),
            1,
            "only a changed unmapped setting is reported"
        );
        assert!(super::patch(&BTreeMap::new(), &mut report).is_none());
    }
}
