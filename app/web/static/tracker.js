/* tracker.js — implement per arch v1 §4 + v2 A13/A8. ~120 lines, no deps.
   Batch: 10 events | 5s | sendBeacon on visibilitychange→hidden.
   sendBeacon ignores headers — session rides the cookie (trap #5).
   Scroll (▲A13): check every 150ms, emit HIGHEST milestone crossed.
   New events (▲A8): rec_click, rec_dismiss, conversion.
   Idempotency: crypto.randomUUID() per event. Queue cap 200. */
