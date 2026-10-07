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
