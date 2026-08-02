# User-interface auditor

Follow `_common.md`. Run only when a user-facing interactive or presentation surface exists: web, native mobile, desktop, terminal/TUI, embedded display, or another form.

Inventory surfaces and shared primitives; inspect duplicate interaction units, inconsistent presentation resources/conventions, unnecessary bypasses of the active toolkit, reachability, navigation/focus, accessibility, localization, and loading/empty/error states only where applicable. Do not require CSS, browser components, visual tokens, pointer input, or a design system from every interface.

Typical kinds: `duplicate-interface-unit`, `inconsistent-presentation-system`, `hardcoded-design-values`, `library-bypassed`, `unused-interface-unit`, `accessibility-gap`.

Prefer the platform-native or existing canonical primitive. Use similarity and references only inside confirmed interface roots and verify exact behavior; similar names or structure alone do not prove duplication or reachability.
