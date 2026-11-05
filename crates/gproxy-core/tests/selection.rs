#![cfg(not(target_arch = "wasm32"))]
mod support;
use support::*;

use gproxy_channel::channel::{
    QuotaAllowance, QuotaEntry, QuotaScope, QuotaSnapshot, QuotaSubject, QuotaValue,
};
use gproxy_core::{RequestContext, keys};
use gproxy_store::entity::{
    config::setting,
    upstream::{credential, provider},
};
use http::StatusCode;
use sea_orm::Set;
use serde_json::json;
use std::sync::Arc;

fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

async fn configured(strategy: &str) -> Harness {
    let h = harness(full(), strategy).await;
    for id in ["a", "b"] {
        h.core
            .store()
            .credentials()
            .update_many(vec![credential::ActiveModel {
                id: Set(id.into()),
                metadata: Set(json!({"quota":[
                    {"id":"5h", "window_seconds":18000, "tracking":"reported"},
                    {"id":"1w", "window_seconds":604800, "tracking":"reported"}
                ]})),
                ..Default::default()
            }])
            .await
            .unwrap();
    }
    h.core
        .store()
        .settings()
        .update(setting::ActiveModel {
            config_revision: Set(h.core.snapshot().revision.0 as i64 + 1),
            ..Default::default()
        })
        .await
        .unwrap();
    h.core.reload_data().await.unwrap();
    h
}

async fn observe(h: &Harness, credential: &str, entries: &[(&str, Option<i64>, i64)]) {
    h.channel
        .quota_snapshots
        .lock()
        .unwrap()
        .push_back(QuotaSnapshot {
            observed_at_ms: now(),
            entries: entries
                .iter()
                .map(|(id, end, remaining)| QuotaEntry {
                    id: (*id).into(),
                    source_id: (*id).into(),
                    label: None,
                    subject: QuotaSubject::Account,
                    model_scope: QuotaScope::All,
                    value: QuotaValue::Window(QuotaAllowance {
                        remaining: Some((*remaining).into()),
                        period_end_ms: *end,
                        ..Default::default()
                    }),
                })
                .collect(),
        });
    h.core
        .query_credential_quota("p", credential)
        .await
        .unwrap();
}

async fn run(h: &Harness, session: Option<&str>, allowed: Option<&str>) -> String {
    let mut context: RequestContext = (*h.context("r", 2, session)).clone();
    if let Some(id) = allowed {
        context.target.credentials.retain(|c| c.id == id);
    }
    run_context(h, context).await
}

async fn run_context(h: &Harness, context: RequestContext) -> String {
    h.script(vec![json_reply(StatusCode::OK, json!({"ok":true}))]);
    let execution = h
        .core
        .stream_generate_content(Arc::new(context), request("{}"))
        .await
        .unwrap();
    let (response, completion) = execution.into_parts();
    read(response.body).await;
    completion.await.unwrap();
    let line = h.client.seen.lines().pop().unwrap();
    if line.contains("auth=Bearer ka") {
        "a".into()
    } else {
        assert!(line.contains("auth=Bearer kb"), "{line}");
        "b".into()
    }
}

async fn bind_models(h: &Harness, bindings: serde_json::Value) {
    let mut config = h.core.snapshot().providers["p"].entity.config.clone();
    config["model_credentials"] = bindings;
    h.core
        .store()
        .providers()
        .update_many(vec![provider::ActiveModel {
            id: Set("p".into()),
            config: Set(config),
            ..Default::default()
        }])
        .await
        .unwrap();
    h.core
        .store()
        .settings()
        .update(setting::ActiveModel {
            config_revision: Set(h.core.snapshot().revision.0 as i64 + 1),
            ..Default::default()
        })
        .await
        .unwrap();
    h.core.reload_data().await.unwrap();
}

#[tokio::test]
async fn model_binding_is_exact_and_unconfigured_models_keep_their_pool() {
    let h = configured("round_robin").await;
    bind_models(&h, json!({"gpt-x": ["b"]})).await;
    for _ in 0..3 {
        assert_eq!(run(&h, None, None).await, "b");
    }
    // Prefixes, aliases and requests without a model are not silently bound.
    for model in [Some("gpt-x-high"), Some("different"), None] {
        let mut context = (*h.context("other", 2, None)).clone();
        context.target.upstream_model = model.map(str::to_owned);
        assert_eq!(run_context(&h, context.clone()).await, "a");
        assert_eq!(run_context(&h, context).await, "b");
    }
}

#[tokio::test]
async fn model_binding_overrides_an_old_affinity_pin_and_reset_preference() {
    for strategy in ["earliest_reset", "round_robin_affinity"] {
        let h = configured(strategy).await;
        let time = now();
        observe(&h, "a", &[("5h", Some(time + 300_000), 10)]).await;
        observe(&h, "b", &[("5h", Some(time + 600_000), 10)]).await;
        assert_eq!(run(&h, Some("same-session"), None).await, "a");
        bind_models(&h, json!({"gpt-x": ["b"]})).await;
        assert_eq!(run(&h, Some("same-session"), None).await, "b");
    }
}

