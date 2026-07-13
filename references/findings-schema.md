# Findings schema

Each auditor returns one YAML document with this structure. The main agent validates it before saving to docs/audit/findings/<auditor>.yaml.

```yaml
auditor: stack            # stack | architecture | ui | db | bloat | security | testing
scanned_at: 2026-07-11
scope:                    # what was actually scanned
  included: ["src/", "package.json"]
  excluded: ["node_modules/", "vendor/legacy/"]
findings:
  - id: stack-001                    # <auditor>-NNN, stable across runs
    kind: duplicate-purpose-libs     # a type from the auditor's prompt
    title: "Two HTTP clients: axios and a hand-written fetch wrapper"
    severity: high                   # low | medium | high | critical
    confidence: high                 # low | medium | high — how sure the auditor is about the fact
    evidence:
      - path: "src/api/client.ts"
        detail: "axios, 34 usages across 12 files"
      - path: "src/utils/http.ts"
        detail: "hand-written fetch wrapper, 9 usages across 4 files"
    options:                         # solution options; empty if the solution is obvious
      - id: A
        summary: "Keep axios, migrate the 4 fetch-wrapper files"
        effort: S                    # S | M | L
        tradeoffs: "minimal migration; +13kb bundle"
      - id: B
        summary: "Keep the fetch wrapper, migrate 12 files"
        effort: M
        tradeoffs: "fewer dependencies; more edits and no interceptors"
    status: new                      # new | persisting | resolved (for repeat runs)
extras: {}                           # auditor-specific data (e.g. the DB schema from the db auditor)
```

Rules:
- a finding without evidence is not allowed;
- ids are stable: on a repeat run the auditor receives its previous findings file — matching facts keep their id and get status: persisting; the main agent marks the vanished ones as resolved. "The same fact" = same kind + same primary evidence path (the first evidence entry); if the file was renamed but the content clearly matches, keep the id;
- severity is about impact on the project, confidence is about the auditor's certainty; these are different axes;
- options are ordered: the auditor's recommendation goes first.
