# SEO Validator v7.4.0 — Edge-Case Hardening

**Released:** 2026-05-13 (same day as v7.3.0)

## Why this release

v7.3.0 added three audit sections (image-asset redirects, generic redirect-set
verification, orphan image audit) and immediately caught six real bugs on
first production run. The release cleared the structural gaps v7.2.1 had.

v7.4.0 closes the edge cases v7.3.0 leaves behind:

- An article can declare an `og:image` URL that doesn't exist on disk. v7.3
  flags the article as having an OG tag (correct) and flags an orphan
  asset if there's a different file with no reference (correct). It does
  not flag the declared-but-missing case. Crawlers see a broken og:image,
  social previews break, the audit passes.
- Two `location =` blocks in nginx can declare the same source path.
  nginx silently picks one — typically the first match in the hash table
  lookup, but the behaviour is not documented as deterministic across
  config reloads. v7.3 has no view on this.
- The OG image set (67 dynamically-generated PNGs under `dist/og/`) was
  outside the orphan audit's scope. v7.3 only walked `dist/images/`.
  An article rename without OG regeneration left stale PNGs on disk
  with no detection.

v7.4.0 closes all three.

## Audit changes

### Section 5: Open Graph Tags Audit (extended)

Existing v7.2.1 behaviour preserved: every page's `og:title`, `og:description`,
`og:image` presence is checked.

New in v7.4: every unique `og:image` URL collected from the audit run gets
an HTTP HEAD request. Non-2xx is a hard failure. The output lists each
broken URL plus the pages that reference it (first three, then a count).

The HEAD requests run in parallel via the existing `--workers` thread pool,
so the additional runtime cost is roughly the slowest single HEAD round-trip
(~50-200ms on a typical site with 60-80 unique og:image URLs).

### Section 21: Redirect Set Verification (extended)

Existing v7.3 behaviour preserved: every exact-match nginx redirect is
verified source 301 → target 2xx in one hop, or source 410 if the rule is
a Gone declaration.

New in v7.4: duplicate source-path detection. The parser already preserves
duplicates (the regex matches every `location = X { return ...; }` block
independently); v7.4 adds a `Counter` pass over the parsed result and
flags any source appearing more than once. Each duplicate is reported as
a hard failure with the count of conflicting blocks.

This catches a real config-integrity bug class: two `location =` rules
with the same source path silently shadow each other. Whichever rule
wins depends on nginx's internal lookup order, which is not stable across
config reloads. Removing the duplicate is always the right fix.

### Section 22: Orphan Image Audit (extended)

`IMAGE_DIRS` default extended from `["images"]` to `["images", "og"]`.
The orphan walk now covers both directories recursively. The output
reports per-directory subtotals when more than one directory is walked:

```
Walked dist/images/ (66 assets), dist/og/ (67 assets).
133 image asset(s) total, 133 referenced from rendered HTML, og:image
tags, favicon links, or redirect targets.

Orphan image summary: 0 issues found
```

The dist/og/ tree is structured by section subdirectory (insights,
ai-architecture, open-source) on speytech.com. The walk is recursive so
the nested structure is handled without configuration. Section banner
PNGs at `dist/og/insights.png` (etc.) are picked up alongside the
per-article PNGs at `dist/og/insights/<slug>.png`.

## CLI additions

- `--image-dirs DIR1,DIR2[,...]` — override the default `images,og` scan
  set. Useful when the validator runs against a site with a different
  directory layout, or when narrowing a debugging audit to a single
  subdirectory.

All other v7.3 flags continue to work unchanged.

## Other changes

- `_verify_redirect` accepts any 2xx final status as healthy. The
  previous `final_status != 200` check was unnecessarily narrow; a 201
  or 204 would have been flagged as a broken target despite being valid
  HTTP success responses. In practice every speytech.com redirect target
  resolves to 200, but the tighter check could surface false-positives
  on sites that use other 2xx codes for asset responses.
- `head_check()` helper added for the section 5 og:image verification.
  Wraps `requests.head()` with `allow_redirects=True` so a 301'd og:image
  still resolves to 200. Returns `(status, error)` tuple; error is None
  on success.

## Performance impact

| Section | v7.3.0 runtime | v7.4.0 runtime | Delta |
|---------|----------------|----------------|-------|
| Section 5 (Open Graph) | <0.1s | +1-2s (67 HEADs ÷ 10 workers) | +1-2s |
| Section 21 (Redirect Set) | ~3s | +<0.01s (Counter pass) | +0.01s |
| Section 22 (Orphan) | <0.1s | +<0.1s (one extra rglob) | +0.1s |
| **Total v7.4 delta** | | | **~2-3s** |

Total runtime against speytech.com's 85-URL sitemap remains under 40s
in sampled mode, well within typical CI budgets.

## Backward compatibility

Every v7.3 CLI flag continues to work. No breaking changes. Section
numbering unchanged (5, 21, 22 extended in place rather than new
sections added). Existing CI integrations need no modifications.

The og:image resolution check is a new hard-failure condition. If any
article on a site currently declares a broken og:image, the v7.4 build
will fail. This is by design: the audit is meant to catch exactly this
class of bug. Verify the site is clean against v7.4 before integrating
into a strict CI gate.

## Verification matrix

| Scenario | Mode | State | Expected exit |
|----------|------|-------|---------------|
| 1 | default | clean (no broken og, no duplicates, no orphans) | 0 |
| 2 | default | one og:image returns 404 | 1 |
| 3 | default | nginx config has a duplicate source | 1 |
| 4 | default | orphan in `dist/og/` (deleted article's PNG retained) | 0 (INFO) |
| 5 | --strict | orphan in `dist/og/` | 1 (escalated) |
| 6 | default | `--image-dirs images` skips dist/og/ | 0 (orphans in og/ not flagged) |

A `VERIFICATION-v7.4.0.md` recipe accompanies this changelog with the
exact commands to run each scenario on Axioma.

## Six pre-existing orphans from v7.3 — status

The six orphan images surfaced by v7.3's first production run on
2026-05-13 (the ML observability article cluster's missing frontmatter
image references) were resolved the same day. The v7.4.0 release runs
against a clean baseline.
