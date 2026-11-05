# Upstream synchronization with local behavior preservation

Status: implementation and validation in progress; not deployed.

- Deployed local source before this update: `e91c12f645f5dfe47d34e87546cda89bf4a3d44c`.
- Pinned upstream source: `06bba2941ea4d7d9af7af934604804a24808dddd`.
- The update merges upstream into the maintained fork, never replaces it with an official image.
- See `PATCH_COMPARISON.md` for the original v3 preservation inventory and evidence.

| Local contract | Integration decision | Required evidence |
|---|---|---|
| Selected-account native and header session ids | Preserve the exact versioned row-identity seed and output encodings. Add the policy to the new hub envelope; do not adopt the upstream shared constant when account pinning is enabled. | Golden id; content/model/token/project changes, account switching and process restart wire regression. Newly introduced hub cascade/trajectory labels use the same account policy; their request timestamp remains unique for identical calls. |
| Account/device runtime identity headers | Preserve local identity headers while updating reported client version to the new hub; retain the device-id derivation. | Header ownership, identity isolation and no plaintext identity on wire tests. |
| Updated IDE/hub protocol | Adopt upstream IDE 2.6.0 / hub 2.28.1 (CLI 1.4.10), OAuth/discovery, connection profile and request labels together. | Native channel tests; synthetic installed-image generation and SSE. |
| Exact model-account restrictions | Keep model_credentials intersected before selection, affinity, retry and probing; unavailable bound accounts fail closed. No cooldown probe may bypass an explicit model binding. | Missing/disabled/blocked/not-admitted/empty bindings, affinity and failure regressions. |
| Earliest-reset strategy | Preserve reset-time priority and round-robin ties/unknowns; do not silently add quota-headroom weighting to this local policy. | Existing reset ordering/tie/expiry/scope regressions. |
| Inactive five-hour quota window | Keep the display-only marker, null usable quota, hidden reset and weekly-window guidance; merge upstream expiring-credit UI support. | Backend classification and console/WebKit quota tests. |
| Existing private management clients | Preserve scoped v3-compatible aliases, numeric ids, lists, cached quota, refresh-field projection and native auth/CSRF authority. | Host management/security tests and installed-image legacy reads. |
| Caller output limits / Google tool identity | Retain verified caller-limit behavior and per-tool OAuth identity; adopt upstream compatible extensions. | Existing channel regressions. |
| Retention, prices, accounts and histories | Preserve explicit-null unlimited retention, tier-price spellings/zero/null, lossless archive/projection logic and all live account/route/price policies. | Python projection tests, native migration regressions, isolated startup schema/content/secret audit and rollback readiness. |
| Build and operation policy | Every workflow is manual/reusable only. Build immutable images on GitHub, never compile heavily on the production host. No paid inference or GPU wakeup. Switch only after logs/activity/connection checks are idle. | Workflow event audit, complete CI and immutable-image verification, mock wire/WebKit checks and idle deployment report. |

The upstream removal of the old independent session_affinity boolean is a native
strategy API change, not a local behavioral patch. None of the six live providers
sets it; their configured strategies are preserved. New sticky/round-robin-affinity
semantics retain upstream coverage, and the local binding test covers both reset
selection and a genuine round-robin-affinity pin.

Do not mark this update complete until all acceptance gates pass. Preserve the old
image, complete database and configuration for rollback across schema migrations.
