# Argon quota refresh / stale exhaustion recovery

Confirmed production evidence: account-scoped `exhausted` for the exact Argon high model remained until the old retry deadline after a fresh native quota diagnostic reported a positive independent `gemini-argon-wtu-weekly` allowance. Automatic recovery only handled `quota_exhausted`; the channel also discarded the independent bucket.

Fix:
- Declare/read/scope the independent Argon weekly bucket, including catalogues without per-model quotaInfo. Never relabel a near-end Argon week as five hours or combine it with ordinary Gemini quota.
- Ignore old ordinary-Gemini observations/blocks for Argon reset ordering and eligibility.
- Let a channel explicitly prove recovery of a finite model-scoped upstream exhaustion. Other channels opt out. Antigravity requires its own known, unexpired positive weekly window and no exhausted/unknown/disabled covering window.
- Clear the matching old store and cache blocks, retaining newer concurrent refusals and every credit, rate-limit, counted-budget, unrelated and unknown/global block. SQL deletion rechecks the observation cutoff.

No schema migration, account weight, session identity, routing, price or paid-usage permission change. Unknown/missing independent quota never grants Argon recovery. Real upstream refusal remains authoritative and can re-block normally. No new generation retry or polling job is added.

Validation: focused synthetic channel/core tests, complete native workspace suite and immutable image build via the existing manual GitHub workflow. Deployment requires an idle activity gate; never interrupt active production requests. No paid inference/GPU wakeup is used for validation.

Verified build: [manual native test/image run 42549490942](https://github.com/Roland4396/gproxy/actions/runs/42549490942), runtime commit `d9da335b48cbd05a46270a3f9af50e5acf0e4438`.

The network-none end-to-end canary reproduced the failure on `d3a78037e561` and passed on the new immutable image, including failed/exhausted refreshes staying blocked, positive independent recovery, unrelated Gemini exhaustion remaining blocked and persistence across restart. Existing account-session/JSON/SSE/restart canaries also passed. The separate follow-up ops commit adds this proved canary to future **manual-only** builds; it does not change the tested runtime code.
