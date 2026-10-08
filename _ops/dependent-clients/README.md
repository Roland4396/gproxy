# Existing Stream quota keeper / v4 cookie-management compatibility

v4 intentionally refuses cookie-authenticated unsafe management requests without
an explicit same-origin `Origin`. v3 accepted a missing Origin; therefore the old
Python keeper's cached GET checks can pass while its quota-probe and reveal POSTs
fail locally with 403. Do not relax native authorization/CSRF, synthesize a trusted
Origin from a forgeable legacy marker, or interpret this failure as account quota.

`stream-quota-v4-origin.patch` applies to the independently deployed Stream source
(`aetherstream/features/quota_keeper/{client,service}.py` and its quota tests), **not**
to the Gproxy Rust workspace. The manifest records the exact before/after source
hashes. Verify them before applying; do not overwrite unrelated local changes.
The client derives Origin from HTTPX's normalized scheme/Host authority, including
default ports and IPv6, and supplies it only on its private management POSTs. The
separate inference client is unchanged. Error logs expose HTTP status/method and a
fixed operation name, never exception text, credentials, response bodies or URLs.

2026-10-08 incident evidence:

- Production audit: quota-diagnostics POSTs were rejected locally with 403; cached
  GETs returned 200. An isolated empty-credential canary reproduced the exact
  `forbidden: cross-origin request` without any upstream query.
- Installed native Gproxy plus the patched Stream client, with **fake** credentials
  and a namespace-local Antigravity mock, passed real quota diagnostics/reveal 200,
  fresh receipt evidence, second-based boundaries and four-window/inactive behavior.
  Missing/foreign Origin remained 403. No external network, OAuth refresh or
  inference was used in this validation.
- 271 offline application tests passed. Isolated real Nginx SSE/WebSocket promotion,
  rollback while draining, readiness rejection and final drain passed.
- Production patch image inherits the complete running Stream image unchanged except
  these two Python modules: base `sha256:2467669c76b25e8b138fd8f555beab7cfe649b7356845bb8a126ad24eea061e5`,
  patched `sha256:53a7e7ffe8271196240504649ffb8d8b60c302cfb10deacecd2681d2c1b256e6`.
  This is a pure COPY layer, not a local compiler/dependency rebuild. Pending unrelated
  Stream source/requirements edits were not included. Gproxy itself was not rebuilt.
- The normal Stream rolling-release controller promoted green and retired blue only
  after its drain gates. The fixed entry, Gproxy, Tavern and account-pool containers
  were not restarted, and runtime/pool configuration hashes were unchanged.
- With explicit user approval, only the three newly quota-error-paused keeper rows
  (9, 16, 21) were resumed. Existing guards/triggers were preserved. Normal scheduled
  production work then returned 200 once per account (16 total tokens each), and all
  three new cycles were confirmed. Prior generation-error rows (15, 26, 27) stayed
  paused. These authorized production requests are not represented as unpaid mocks.

Runtime state/backups and screenshots remain private. For rollback, retain the
**current shared guards**, including work submitted after promotion. Never restore
an older keeper snapshot that could authorize duplicate requests. The original
Stream image has the missing-Origin behavior and would require this client fix
again after a code rollback.
