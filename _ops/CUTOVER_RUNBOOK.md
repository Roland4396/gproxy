# Private Gproxy v4 cutover / rollback runbook

The application is built only on GitHub. The deployed binary must match a
successful native-image run and a verified immutable revision; an earlier
candidate that lost tier-price overrides is not eligible.

## Before stopping anything

1. Complete native/console/channel/protocol tests, image archive verification,
   real-snapshot import and field-level/archive parity (including all tiers),
   installed synthetic JSON/SSE/provider-mount/disconnect checks, and desktop /
   iPhone WebKit checks. Close and remove all isolated canaries.
2. Retain the v3 image, its complete existing data directory, compose file and
   environment. Keep backups and any logs/screenshots owner-private; none may
   be committed to the public source repository.
3. Check Stream's release journal and private application lifecycle counters:
   no active HTTP/WebSocket or request-owned detached work. A stable slot's
   normal background ownership is not itself an active request.
4. Inspect recent Stream/account-pool/Gproxy upstream/access logs, recent usage
   and activity timestamps, and established Gproxy connections. Distinguish
   idle private keep-alive sockets from live requests. There must be no active
   outgoing model request or pending generation. Recheck immediately before
   the stop; health alone never authorizes it. If busy, wait for completion.

## Switch only Gproxy

1. Announce the idle cutover. Gracefully stop only the existing Gproxy service.
   Do not restart Stream, its stable Nginx entry, Tavern or the account pool.
2. With the old server stopped, use SQLite's backup API to take a **fresh**
   self-contained snapshot, including any WAL. Run integrity and foreign-key
   checks; record its hash. Also back up the exact current compose/environment
   and container/image metadata. Leave the complete original data directory
   intact. Never copy only a main SQLite file while ignoring a nonempty WAL.
3. Import that snapshot into a fresh separate v4 data directory with the
   immutable image, no external network and no published ports. Apply the
   offline history projection and read-only parity auditor. Abort/restore v3
   on any mismatch; archive retention is not a substitute for native policy,
   effective routing, prices, credentials or usage preservation.
4. Boot the network-isolated management canary, validate legacy and native
   reads/login, stop it, take a SQLite post-boot backup and repeat parity.
   Stage the **post-boot self-contained** database for production so WAL writes
   are not lost. Keep the destination owned by the existing container UID.
5. Use a v4-only environment file with the original master key and automatic
   update disabled. Preserve the original environment file. Do not pass the
   bootstrap admin password to v4: the imported password hash and key digests
   must remain the authentication authority, as proven in the canary.
6. Atomically update the original compose path to the immutable image, the
   separate v4 data mount and its environment file. Retain the same service
   name, private networks, restart and logging policies; publish no host port.
   Start only Gproxy. Check readiness, image/revision, port/network metadata,
   effective legacy/native management reads and dependent service health.
7. Retake a read-only consistent database backup for post-start parity. Record
   that old v3 data/image/compose remain available and that no inference, live
   quota probe, OAuth refresh or GPU test was used. Existing UI tabs may need
   refresh/re-login because old sessions are archived rather than reused.

## Rollback

1. Apply the same active-request gate to the running v4 service. Never stop a
   busy instance merely to roll back. Preserve its full data and a fresh
   consistent snapshot, including requests or edits received since promotion.
2. Gracefully stop only Gproxy. Restore the retained original compose bytes
   **to the original compose path** and start only that service. This makes
   relative `.env` / `./data` paths resolve to the untouched original v3 data,
   not to an accidentally created directory under the backup folder.
3. Do not point the v3 image at a v4 database and do not attempt a schema
   downgrade. Post-promotion v4 writes remain in the retained v4 data and must
   be explicitly reconciled if rollback is used; do not promise bidirectional
   database migration that has not been implemented.
4. Verify v3 login, provider/credential counts, dependent health and activity
   gates. Keep the failed v4 data and private evidence for diagnosis.

The retained v3 image has already been booted on an isolated snapshot with
successful login/count parity. The final deployment report records actual
cutover paths, hashes and outcomes, not merely this planned procedure.

Executed 2026-10-08 05:55–05:56 CST with all gates passed; see
[the final deployment report](../DEPLOYMENT_REPORT_20261008.md).
