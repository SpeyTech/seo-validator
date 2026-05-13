# seo-validator

A CI/CD-integrated SEO audit tool for static websites. Validates a freshly
built site against 22 audit sections before the deploy goes live. Designed
to be dropped into a build pipeline as a post-build, pre-traffic gate.

Originally built for [speytech.com](https://speytech.com)'s atomic deploy
pipeline (Astro static build, nginx, atomic dist swap). Generalisable to
any static site that publishes a sitemap and serves over HTTPS.

## What it does

The validator emulates Google's crawl behaviour and checks for the issues
that most commonly degrade search visibility and break crawler trust:

- HTTP response codes for every URL in the sitemap
- Title, h1, meta description presence and length
- Canonical tag presence and correctness
- Trailing slash consistency (catches GSC "alternate page with canonical")
- Redirect chain detection
- Meta robots / noindex misconfiguration
- Open Graph tags (`og:title`, `og:image`, `og:description`)
- Viewport meta and favicon
- Heading hierarchy (scoped to `<main>`/`<article>` content)
- Image alt text coverage
- Internal broken links (`--thorough`)
- Orphan pages — in sitemap but not linked anywhere (`--thorough`)
- Duplicate titles and descriptions
- Mixed content (http resources on https pages)
- Soft 404 detection
- `robots.txt`, `llms.txt`, `llms-full.txt` presence
- JSON-LD structured data
- **Image-asset redirect verification** (nginx-config-driven)
- **Generic redirect-set verification** (every exact-match nginx redirect)
- **Orphan image audit** (dist assets never referenced from HTML)

The last three are the v7.3 additions; see [`CHANGELOG-v7.3.0.md`](./CHANGELOG-v7.3.0.md)
for the motivation.

## Quick start

```bash
pip install requests beautifulsoup4 lxml

./seo_validator.py --domain example.com
```

This runs a sampled audit. For a full audit (every sitemap URL, every
redirect verified):

```bash
./seo_validator.py --domain example.com --full
```

For the full set including broken-link and orphan-page checks (adds HTTP
requests, ~1–2 minutes on a typical site):

```bash
./seo_validator.py --domain example.com --full --thorough
```

Exit code is 0 if all hard checks pass, 1 if any hard failure is detected.
INFO findings and warnings do not affect the exit code unless `--strict`
is passed.

## CLI flags

| Flag | Description |
|------|-------------|
| `-d, --domain DOMAIN` | Domain to audit (required). |
| `-s, --sitemap URL` | Custom sitemap URL. Defaults to `https://<domain>/sitemap-0.xml`. |
| `-t, --timeout SECONDS` | Request timeout (default: 10). |
| `-w, --workers N` | Concurrent workers for URL checks (default: 10). |
| `--full` | Test every sitemap URL instead of sampling. |
| `--thorough` | Include broken-link and orphan-page checks. |
| `--strict` | Treat title/description length and orphan-image findings as failures. |
| `--skip-og` | Skip Open Graph audit. |
| `--skip-images` | Skip image alt text audit. |
| `--skip-image-redirects` | Skip the image-asset redirect audit (section 20). |
| `--skip-redirect-set` | Skip the generic redirect-set verification (section 21). |
| `--skip-orphan-images` | Skip the orphan image audit (section 22). |
| `--nginx-config PATH` | Path to nginx config for redirect discovery. Default: `/etc/nginx/sites-available/speytech.com`. |
| `--dist-path PATH` | Path to the built static site root for the orphan image audit. Default: `./dist`. |
| `--image-dirs DIR[,DIR...]` | Comma-separated subdirectories under `dist-path` to scan for orphans. Default: `images,og`. |
| `-v, --version` | Print version and exit. |
| `-h, --help` | Print help and exit. |

## Audit sections

1. Sitemap fetch
2. Basic SEO (status + core elements)
3. Title and description length
4. Meta robots (noindex detection)
5. Open Graph tags (presence + og:image URL resolves to HTTP 2xx)
6. Viewport meta (mobile-friendliness)
7. Favicon
8. Heading hierarchy
9. Image alt text
10. Canonical tags
11. Trailing slash canonical (GSC "alternate page" check)
12. Redirect chains
13. Duplicate content (titles and descriptions)
14. Mixed content (http on https)
15. Soft 404 detection
16. Internal broken links (`--thorough`)
17. Orphan pages (`--thorough`)
18. Critical files (`robots.txt`, `llms.txt`, `llms-full.txt`)
19. JSON-LD schema validation
20. Image-asset redirect audit
21. Redirect set verification (with duplicate-source detection)
22. Orphan image audit (walks `dist/images/` and `dist/og/`)

## Output format

```
============================================================
SEO Validator v7.3.0 - Comprehensive Site Audit
Domain: example.com
Mode: Sampling
============================================================
Fetching sitemap from: https://example.com/sitemap-0.xml ... OK (85 URLs)

Collecting page data for 85 URLs...
Collected data for 85 pages.


=== 2. Basic SEO Audit (Status + Core Elements) ===
✓ https://example.com/ -> 200 (title, h1, description)
...

=== 20. Image-Asset Redirect Audit ===
Discovered 5 image redirect rule(s) from /etc/nginx/sites-available/example.com. Verifying...
✓ /images/old-name-a.svg -> 301 -> /images/new-name-a.svg -> 200
✓ /images/old-name-b.svg -> 301 -> /images/new-name-b.svg -> 200
...

Image-asset redirect summary: 0 issues found

=== 21. Redirect Set Verification ===
Discovered 17 exact-match redirect rule(s) from /etc/nginx/sites-available/example.com. Verifying...
✓ /contact-us/ -> 301 -> /contact/ -> 200
✓ /home-temp/ -> 410
...

Redirect set summary: 0 issues found

=== 22. Orphan Image Audit ===
67 image asset(s) in dist, 67 referenced from rendered HTML or redirect targets.

Orphan image summary: 0 issues found

============================================================
AUDIT PASSED. All systems nominal.
============================================================
```

When something fails, the section reports the issue clearly and the
final summary turns red:

```
=== 20. Image-Asset Redirect Audit ===
Discovered 5 image redirect rule(s) from /etc/nginx/sites-available/example.com. Verifying...
✓ /images/old-name-a.svg -> 301 -> /images/new-name-a.svg -> 200
✗ /images/old-name-b.svg -> 301 -> /images/new-name-b.svg -> 404 (target broken)
   └─ Redirect target is broken. Either restore the target or remove the redirect.
...

Image-asset redirect summary: 1 issue found

============================================================
AUDIT FAILED. See errors above.
============================================================
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All hard checks passed. |
| 1 | One or more hard failures detected. |

INFO findings and warnings never affect the exit code unless `--strict`
is passed, in which case INFO findings escalate to failures.

When wiring into CI, run the validator without piping if you need the
exit code. A trailing `| tee` or `| grep` will mask the validator's
exit code with the pipe-tail's. Use `${PIPESTATUS[0]}` in bash if you
must pipe:

```bash
# Correct: exit code captured cleanly
python3 seo_validator.py --domain example.com
echo $?

# Also correct (bash): exit code via PIPESTATUS
python3 seo_validator.py --domain example.com 2>&1 | tee /tmp/audit.log
echo "${PIPESTATUS[0]}"

# Incorrect: $? reports tee's exit code, not the validator's
python3 seo_validator.py --domain example.com | tee /tmp/audit.log
echo $?   # always 0 if tee writes successfully
```

## Configuration

The validator reads two filesystem paths:

- **nginx config** (`--nginx-config`): used by sections 20 and 21 to
  discover redirect rules. The validator extracts every
  `location = X { return 301 Y; }` and `location = X { return 410; }`
  rule. If the file is unreadable, a small inline `KNOWN_REDIRECTS`
  fallback is used and a warning is emitted.

- **dist path** (`--dist-path`): used by section 22 to walk the built
  site's image assets. Defaults to `./dist`. If the path is not a
  directory, section 22 skips with a warning.

Both paths are read-only. The validator never writes to disk.

## Requirements

- Python 3.8+
- `requests`
- `beautifulsoup4`
- `lxml` (for XML sitemap parsing)

```bash
pip install requests beautifulsoup4 lxml
```

No other runtime dependencies.

## Pipeline integration

Designed to run after build, before traffic. The reference integration
in `build.sh` for speytech.com:

```bash
# Build to a staging directory
npx astro build --outDir ./dist.new

# Atomic swap
sudo mv dist dist.old
sudo mv dist.new dist

# Validate the live site post-swap
if ! python3 scripts/seo_validator.py --domain example.com; then
  # Roll back to the previous build
  sudo mv dist dist.failed
  sudo mv dist.old dist
  exit 1
fi
```

The validator hits the live HTTPS site, so it sees what real crawlers will
see, including nginx redirects, security headers, and the full response
chain.

## License

MIT. See [LICENSE](./LICENSE).

## Origin

Built for [speytech.com](https://speytech.com)'s CI pipeline. The repo
sits inside the site repo as a deployment-time tool, but the script
itself is generic — it makes no speytech.com-specific assumptions beyond
the default `--nginx-config` path, which is overridable. Drop it into any
static-site CI that wants a pre-traffic SEO gate.