#[tokio::test]
async fn model_binding_never_widens_admission_and_unavailable_targets_fail_closed() {
    use gproxy_core::CoreError;
    for mode in ["not-admitted", "disabled", "blocked", "missing", "empty"] {
        let h = configured("round_robin").await;
        let bindings = match mode {
            "missing" => json!({"gpt-x": ["not-present"]}),
            "empty" => json!({"gpt-x": []}),
            _ => json!({"gpt-x": ["b"]}),
        };
        bind_models(&h, bindings).await;
        if mode == "blocked" {
            observe(&h, "b", &[("5h", Some(now() + 600_000), 0)]).await;
        }
        let mut context = (*h.context("denied", 3, Some("s"))).clone();
        if mode == "not-admitted" {
            context.target.credentials.retain(|c| c.id == "a");
        }
        if mode == "disabled" {
            let bound = context
                .target
                .credentials
                .iter_mut()
                .find(|c| c.id == "b")
                .unwrap();
            Arc::get_mut(bound).unwrap().enabled = false;
        }
        let error = h
            .core
            .stream_generate_content(Arc::new(context), request("{}"))
            .await
            .unwrap_err();
        assert!(
            matches!(error, CoreError::NoUsableCredential { .. }),
            "{mode}: {error}"
        );
        assert!(
            h.client.seen.lines().is_empty(),
            "{mode}: no upstream call allowed"
        );
    }
}

#[tokio::test]
async fn model_binding_does_not_retry_another_account_after_failure() {
    use gproxy_core::CoreError;
    for (status, expected_calls) in [
        (StatusCode::SERVICE_UNAVAILABLE, 3),
        (StatusCode::TOO_MANY_REQUESTS, 1),
    ] {
        let h = configured("round_robin").await;
        bind_models(&h, json!({"gpt-x": ["b"]})).await;
        // Upstream now excludes a refusing credential for the remainder of
        // one request. Three separately issued 503 requests still reach the
        // retained failure threshold; 429 blocks on the first request.
        for index in 0..expected_calls {
            h.script(vec![json_reply(status, json!({"error":"fixture"}))]);
            let request_id = format!("retry-{index}");
            let execution = h
                .core
                .stream_generate_content(h.context(&request_id, 3, None), request("{}"))
                .await
                .unwrap();
            let (response, completion) = execution.into_parts();
            assert_eq!(response.status, status);
            read(response.body).await;
            completion.await.unwrap();
            let calls = h.client.seen.lines();
            assert_eq!(calls.len(), index + 1);
            assert!(calls.iter().all(|line| line.contains("auth=Bearer kb")));
        }
        // Once the sole bound credential is blocked, a fresh request also
        // fails closed rather than escaping to the otherwise healthy a.
        let error = h
            .core
            .stream_generate_content(h.context("blocked", 3, None), request("{}"))
            .await
            .unwrap_err();
        assert!(
            matches!(error, CoreError::NoUsableCredential { .. }),
            "{error}"
        );
        assert_eq!(h.client.seen.lines().len(), expected_calls);
    }
}

#[tokio::test]
async fn earliest_reset_compares_actual_times_across_windows_and_stays_in_allowed_pool() {
    let h = configured("earliest_reset").await;
    let time = now();
    observe(
        &h,
        "a",
        &[
            ("5h", Some(time + 7_200_000), 10),
            ("1w", Some(time + 600_000), 10),
        ],
    )
    .await;
    observe(&h, "b", &[("5h", Some(time + 3_600_000), 80)]).await;
    assert_eq!(run(&h, None, None).await, "a");
    assert_eq!(run(&h, None, None).await, "a");
    assert_eq!(run(&h, None, Some("b")).await, "b");
    // New observed exhaustion excludes a even though its reset is sooner.
    observe(&h, "a", &[("1w", Some(time + 600_000), 0)]).await;
    assert_eq!(run(&h, None, None).await, "b");
}

#[tokio::test]
async fn earliest_reset_reconsiders_the_order_even_in_the_same_session() {
    let h = configured("earliest_reset").await;
    let time = now();
    observe(&h, "a", &[("5h", Some(time + 3_600_000), 10)]).await;
    observe(&h, "b", &[("5h", Some(time + 600_000), 10)]).await;
    assert_eq!(run(&h, Some("s"), None).await, "b");
    observe(&h, "a", &[("1w", Some(time + 300_000), 10)]).await;
    assert_eq!(run(&h, Some("s"), None).await, "a");
    assert_eq!(run(&h, Some("new"), None).await, "a");
}

#[tokio::test]
async fn tied_and_unknown_resets_rotate_and_expired_observations_do_not_win() {
    let h = configured("earliest_reset").await;
    assert_eq!(run(&h, None, None).await, "a");
    assert_eq!(run(&h, None, None).await, "b");
    let time = now();
    observe(&h, "a", &[("5h", Some(time - 1), 10)]).await;
    observe(&h, "b", &[("5h", Some(time + 600_000), 10)]).await;
    assert_eq!(run(&h, None, None).await, "b");
    observe(&h, "a", &[("1w", Some(time + 600_000), 10)]).await;
    assert_eq!(run(&h, None, None).await, "a");
    assert_eq!(run(&h, None, None).await, "b");
}

