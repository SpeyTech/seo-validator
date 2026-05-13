# SEO Validator v7.3.0 — Asset-Redirect Coverage

**Released:** 2026-05-13

## Why this release

A real operational finding on 2026-05-13 surfaced a gap in v7.2.1's coverage.
Five legacy hero SVGs were renamed to slug-matching canonical names, and
nginx 301 redirects were added at the canonical apex server block for the
legacy paths. v7.2.1's trailing-slash audit only sampled HTML URLs, so
broken image redirects would have passed the audit while real failures
existed on the live site.

v7.3.0 closes that gap and the broader gap it implies: any exact-match
`location =` redirect declared in the nginx config could silently break
between deploys without the validator noticing.

## Audit sections added

### Section 20: Image-Asset Redirect Audit

Discovers every `location = /images/X.{svg,png,...} { return 301 /images/Y.{svg,png,...}; }`
rule from the nginx config and verifies each one resolves cleanly:

- source returns 301
- target returns 200
- chain length is exactly 2

Cross-references rendered HTML to flag pages that still reference legacy
slugs. These are reported as INFO findings (the redirect catches them for
visitors, but the page source should be updated to the canonical path).

Hard failure for broken or chained redirects.

### Section 21: Redirect Set Verification

Same discovery extended to every exact-match `location =` redirect in the
config, not just image redirects. Covers legacy URL renames
(`/contact-us/` → `/contact/`), search-engine and crawler conventions
(`/sitemap.xml` → `/sitemap-index.xml`, RSS/Atom paths → `/rss.xml`),
410 Gone rules for deliberately-removed URLs, and the image redirects from
section 20.

Hard failure for any source that no longer redirects, any target that no
longer returns 200, or any redirect that chains.

The full speytech.com config produces 17 rules: 16 × 301 redirects plus
1 × 410 Gone (`/home-temp/`).

### Section 22: Orphan Image Audit

Walks `dist/images/` for image assets and flags any that are never
referenced from rendered HTML pages, `og:image` tags, favicon links, or
nginx redirect targets. Redirect targets count as references — a 301 to a
canonical asset is itself a reference that should keep the target on disk.

INFO finding by default; `--strict` escalates to failure.

## CLI additions

- `--nginx-config PATH` — path to nginx config for redirect discovery.
  Defaults to `/etc/nginx/sites-available/speytech.com`. The validator
  runs on the same host as nginx (post-atomic-swap, against the live
  site), so this file is normally readable.
- `--dist-path PATH` — built site root for the orphan image audit.
  Defaults to `./dist`.
- `--skip-image-redirects` — skip section 20.
- `--skip-redirect-set` — skip section 21.
- `--skip-orphan-images` — skip section 22.

`--strict` now escalates orphan-image INFO findings to failures, in
addition to its existing v7.2.1 behaviour for title/description length.

## Fallback behaviour

If the nginx config is unreadable (e.g. running the validator on a dev
machine without prod config), sections 20 and 21 fall back to a small
inline `KNOWN_REDIRECTS` constant containing the five image redirects that
motivated this release. A YELLOW warning indicates the fallback is in use.
The orphan audit handles the same case via the redirect set it discovers
(or falls back to KNOWN_REDIRECTS).

The nginx config remains the single source of truth. The inline fallback
exists so the validator runs without prod access; it should not be
relied on for production audits.

## Other changes

- `collect_page_data()` now parses `<source srcset="...">` inside `<picture>`
  elements and `<img srcset="...">` attributes, in addition to `<img src>`.
  v7.2.1 missed these, which would have produced false-positive orphan
  findings for any responsive image. Same-domain image references are now
  collected as normalised paths in `data['image_sources']`.
- Version string consolidated into a single `__version__` module constant.
  Previously hardcoded in three places (docstring, argparse version action,
  main() banner). Shipped as a separate prep commit.
- `Path` and `os` added to imports for the filesystem walk in section 22.

## Performance impact

Section 20 verifies the 5 image redirects in ~1 second.
Section 21 verifies the full 17-rule set in ~3 seconds.
Section 22 is a pure filesystem walk and runs in well under a second.

Total v7.3 additions: roughly 4–5 seconds on top of the existing v7.2.1
runtime of ~30 seconds against speytech.com's 85 sitemap URLs. Well within
the 5-second additional-runtime budget set out in the v7.3 ticket.

## Backward compatibility

Every existing v7.2.1 flag continues to work. Sections 2–19 are unchanged.
New sections append at 20, 21, 22 to avoid disturbing existing CI output
diffs. The default behaviour is to run all new sections; use the
`--skip-*` flags to opt out individually.

## Test discipline

Before declaring v7.3 done, verify the new sections fire correctly on:

1. **All-clean state** — every redirect resolves, no orphan images.
2. **Deliberately broken redirect** — rename a redirect target file in
   `dist/images/` and confirm section 20 reports a hard failure.
3. **Deliberately added orphan image** — drop a test SVG in
   `dist/images/` that no article references and confirm section 22
   reports an INFO finding (default) or a failure (`--strict`).

A verification recipe is provided in the v7.3 ticket and the README.
