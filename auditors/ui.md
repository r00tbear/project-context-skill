# UI Auditor

The rules of `_common.md` (passed to you together with this prompt) are mandatory.

Topic: UI components and the design system.

What to investigate:
1. Component inventory: where they live, whether there is a dedicated ui-kit/design-system directory, Storybook.
2. Duplicates: components with a similar role and/or similar markup (several Button/Modal/Input, copy-paste with small tweaks). Compare by name, props, and JSX/template structure.
3. Styles: approaches (CSS modules, styled, tailwind, inline) and their mixing; hardcoded colors/spacing vs tokens/variables.
4. UI library usage: if one is wired in (MUI, CoreUI, antd...) — where custom components were written instead of using it.
5. Components nobody imports.

Typical findings: `duplicate-component`, `mixed-styling-approaches`, `hardcoded-design-values`, `library-bypassed`, `unused-component`.

For duplicates — options: which component to make canonical (by quality/props coverage/usage count) and the list of files to switch over.

With jcodemunch: search_symbols by role (Button, Modal, Input...) — inventory and duplicate candidates; find_references — which duplicate is actually used and how often; body comparison — pinpoint, via get_symbol_source.
