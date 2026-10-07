# Gproxy v3 → v4 local patch comparison

Baseline: `54ee589a91f6005278a064db6d714acabb600067`.
Pinned upstream: `14ec5d71d7aca94255dee7b6704504234d1e3f37` (v4.1.1 lineage).
This is an implementation/evidence ledger; deployment gates below are not yet complete.

## Every fork-only commit

| Original commit | Original behavior | v4 disposition / regression evidence |
|---|---|---|
| `4411129895b9c9ba5f22f5e031f4a7dc9ba72d2d` | Renew OAuth before quota queries; Antigravity independent Gemini/3p 5h and weekly windows, correct model scopes | Native core renews near-expiry material and permits exactly one 401 refresh/retry for unknown expiry. `core/tests/quota.rs::quota_query_refreshes_expired_material_before_querying` and `quota_query_refreshes_unknown_expiry_on_401_and_retries_only_once`; `channel/tests/antigravity.rs::the_summary_reports_each_familys_windows`. Ported localized four-window labels. |
| `2121e1e4513a521dc19f0ed75907a3dac9ec3055` | Disabled 5h is inactive, never available 0%; hide its reset and direct the viewer to weekly quota | **Required an explicit port:** stock v4 omitted the bucket entirely. Preserve `antigravity_disabled` with null usage and exclude it from classification/cycle advancement/selection. Native console shows inactive, no progress/earlier reset, with weekly guidance. Backend summary regression plus `console/pages/credentials/upstream.test.tsx` regression. |
| `09d208de8cb4d4c320c785508c3a4164bbb918f4` | Keep caller's Claude output limit; continue stripping it for Gemini | Native `antigravity/claude.rs::{output_limit,apply_limits}` restores the limit and reconciles a single valid thinking budget. `claude_keeps_its_output_limit_and_takes_one_thinking_budget` and `gemini_models_still_lose_the_output_limit`. v4 also bounds the limit safely rather than sending an invalid budget. |
| `bd44477ae6bc9bda61d407427923efb6860f4c30` | Choose earliest usable reset without violating allowed pool or a valid session pin; rotate ties/unknowns | Native `core/select_reset.rs` and five `core/tests/selection.rs` regressions cover actual times, pins, ties/unknowns, expired observations and operation/model scopes. **Ported migration:** `v3/provider_config.rs` now preserves `earliest_reset` instead of falling back to round-robin; added import regression. |
| `6a310c3564821950c03232d4ace6461e95a3d8ef` | Correct Antigravity client identity, agent envelope, per-account/per-conversation opaque runtime IDs, project onboarding | Native v4 has the updated captured CLI 1.2.16 envelope/UA, canonical caller `request.sessionId` preservation and ANTIGRAVITY onboarding metadata. **Ported missing runtime headers:** account-scoped `x-machine-id`, session-scoped `x-vscode-sessionid`, client name/version, and overwrite untrusted client identity headers. Preserve v4's newer envelope grammar rather than restoring obsolete agent request-ID syntax or overriding an explicit caller session. `identity.rs` stability/privacy regression and prepare regression; native agent-envelope/session/onboarding regressions. |
| `54ee589a91f6005278a064db6d714acabb600067` | Group each Google tool's OAuth client ID/secret as one identity for login and refresh | Native `shared/code_assist/google.rs::GoogleTool` groups client ID/secret, endpoints, scopes, UA and onboarding metadata. Antigravity/Gemini CLI supply their own tool identity for authorize/exchange/refresh. Native `authorize_uses_antigravitys_own_client_and_scopes`, exchange/discovery and refresh regressions. Existing provider settings have no custom OAuth client override to lose. |
| `9b669b931124240c61ae1f9f494bce361a4de81c` | Do not cancel the initial build/cache while queuing a follow-up | GitHub native-image workflow uses `cancel-in-progress: false`, cache on failure and immutable commit artifacts. Production is never a compiler runner. |
| `b03d6d38e5f937c00935e80183220e93918cd025` | Build quota and Claude output-limit fixes together | One pinned commit, complete console/backend/channel/protocol regressions, one image; no mixing binaries from separate branches. |
| `3d716f4258f17920ed5fe96c8549ec9e25a3e360` | Include earliest-reset regressions in the shipped image build | Complete core tests plus provider migration test execute before the release artifact; build metadata and SHA-256 identify the exact source revision. |