#[tokio::test]
async fn newer_unknown_reset_does_not_resurrect_an_old_reading() {
    let h = configured("earliest_reset").await;
    let time = now();
    observe(&h, "a", &[("5h", Some(time + 300_000), 10)]).await;
    observe(&h, "b", &[("5h", Some(time + 600_000), 10)]).await;
    assert_eq!(run(&h, None, None).await, "a");
    // Ensure a strictly later observation even with a millisecond clock.
    tokio::time::sleep(std::time::Duration::from_millis(2)).await;
    observe(&h, "a", &[("5h", None, 10)]).await;
    assert_eq!(run(&h, None, None).await, "b");
    // A cache miss (as on another instance/restart) gives the same answer.
    h.core
        .cache()
        .delete(&keys::credential_reset_observations("a"))
        .await
        .unwrap();
    assert_eq!(run(&h, None, None).await, "b");
}

#[tokio::test]
async fn sticky_is_deterministic_while_round_robin_affinity_follows_arrival_order() {
    for strategy in ["sticky", "round_robin_affinity"] {
        let h = harness(full(), strategy).await;
        let first = run(&h, Some("session-a"), None).await;
        let second = run(&h, Some("session-b"), None).await;
        assert_ne!(first, second);
        assert_eq!(run(&h, Some("session-a"), None).await, first);
        assert_eq!(run(&h, Some("session-b"), None).await, second);

        // A fresh cache and reversed arrival order must not change sticky's map.
        let fresh = harness(full(), strategy).await;
        let other_second = run(&fresh, Some("session-b"), None).await;
        let other_first = run(&fresh, Some("session-a"), None).await;
        if strategy == "sticky" {
            assert_eq!((other_first, other_second), (first.clone(), second));
        } else {
            assert_eq!((&first[..], &second[..]), ("a", "b"));
            assert_eq!((&other_second[..], &other_first[..]), ("a", "b"));
        }
        let mut reversed = (*h.context("reversed", 2, Some("session-a"))).clone();
        reversed.target.credentials.reverse();
        assert_eq!(run_context(&h, reversed).await, first);

        // Both respect admission; only round-robin affinity remembers a reassignment.
        let replacement = if first == "a" { "b" } else { "a" };
        assert_eq!(
            run(&h, Some("session-a"), Some(replacement)).await,
            replacement
        );
        assert_eq!(
            run(&h, Some("session-a"), None).await,
            if strategy == "sticky" {
                first.as_str()
            } else {
                replacement
            }
        );
    }
}

#[tokio::test]
async fn affinity_strategies_rotate_without_a_stable_session() {
    for strategy in ["sticky", "round_robin_affinity"] {
        let h = harness(full(), strategy).await;
        assert_eq!(run(&h, None, None).await, "a");
        assert_eq!(run(&h, None, None).await, "b");
        let mut context = (*h.context("fallback", 2, Some("same-request-id"))).clone();
        context.session.as_mut().unwrap().source = gproxy_core::SessionSource::RequestFallback;
        assert_eq!(run_context(&h, context.clone()).await, "a");
        assert_eq!(run_context(&h, context).await, "b");
    }
    let h = harness(full(), "round_robin").await;
    assert_eq!(run(&h, Some("s"), None).await, "a");
    assert_eq!(run(&h, Some("s"), None).await, "b");
}

#[tokio::test]
async fn reset_order_respects_dimension_model_and_operation_scopes() {
    use gproxy_channel::channel::QuotaWindow;
    use gproxy_protocol::Operation;
    let h = configured("earliest_reset").await;
    let time = now();
    observe(&h, "a", &[("5h", Some(time + 300_000), 10)]).await;
    observe(&h, "b", &[("5h", Some(time + 600_000), 10)]).await;
    for mode in ["model", "operation", "total", "no_model", "observed_scope"] {
        let mut ctx = (*h.context("r", 2, None)).clone();
        let a = ctx
            .target
            .credentials
            .iter_mut()
            .find(|c| c.id == "a")
            .unwrap();
        let dimension = &mut Arc::get_mut(a).unwrap().quota[0];
        match mode {
            "model" => dimension.scope = QuotaScope::Models(vec!["other-model".into()]),
            "operation" => dimension.operations = Some(vec![Operation::CountTokens]),
            "total" => dimension.window = QuotaWindow::Total,
            "no_model" => {
                dimension.scope = QuotaScope::Models(vec!["gpt-x".into()]);
                ctx.target.upstream_model = None;
            }
            // Unknown declarations may use a concrete scope from the observation.
            "observed_scope" => dimension.scope = QuotaScope::Unknown,
            _ => unreachable!(),
        }
        assert_eq!(
            run_context(&h, ctx).await,
            if mode == "observed_scope" { "a" } else { "b" },
            "{mode}"
        );
    }
}
