# Optional stream-idle clock

This additive local patch extends the deployed `d9da335b48cb` runtime. The operator requested retaining the existing 1,200,000 ms response wait while disabling the 300,000 ms stream-idle cutoff.

- `streamIdleTimeoutMs: 0` is now an explicit disabled setting. Positive values retain their old meaning.
- `requestTimeoutMs` remains positive-only; its default and production target remain 1,200,000 ms.
- The internal `Duration::MAX` sentinel is handled by directly awaiting the operation, **without registering a large timer**. Native and browser runtime helpers share this behavior.
- HTTP/SSE, live WebSocket admission, and Responses WebSocket lanes respect the disabled idle clock. Disabled lanes do not add an overflowing duration to an instant.
- Cancellation, request/response size limits, explicit per-request deadlines, credential guards, route selection, retry policy, and quota behavior are unchanged.
- Settings UI accepts zero and labels its meaning.

Validation uses synthetic credentials and isolated fake upstreams only. No real inference or production private prompt is needed. Deploy only after checking active requests and established connections. A new binary is required before setting zero; the previous binary rejects zero. Reverting the binary therefore requires restoring a positive idle setting first, while idle, without restoring an old full database.
