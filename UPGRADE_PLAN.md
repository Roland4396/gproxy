# Gproxy v4 upgrade — 2026-10-08

## Contract
Upgrade the production Gproxy deployment, preserving every local behavioral patch,
account, route priority, price, history record and relevant configuration.
Do not interrupt active requests, issue paid inference, wake GPUs, or expose ports.
Keep a complete tested rollback. Do not treat backups alone as history parity.

## Baseline and target
- Production source: `54ee589a91f6005278a064db6d714acabb600067`.
- Production image: `gproxy-local:antigravity-54ee589a91f6`.
- Pinned upstream target: `14ec5d71d7aca94255dee7b6704504234d1e3f37`
  (v4.1.1 lineage plus current Claude and usage fixes).
- Initial online SQLite snapshot: `/home/ubuntu/gproxy/backups/pre-v4-upgrade-20261008`.
  Integrity check passed; configuration and environment backed up with restrictive permissions.
- Original worktree remains untouched; all migration work is isolated here.

## Gates / checkpoints
1. Inventory every fork-only commit and all live configuration/history tables.
2. Map each patch to upstream equivalent or implement a port with regressions.
3. Compile and run backend/channel/protocol/console tests with synthetic/local mocks only.
4. Migrate a fresh isolated SQLite snapshot, auditing counts, content and effective behavior.
   Upstream v3 import does not migrate request captures/logs/sessions/quota health/audit.
   Resolve required history parity rather than silently dropping it.
5. Package an immutable image and verify local mock HTTP/SSE and console WebKit behavior.
6. Before switching: inspect access/upstream logs, DB activity and established connections.
   Take final consistent snapshot; migrate it; switch only when no active request exists.
7. Verify production health/configuration/history parity and retain executable rollback.
8. Commit/push source, produce patch-by-patch report with evidence, then mark goal complete.

## Safety / rollback
- Preserve original image and complete original data directory, not just a downgraded v4 DB.
- Tests use isolated data and local mock upstreams. No production quota refresh or OAuth refresh
  may run from the cloned database during validation.
- No service restart/recreate until activity gates pass.
- Never log decrypted credentials, keys, passwords or full sensitive bodies.

## Progress
- Initial backup and isolated upgrade worktree created. No production modifications.
- Fork-only inventory: six behavioral commits and three build/CI commits.
- Upstream v4 already has earliest-reset strategy and shared Antigravity 5h/weekly pools;
  exact behavioral equivalence is not yet proven.
- Added migration regressions for earliest-reset preservation and explicit unlimited
  retention. Independent payload/observation defaults must not prune old history.
- Console lockfile install and production frontend build passed locally.
- Added offline lossless table archive and native capture/audit/quota history projections;
  seven focused Python unit tests passed. Real v4 schema integration remains required.
- Local Rust compile hit earlyoom at `gproxy-protocol` (SIGTERM, 2082 MiB RSS).
  Kernel/userland logs confirmed low-memory selection; several Chrome processes were
  also terminated. Production Gproxy's start time is unchanged and Stream `/ready`
  is healthy. No further heavy compilation on this host: use isolated GitHub CI.
- Found dependent v3 admin API clients in Stream's quota keeper and account-pool capacity
  probes. Login shape, numeric IDs, pagination and quota response changes require a
  compatibility port before final deployment. Do not silently break these clients.
- Added a narrow v3 wire adapter around the native router: legacy login aliases,
  imported numeric IDs, credential/provider lists, cached upstream observations,
  diagnostic quota probes, secret envelopes and capacity-probe usage windows.
  Native authentication, tenant scope, CSRF and audit remain mandatory. Native v4
  clients keep their page/ID shapes; inference bodies and streams bypass the adapter.
  Synthetic router/security regressions are awaiting GitHub CI execution.
- Offline history projections now validate integrity and foreign keys before commit,
  so an invalid projection rolls back instead of leaving a partial destination.
- Production remains unchanged. A user-reported availability error was confirmed
  in Stream logs on the separate `anti5` route, not on the newly imported Claude
  subscription route; user cancelled the account/VNC investigation and resumed upgrade.
- Latest user preference: Stream Claude effort is **medium** again. In-place CAS
  update preserved inode and every other setting; live blue container readback passed.
- Exact comparison found two incomplete upstream equivalents: disabled-window UI/marker
  and account-scoped runtime headers. Both are explicitly ported; current v4 CLI grammar
  and canonical caller session preservation remain intact. Added channel and console tests.
- Native CI `37674738072` completed compilation but correctly failed our explicit-null
  retention regression: JSON equality treated `Some(None)` as the default `None` patch.
  Fixed patch-presence detection from that log evidence; no release artifact was deployed.
- Added `PATCH_COMPARISON.md` covering every fork-only commit and pending acceptance gates.
- Rollback image rehearsal passed on a private `--network none` canary using the original
  snapshot: management login 200, all six providers and 18 credentials present. The source
  snapshot hash was unchanged. Canary was stopped/removed; production remained running.
- Subsequent CI logs identified a test-only unsuffixed millisecond literal and the frontend's
  mandatory static i18n-key scanner. Added the explicit i64 type and static translation calls;
  neither failure reached release/deployment. Full CI must still pass on the corrected source.
- Compatibility review traced Google token refresh to v4's nested `provider_fields` envelope,
  while the existing private keeper reads flat `project_id`. Added a scoped legacy-only
  projection and synthetic regression that preserves native tokens/envelope and flat precedence.
- Added read-only real-snapshot parity auditor, network-isolated native import/canary driver,
  and explicit functional/visual/WebKit QA inventory. Ten Python tests pass; full native schema
  execution awaits the immutable GitHub artifact.
- CI `37678970970` passed the complete native/frontend suite and produced a verified image.
  Its isolated import retained all accounts, routes, base prices, captures, usage and original
  archives; installed-image JSON/SSE streaming also passed (first event before later emission).
  Expanded field-level audit caught a missing **tier price override** migration (`*_price`
  versus short export keys). This candidate is not production-eligible. Ported both spellings
  and added a zero/null/precedence regression; repeat full CI and real snapshot audit.
- Image verification binds OCI manifest → CI-recorded config → all layer hashes and embedded
  binary SHA. This host exposes the manifest digest as inspect.Id while the runner recorded
  the config digest; a string-only ID comparison is insufficient across those Docker versions.
- Post-boot parity uses SQLite's backup API, including committed WAL pages, not a main-file
  copy. WebKit's first driver launch found sudo's Node 12 rather than the installed Node 20;
  pin the driver binary explicitly and rerun before UI acceptance.
