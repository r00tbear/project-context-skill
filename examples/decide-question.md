# Example: one Decide-phase question batch

Every Decide question follows this shape: fact with evidence → options with effort/trade-offs → recommendation first.

---

**Stack — 1 decision needed.**

Two HTTP clients coexist:
- axios — 34 usages across 12 files (`src/api/client.ts`)
- hand-written fetch wrapper — 9 usages across 4 files (`src/utils/http.ts`), silently retries 5xx twice

Options:
- **A (recommended): keep axios**, migrate the 4 wrapper files — effort S; +13kb bundle, keeps interceptors
- B: keep the wrapper, migrate 12 files — effort M; fewer dependencies, loses interceptors

Say "A" (or just confirm) to accept the recommendation, or pick another option.
