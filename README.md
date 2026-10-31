# I'm AiDog (我是智能狗)

> 🔴 Private repo · Zero public exposure until the fully self-developed core succeeds
> (owner-only use during maturity phase).

A dedicated embodied AI companion built for one person — a second self that
learns, reports proactively, and keeps evolving.

**Architecture**: three-layer separation (replaceable kernel / body layer owned
by this repo / business layer). Authoritative design document:
`docs/design/im_aidog_product_design.md` (confidential, internal analysis).

- **Phase 1**: B+ base (上游内核 as transitional kernel) + overlay body layer
- **Phase 2**: custom intelligent-agent substrate for the chain public chain
- **Endgame**: `mycore` self-developed kernel — swap the core, never swap "me"

## Install

```bash
git clone <product-repo-url> aidog && cd aidog   # private repo
bash deploy/install.sh                           # one command: provision + boot + verify
$EDITOR secrets.env                              # fill the slots it lists (by name)
aidog setup                                      # re-run until it prints READY
```

The installer creates every machine-local file (all gitignored) from sanitized
templates, installs the `aidog` command, boots the runtime, and reports exactly
which credential slots remain — by name, never by value. Full guide:
[`docs/installation.md`](docs/installation.md).

## Language / i18n

- Source code, comments, and markdown docs: **English** (international route).
- UI strings: locale packs under `overlay/locales/` — default **`en`**, switchable **`zh`**,
  new languages added as packs without code changes.

## License & Pricing (owner decree, 2026-10-17)

**Free forever — for any use.** I'm AiDog ships with **zero paid touchpoints**:
no paid tier, no in-app purchases, no usage fees, no feature locked behind
payment. Adding any charge to this product violates the owner decree and is
treated as a defect.

## Status

P2 complete (console, memory/routing, boot self-healing, credentials);
P3 in progress (settings write API, cost/usage panel, new-form HUD console).
