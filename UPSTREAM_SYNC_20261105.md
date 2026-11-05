# Upstream synchronization with local behavior preservation

Status: deployed and verified on 2026-11-05 at 10:21 UTC (18:21 Asia/Shanghai).

- Production source: `d3a78037e561dad2a111b07ce1f82c5d42cbfeeb`.
- Immutable image: `gproxy-local:upgrade-v4-d3a78037e561`.
- Manual build: https://github.com/Roland4396/gproxy/actions/runs/42003017675.
- This report and subsequent QA-harness-only edits do not alter that compiled application revision.

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

All acceptance gates below passed. The previous image, stopped complete data mount,
and original configuration are retained for rollback across schema migrations.

Native rejection update: a credential that refused a request is no longer hit again within that same request. The existing three-failure cooldown threshold remains across separate requests; a bound model still never escapes to another account. Regressions and the installed-image fixture check this behavior explicitly rather than restoring redundant retry traffic.


## Acceptance evidence

- GitHub formatting, console lint/tests/i18n/build, ten offline Python regressions,
  51 focused local-contract tests and 3,168 full native tests (252 suites) passed.
  Full native testing includes the default non-Tauri workspace; it is not merely
  the three focused targets.
- Verified all artifact checksums, OCI manifest/config/layers, source label and
  embedded binary SHA-256 before loading the image. Binary SHA-256:
  `2421f136d4ba28ab791c4212fcdbf65a56a45daacf189dc8eb492b8fb78479f4`.
- Installed-image tests used only synthetic credentials on Docker `network=none`.
  Twelve Antigravity mock requests verified legal stable per-account native/header
  sessions and cascade/trajectory labels, distinct accounts, unique request IDs,
  content/model/token/project changes, separate-request retry, image envelope,
  process restart, JSON and unbuffered SSE. Four generic mock requests additionally
  verified provider-scoped routes and upstream release on downstream disconnect.
  Mock `modelVersion` deliberately differs from the requested alias; this proves
  converter tolerance, not any observation of Google's current real model routing.
- An isolated real-data copy passed native and legacy management reads, cached
  quota reads and console startup with six providers and eighteen credentials.
  All 108 original tables remained; 107 table-row checks preserved all original
  row multisets except expirable live login sessions. Credential ciphertexts,
  password hashes, account ownership/configuration, prices, archived history and
  settled costs were preserved. All 32 dynamic cache-price tiers are equivalent.
  The same audit passed against fresh pre/post-cutover snapshots of production.
- Dynamic cache-price verification converts the legacy database's scale-nine
  money atoms into decimal strings. An initial QA mismatch came from comparing
  atoms directly to decimal prices; application migration was already correct.
  The former 30-minute bucket maps to unknown TTL, not an invented 1,800-second TTL.
- Desktop and iPhone WebKit passed: 35 screenshots, twelve navigation surfaces,
  login, search, inactive quota/weekly guidance, trend expansion, routing/models,
  rates/tiers, histories/audit, unlimited retention and locale changes; zero page
  errors or external requests. Two quota responses were synthetic fixtures.
  The harness now uses upstream's Personal/Admin navigation toggle, and removes
  its owned runtime clone and temporary master-key file after stopping its canary.
- Deployment checked access/upstream logs, Stream lifecycle and pending-work
  counters, quota-keeper activity and established GProxy connections. All were
  idle immediately before maintenance. Only the GProxy image was replaced;
  Stream blue/green, the stable Stream entry, account-pool and Tavern containers
  kept their exact container IDs. Runtime environment and mounts are unchanged.
- Post-deployment health and console returned HTTP 200. Account-pool DNS access to
  `http://gproxy:8787/healthz` also returned HTTP 200. Both management representations,
  cached quota, the complete Antigravity configuration, fixed-account sessions and
  all four existing Argon bindings were verified unchanged. Startup had no errors
  or warnings; Stream remained stable and the final activity check was idle.
- All canary containers, runtime copies and temporary QA key/login files were
  removed. Sealed QA postboot database copies were deleted after verification;
  actual rollback data/configuration and the old image are retained privately.
- All sixteen workflows are manual/reusable only. No automatic build was enabled.
  The maintained update branch is used; the fork's default branch was not reset.

No real inference, real quota probe, OAuth refresh or GPU request was used in
acceptance testing. This validates the upgraded software and retained local
contracts; it does not claim a successful real Google generation or immunity to
account restrictions. Upstream's separate Argon-quota normalization gap remains
outside this update.
