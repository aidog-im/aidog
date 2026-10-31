# Locale Packs (i18n)

UI strings for the I'm AiDog product surface live here — never hard-coded in source.

- `en.yaml` — **default locale** (international route)
- `zh.yaml` — Chinese (switchable at runtime via user setting)
- New language = add `<lang>.yaml` + register in the locale index; zero code changes.
- YAML schema will be aligned with the upstream 上游内核 `locales/*.yaml` overlay
  format right after kernel install (P1) — do not invent keys before that.
- Resolution order: explicit user setting > profile config > default `en`.
- All keys and developer-facing descriptions in this pack are written in English;
  only the value strings are localized.
