# Upgrade acceptance QA inventory

All checks use synthetic credentials, closed snapshots or management reads.
No live generation, quota probe/OAuth refresh from cloned accounts, or GPU work.
Desktop and iOS/WebKit checks are separate from backend parity checks.

| Claim / surface | Functional check | Visual state / evidence |
|---|---|---|
| Accounts and login survive | Native import; compare every enabled flag, provider, sealed-secret presence, password hash and key digest; namespace-only legacy login | v4 login, credential list and provider list; desktop and iPhone WebKit |
| Routing and priority survive | Compare every operator dialect mapping, route strategy/tier/weight, rule attachment and enabled alias; CI earliest-reset/pinning tests | Route detail and operation/rule detail, readonly inspection |
| Prices survive | Compare all model patterns, priorities, fixed-decimal rates, units and conditions | Pricing list/detail, no edits to migrated prices |
| Usage/capture/audit history survives | Multiset digest for every archived table; native usage tokens and original/unrounded cost; exact captured bytes and headers; cycle costs/boundaries | Usage/capture/audit pages with migrated rows; evidence files private |
| Unlimited retention survives | Verify every old retention/logging policy and new payload/observation policies; reopen canary and repeat audit | Settings retention page, readonly |
| Antigravity four windows remain distinct | Native channel fixtures and localized static keys; fixture-only quota response in browser | Four labels and weekly-exhausted state; paused 5h has no available percentage, reset or trend |
| Existing private v3 clients remain compatible | Login aliases/cookie, integer IDs/arrays, cached second-based reset and real observed times, scoped reveal envelope; refreshed OAuth nested-to-flat projection | Management evidence only; no secret screenshots |
| Native v4 API stays native | Login through portal API, paged string-ID admin lists; auth/tenant/CSRF regressions | v4 console navigation/login unchanged |
| HTTP/SSE stay streaming | Installed immutable image against namespace-local synthetic upstream; first event observed before later event/end; JSON shape and no inference adapter buffering | Wire timing evidence, not a screenshot |
| iOS viewport usability | WebKit touch login and navigation; check viewport/document and key-region bounds | iPhone viewport initial and post-navigation screenshots; no clipped primary controls |
| Rollback is executable | Retained v3 image boot on snapshot, login/count parity, stop/remove; production old image/data/compose retained | Rehearsal metadata and private logs |

## User-visible controls covered

- Login inputs and submit; invalid login then successful login.
- Responsive navigation open/close and credentials/providers/routes/pricing/history/settings links.
- Credential search/filter and detail tabs; quota trend expand/collapse on an active fixture window.
- Language switch and return to the original language; four-window labels in supported locales.
- Readonly settings details; no changes to imported account/configuration/history.

## Off-happy-path exploration

1. Unauthenticated management read, ordinary-user denial and foreign-origin cookie write.
2. Missing/unknown quota start/reset, exhausted weekly with inactive 5h, empty captures and interrupted stream.
3. Narrow viewport with dense history/list rows and open navigation; check touch-accessible controls.
4. Early downstream disconnect on synthetic SSE; upstream must be released and no second request retried.

## Browser safety

Quota panels automatically probe on open. In cloned-account UI checks intercept
those requests and fulfil an explicitly synthetic/cached DTO fixture; never let
the console initiate a real probe. Browser and canary are network-isolated. A
fixture rendering check is not evidence of fresh live upstream availability.
Interactive `js_repl` is not available in this executor; use the installed Node
Playwright driver with persistent handles per test run instead of claiming it was used.
Mobile WebKit supports real touch taps but not the Playwright wheel API. The
driver stages an internal quota-dialog scroll position before tapping its trend
control; it does not claim to have tested an iOS swipe gesture. Desktop and
mobile screenshots are captured only after the appropriate overlay state has
settled, with before/after DOM/bounding-box checks for dialog evidence.