Paths in the table are relative to `crates/gproxy-*` or `console/src` as named.

## Additional upgrade-only preservation work

- Explicit-null retention/max database size are policies, not missing values. Payload and
  quota-observation retention follow the old policy, and no new independent payload budget
  silently deletes retained history. The null-only patch regression caught a JSON-equality
  bug in the first CI run; presence is now checked before deciding a patch is empty.
- Real-snapshot validation additionally found native v3 tier blobs use `input_price`,
  `output_price`, `cache_read_price` and `cache_creation_30m_price`, whereas upstream's
  translator only read short export keys. The first isolated import lost those overrides
  despite preserving all 31 rules and 109 base rates. Deployment was rejected. The importer
  now accepts both spellings with explicit short-key precedence, retaining zero and null;
  a synthetic regression and per-field real audit cover all 32 tier rows.
- Native import preserves provider invocation names, enabled flags, credentials/ownership,
  key digests/password hashes, routes/aliases, prices and usage history. Its reports must be
  audited against the real snapshot: seeded defaults may be superseded by native channel
  declarations, but operator-made routing/rewrite decisions may not disappear silently.
- `_ops/migrate_history_v3.py` archives every original table losslessly and projects captures,
  audit and quota observation/cycle history into native tables. Validate all archives,
  referenced IDs, capture bytes and costs before acceptance. Validation precedes commit.
  Missing v3 stream-event timestamps are not fabricated; original body bytes are kept as
  buffered legacy captures. Original sessions/health and unsupported legacy details remain
  available in the explicitly named archive, rather than being misrepresented as native rows.
- Existing Stream quota keeper and account-pool management clients keep legacy login aliases,
  imported numeric IDs, arrays, secret envelopes and second-based quota boundaries. v4's four
  Antigravity source IDs are grouped under their old `subscription` capability for these clients.
  Native auth/scope/CSRF/audit remain the only authority; inference/SSE/WebSocket traffic bypasses
  the adapter. Incomplete legacy list projection fails explicitly rather than hiding accounts.
  After Google refresh, native account facts move into `provider_fields`; the legacy scoped
  reveal additionally exposes those fields flat, retaining the envelope and existing flat values.
  A synthetic regression covers both newly refreshed and original v3 secret layouts.
- No production route/model/account removal and no production restart during implementation.
  Claude's current Stream effort preference is medium, independent of the Gproxy image upgrade.

## Acceptance evidence (update only after passing)

- [x] Consistent read-only v3 snapshot, original compose/environment/image retained.
- [x] Original source worktree unchanged; upgrade ports isolated on their own branch.
- [x] Console initial lint/tests/build passed on GitHub; seven projection unit tests passed.
- [x] First native CI compiled all selected packages; regression correctly stopped release
      on explicit-null policy loss (`37674738072`). Fixed from that log evidence.
- [x] Retained v3 image booted on the original snapshot in a network-isolated canary:
      login 200 and provider/credential count parity; original snapshot unchanged;
      canary stopped and removed. Production remained on its original process/image.
- [ ] All final-source backend/channel/protocol/console regressions passed on GitHub.
- [ ] Immutable final artifact hashes/image revision verified.
- [ ] Real-snapshot native import and history projection: complete counts/content/cost audit.
- [ ] Local synthetic HTTP/SSE and management compatibility; WebKit console verification.
- [ ] Production activity gates, final snapshot and migration, safe image/data switch.
- [ ] Production health/parity, rollback rehearsal and final report.

No paid inference, live cloned-account refresh/probe or GPU wakeup is permitted for validation.

CI also caches workspace artifacts explicitly: the action's default only caches
dependencies, which otherwise recompiled the large protocol workspace library on
every small patch. See [the action's workspace-cache option](https://github.com/Swatinem/rust-cache#example-usage).
