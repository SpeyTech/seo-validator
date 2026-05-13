#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
SEO Validator - Google Crawl Emulator (CI/CD Mode)
================================================================================

A comprehensive site health checker that emulates Google's crawl behaviour,
designed for CI/CD integration to catch SEO and accessibility issues before
deployment.

This script validates:
  - HTTP response codes for all sitemap URLs
  - On-page SEO elements (title, h1, meta description, length checks)
  - Canonical tag presence and correctness
  - Trailing slash consistency (catches "alternate page with canonical" issues)
  - Redirect chain detection (catches nested/chained redirects)
  - Meta robots / noindex detection
  - Open Graph tags (og:title, og:image, og:description)
  - Viewport meta tag (mobile-friendliness)
  - Favicon presence
  - Heading hierarchy (h1 → h2 → h3 nesting)
  - Image alt text audit
  - Internal broken link detection
  - Orphan page detection (in sitemap but not linked)
  - Duplicate titles and descriptions
  - Mixed content detection (http on https)
  - Soft 404 detection
  - Critical files (robots.txt, llms.txt, llms-full.txt)
  - JSON-LD structured data validation
  - Image-asset redirect audit (nginx-config-driven)
  - Generic redirect-set verification (every exact-match nginx redirect)
  - Orphan image audit (assets in dist/ never referenced from rendered HTML)

Exit codes:
  0 - All checks passed
  1 - One or more critical failures detected

Usage:
  ./seo_validator.py --domain example.com
  ./seo_validator.py --domain example.com --full
  ./seo_validator.py --domain example.com --full --thorough
  ./seo_validator.py --help

Requirements:
  - Python 3.8+
  - requests
  - beautifulsoup4
  - lxml (for XML sitemap parsing)

Install dependencies:
  pip install requests beautifulsoup4 lxml

--------------------------------------------------------------------------------

Author:     William Murray <william@speytech.com>
            Regenerative Systems Architect, SpeyTech

Copyright:  © 2026 The Murray Family Innovation Trust

License:    MIT License

Website:    https://speytech.com

Version:    7.3.0
Created:    2026-01-25
Modified:   2026-05-13

Changelog:
  v7.3.0 (2026-05-13) - Added three audit sections for asset-redirect coverage
                      - Section 20: Image-Asset Redirect Audit. Verifies every
                        nginx `location =` image redirect resolves cleanly
                        (source 301, target 200, chain length 2). Cross-checks
                        rendered HTML so pages still referencing a legacy slug
                        get flagged as INFO findings.
                      - Section 21: Redirect Set Verification. Same discovery
                        across the full exact-match redirect set (legacy URL
                        renames, RSS conventions, 410 Gone rules). Hard
                        failure for any broken source or target.
                      - Section 22: Orphan Image Audit. Walks dist/images/
                        and flags assets never referenced from any rendered
                        HTML page or 301 redirect target. INFO finding by
                        default, escalates to failure under --strict.
                      - Nginx config parsed at runtime to avoid drift between
                        validator and source of truth. Override path via
                        --nginx-config. Inline KNOWN_REDIRECTS fallback if
                        the config is unreadable.
                      - collect_page_data() now captures <source srcset>
                        in addition to <img src> so responsive <picture>
                        elements are not false-flagged as orphan references.
                      - Consolidated version string into a single __version__
                        constant (was hardcoded in three places).
  v7.2.1 (2026-02-07) - Added SOFT_404_EXEMPT_PATHS for pages where 404-like
                        content is legitimate (e.g., changelog describing 404 page)
  v7.2.0 (2026-02-07) - Reduced false positives across multiple audits
                      - Title length: min 30→15, max 60→70 (utility pages
                        don't need long titles, template suffixes inflate count)
                      - Title length: exempt utility/legal pages from checks
                      - Heading hierarchy: scoped to <main>/<article> content
                        (ignores layout components like footer/nav headings)
                      - Soft 404: scoped to <main>/<article> content (stops
                        matching article body text like changelog entries)
  v7.1.0 (2026-02-02) - Tightened soft 404 patterns to reduce false positives
                      - Soft 404 now shows which pattern matched
                      - Soft 404 is now a warning, not a failure
                      - Added --strict flag for title/description length failures
  v7.0.0 (2026-02-02) - Major release: comprehensive SEO audit suite
                      - Added meta robots/noindex detection
                      - Added Open Graph tag validation
                      - Added title/description length checks
                      - Added viewport meta validation
                      - Added favicon detection
                      - Added heading hierarchy validation
                      - Added image alt text audit
                      - Added internal broken link detection (--thorough)
                      - Added orphan page detection (--thorough)
                      - Added duplicate title/description detection
                      - Added mixed content detection
                      - Added soft 404 detection
                      - Refactored page data collection for efficiency
  v6.1.0 (2026-02-02) - Added --full flag for complete audits
  v6.0.0 (2026-02-02) - Added trailing slash and redirect chain audits
  v5.1.0 (2026-01-25) - Added argparse, MIT license
  v5.0.0 (2026-01-25) - Content-aware auditing, parallel checking

================================================================================
"""

import argparse
import os
import requests
from bs4 import BeautifulSoup
import concurrent.futures
import random
import json
import sys
import re
from pathlib import Path
from urllib.parse import urlparse, urljoin
from collections import defaultdict

__version__ = "7.3.0"

# =============================================================================
# ANSI Colours
# =============================================================================

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# =============================================================================
# Global State
# =============================================================================

DYNAMIC_URLS = []
ANY_FAILURE = False
PAGE_DATA = {}  # Stores parsed data for each URL

# Request configuration
UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
HEADERS = {"User-Agent": UA}
TIMEOUT = 10

# SEO constants — v7.2.0: relaxed to reduce false positives
# Min 15: utility pages like "FAQ — SpeyBooks" (15 chars) are perfectly fine
# Max 70: article titles + " — SpeyBooks Insights" suffix inflate the count
TITLE_MIN_LENGTH = 15
TITLE_MAX_LENGTH = 70
DESC_MIN_LENGTH = 50
DESC_MAX_LENGTH = 160

# Pages where short/long titles are expected and should not be flagged
# These are utility, legal, and listing pages — not content pages
TITLE_LENGTH_EXEMPT_PATHS = {
    '/faq/', '/contact/', '/pricing/', '/privacy/', '/terms/',
    '/cookies/', '/sitemap/', '/status/', '/about/', '/features/',
    '/security/', '/accessibility/', '/changelog/', '/sub-processors/',
    '/transparency/', '/anti-tax-evasion/', '/insights/',
}

# Soft 404 indicators - tuned to reduce false positives on technical content
# Each tuple is (pattern, description) for better debugging
SOFT_404_PATTERNS = [
    (r'(this\s+)?page\s*(was\s+)?(not|wasn\'t|cannot\s+be)\s*found', 'page not found'),
    (r'<title>.*404.*</title>', '404 in title'),
    (r'error\s*404', 'error 404'),
    (r'(this\s+)?(page|content|resource)\s*(has\s+been|was)\s*(removed|deleted|moved)', 'page removed/deleted'),
    (r'(this\s+)?(page|url)\s*(doesn\'t|does\s*not)\s*exist', 'page does not exist'),
    (r'sorry.*((page|content).*not\s+found|couldn\'t\s+find\s+(this|the)\s+page)', 'sorry page not found'),
    (r'(oops|uh\s*oh).*not\s+found', 'oops not found'),
]

# Pages where soft 404 patterns are expected in legitimate content
# (e.g., changelog entries describing 404 page features)
SOFT_404_EXEMPT_PATHS = {'/changelog/'}


# =============================================================================
# v7.3 Configuration: redirect audits and orphan image audit
# =============================================================================

# Default nginx config path. The validator runs on the same host as nginx
# (post-atomic-swap, against the live site), so this file is normally readable.
# Override with --nginx-config. If unreadable, the inline KNOWN_REDIRECTS
# fallback below is used and the section reports a YELLOW warning.
DEFAULT_NGINX_CONFIG = "/etc/nginx/sites-available/speytech.com"

# Inline fallback redirect set. Only consulted when the nginx config cannot
# be read. Kept intentionally minimal — covers the image renames that
# motivated v7.3. The nginx config remains the source of truth; this list
# exists so the validator runs cleanly on dev machines without prod config.
# Tuples are (source_path, target_path_or_status, status_code).
# A status_code of 410 means the target is the literal "410" return, not a path.
KNOWN_REDIRECTS = [
    ("/images/cardiocore-litigation.svg",  "/images/implantable-device-litigation.svg",  301),
    ("/images/hash-chain-diagram.svg",     "/images/cryptographic-proof-execution.svg",  301),
    ("/images/mycoeco-architecture.svg",   "/images/mycoeco-kernel.svg",                 301),
    ("/images/nvidia-asil-comparison.svg", "/images/nvidia-asil-determinism.svg",        301),
    ("/images/semantic-security-hero.svg", "/images/semantic-security-monitoring.svg",   301),
]

# File extensions considered image assets for the redirect and orphan audits.
IMAGE_EXTENSIONS = {".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico"}

# Directories under dist/ that hold image assets. Walked by the orphan audit.
# Kept narrow so the audit does not flag, e.g., favicon variants in dist root.
IMAGE_DIRS = ["images"]


def parse_nginx_redirects(config_path):
    """
    Extract exact-match redirect rules from an nginx config file.

    Matches lines of the form:
        location = /path { return 301 /target; }
        location = /path { return 410; }

    Returns a list of (source, target, status) tuples, where target is None
    for 410 Gone rules.

    Returns None if the file cannot be read; the caller treats this as
    a fallback signal.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, IOError):
        return None

    redirects = []

    # location = /path { return 301 /target; }
    pattern_301 = re.compile(
        r'location\s*=\s*(\S+)\s*\{\s*return\s+(301|302|303|307|308)\s+(\S+?)\s*;\s*\}',
        re.IGNORECASE,
    )
    for match in pattern_301.finditer(content):
        source, status, target = match.group(1), int(match.group(2)), match.group(3)
        redirects.append((source, target, status))

    # location = /path { return 410; }
    pattern_410 = re.compile(
        r'location\s*=\s*(\S+)\s*\{\s*return\s+410\s*;\s*\}',
        re.IGNORECASE,
    )
    for match in pattern_410.finditer(content):
        source = match.group(1)
        redirects.append((source, None, 410))

    return redirects


def is_image_path(path):
    """Return True if the given URL path looks like an image asset."""
    if not path:
        return False
    lower = path.lower().split("?", 1)[0].split("#", 1)[0]
    return any(lower.endswith(ext) for ext in IMAGE_EXTENSIONS)


def mark_failure():
    """Flag that at least one critical check has failed."""
    global ANY_FAILURE
    ANY_FAILURE = True


def mark_warning():
    """For future use - warnings don't fail CI but are reported."""
    pass


def print_header(title):
    """Print a section header."""
    print(f"\n\n=== {title} ===")


def print_subheader(title):
    """Print a subsection header."""
    print(f"\n{BOLD}{title}{RESET}")


# =============================================================================
# Sitemap Fetching
# =============================================================================

def fetch_sitemap_urls(sitemap_url):
    """Fetch and parse URLs from the XML sitemap."""
    print(f"Fetching sitemap from: {sitemap_url} ...", end=" ")
    try:
        r = requests.get(sitemap_url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"{RED}Failed ({r.status_code}){RESET}")
            mark_failure()
            return []
        
        soup = BeautifulSoup(r.content, 'xml')
        if not soup.find('loc'):
            soup = BeautifulSoup(r.content, 'html.parser')
            
        urls = [loc.text.strip() for loc in soup.find_all("loc")]
        print(f"{GREEN}Success{RESET}")
        print(f"Found {len(urls)} URLs in sitemap.")
        return urls
    except Exception as e:
        print(f"{RED}Error fetching sitemap: {e}{RESET}")
        mark_failure()
        return []


# =============================================================================
# Page Data Collection (Single Pass)
# =============================================================================

def collect_page_data(url):
    """
    Fetch a page and extract all SEO-relevant data in one pass.
    
    Returns a dict with all extracted data, or None on failure.
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        
        data = {
            'url': url,
            'status_code': r.status_code,
            'content_type': r.headers.get('Content-Type', ''),
            'html': None,
            'title': None,
            'title_length': 0,
            'meta_description': None,
            'meta_description_length': 0,
            'h1_tags': [],
            'heading_structure': [],
            'canonical': None,
            'meta_robots': None,
            'viewport': None,
            'og_title': None,
            'og_description': None,
            'og_image': None,
            'favicon': None,
            'images': [],  # List of (src, alt) tuples
            'image_sources': [],  # All same-domain image URLs (img src + source srcset), absolute paths
            'internal_links': [],  # List of internal hrefs
            'external_links': [],  # List of external hrefs
            'mixed_content': [],  # HTTP resources on HTTPS page
            'is_soft_404': False,
            'soft_404_reason': None,
            'issues': [],
        }
        
        if r.status_code != 200:
            data['issues'].append(f"HTTP {r.status_code}")
            return data
        
        # Only parse HTML content
        if 'text/html' not in data['content_type']:
            return data
        
        data['html'] = r.text
        soup = BeautifulSoup(r.text, 'html.parser')
        parsed_url = urlparse(url)
        base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
        
        # Title
        if soup.title and soup.title.string:
            data['title'] = soup.title.string.strip()
            data['title_length'] = len(data['title'])
        
        # Meta description
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            data['meta_description'] = meta_desc['content'].strip()
            data['meta_description_length'] = len(data['meta_description'])
        
        # H1 tags
        data['h1_tags'] = [h1.get_text(strip=True) for h1 in soup.find_all('h1')]
        
        # Heading structure — v7.2.0: scoped to <main>/<article> to ignore
        # layout headings (nav, footer, CTA sections) that cause false h2→h4 skips
        main_el = soup.find('main') or soup.find('article') or soup
        for tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            for heading in main_el.find_all(tag):
                data['heading_structure'].append((tag, heading.get_text(strip=True)[:50]))
        
        # Canonical
        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href"):
            data['canonical'] = canonical['href']
        
        # Meta robots
        meta_robots = soup.find("meta", attrs={"name": "robots"})
        if meta_robots and meta_robots.get("content"):
            data['meta_robots'] = meta_robots['content'].lower()
        
        # Viewport
        viewport = soup.find("meta", attrs={"name": "viewport"})
        if viewport and viewport.get("content"):
            data['viewport'] = viewport['content']
        
        # Open Graph
        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content"):
            data['og_title'] = og_title['content']
        
        og_desc = soup.find("meta", attrs={"property": "og:description"})
        if og_desc and og_desc.get("content"):
            data['og_description'] = og_desc['content']
        
        og_image = soup.find("meta", attrs={"property": "og:image"})
        if og_image and og_image.get("content"):
            data['og_image'] = og_image['content']
        
        # Favicon
        favicon = soup.find("link", rel=lambda x: x and 'icon' in x.lower() if x else False)
        if favicon and favicon.get("href"):
            data['favicon'] = favicon['href']
        
        # Images
        same_domain_image_paths = set()

        def _record_image_ref(raw_src):
            """
            Normalise an image reference to a same-domain path (e.g.
            /images/foo.svg) and record it. Off-domain images are ignored
            for the orphan/redirect audits but still flow through the
            mixed-content check above.
            """
            if not raw_src:
                return
            full = urljoin(url, raw_src)
            parsed_img = urlparse(full)
            if parsed_img.netloc and parsed_img.netloc != parsed_url.netloc:
                return
            if is_image_path(parsed_img.path):
                same_domain_image_paths.add(parsed_img.path)

        for img in soup.find_all('img'):
            src = img.get('src', '')
            alt = img.get('alt')
            if src:
                data['images'].append((src, alt))
                _record_image_ref(src)
                # Check for mixed content
                if parsed_url.scheme == 'https' and src.startswith('http://'):
                    data['mixed_content'].append(src)
            # <img srcset="..."> support (responsive img without <picture>)
            srcset = img.get('srcset', '')
            if srcset:
                for candidate in srcset.split(','):
                    candidate_src = candidate.strip().split()[0] if candidate.strip() else ''
                    _record_image_ref(candidate_src)

        # <source srcset="..."> inside <picture> elements. v7.2.1 missed these;
        # v7.3 captures them so responsive images do not falsely appear as
        # orphans in the orphan-image audit.
        for source_el in soup.find_all('source'):
            srcset = source_el.get('srcset', '')
            if not srcset:
                continue
            for candidate in srcset.split(','):
                candidate_src = candidate.strip().split()[0] if candidate.strip() else ''
                _record_image_ref(candidate_src)

        data['image_sources'] = sorted(same_domain_image_paths)
        
        # Links
        for a in soup.find_all('a', href=True):
            href = a['href']
            
            # Skip anchors, javascript, mailto, tel
            if href.startswith('#') or href.startswith('javascript:') or \
               href.startswith('mailto:') or href.startswith('tel:'):
                continue
            
            # Resolve relative URLs
            full_url = urljoin(url, href)
            parsed_href = urlparse(full_url)
            
            # Check for mixed content in links (less critical but worth noting)
            if parsed_url.scheme == 'https' and full_url.startswith('http://') and \
               parsed_href.netloc == parsed_url.netloc:
                data['mixed_content'].append(full_url)
            
            # Categorize as internal or external
            if parsed_href.netloc == parsed_url.netloc or parsed_href.netloc == '':
                # Normalize internal links
                normalized = f"{parsed_href.scheme or parsed_url.scheme}://{parsed_href.netloc or parsed_url.netloc}{parsed_href.path}"
                if not normalized.endswith('/') and '.' not in parsed_href.path.split('/')[-1]:
                    normalized += '/'
                data['internal_links'].append(normalized)
            else:
                data['external_links'].append(full_url)
        
        # Soft 404 detection — v7.2.0: scoped to <main>/<article> content only
        # Prevents matching body text in articles/changelogs that mention "page not found"
        main_text = main_el.get_text().lower()
        html_content = r.text.lower()  # Raw HTML still needed for title pattern
        for pattern, description in SOFT_404_PATTERNS:
            if description == '404 in title':
                # Title check uses raw HTML
                if re.search(pattern, html_content, re.IGNORECASE):
                    data['is_soft_404'] = True
                    data['soft_404_reason'] = description
                    break
            elif re.search(pattern, main_text, re.IGNORECASE):
                data['is_soft_404'] = True
                data['soft_404_reason'] = description
                break
        
        return data
        
    except Exception as e:
        return {
            'url': url,
            'status_code': None,
            'issues': [f"Error: {str(e)}"],
        }


def collect_all_page_data(urls, max_workers=10):
    """Collect data for all URLs in parallel."""
    global PAGE_DATA
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(collect_page_data, urls))
    
    for data in results:
        if data:
            PAGE_DATA[data['url']] = data
    
    return PAGE_DATA


# =============================================================================
# Audit Functions
# =============================================================================

def audit_basic_seo():
    """Audit basic SEO elements: status, title, h1, meta description."""
    print_header("2. Basic SEO Audit (Status + Core Elements)")
    
    pass_count = 0
    warn_count = 0
    fail_count = 0
    
    # Sort by URL for consistent output
    sorted_urls = sorted(PAGE_DATA.keys())
    
    for url in sorted_urls:
        data = PAGE_DATA[url]
        issues = []
        
        # Status check
        if data['status_code'] != 200:
            print(f"{RED}✗ {data['status_code'] or 'ERR'}{RESET} {url}")
            if data.get('issues'):
                for issue in data['issues']:
                    print(f"   {DIM}{issue}{RESET}")
            fail_count += 1
            continue
        
        # Title check
        if not data['title']:
            issues.append("Missing <title>")
        
        # H1 check
        if len(data['h1_tags']) == 0:
            issues.append("Missing <h1>")
        elif len(data['h1_tags']) > 1:
            issues.append(f"Multiple <h1> ({len(data['h1_tags'])})")
        
        # Meta description check
        if not data['meta_description']:
            issues.append("Missing meta description")
        
        if issues:
            print(f"{YELLOW}⚠ 200{RESET} {url} [{', '.join(issues)}]")
            warn_count += 1
        else:
            print(f"{GREEN}✓ 200{RESET} {url}")
            pass_count += 1
    
    if fail_count > 0:
        mark_failure()
    
    print(f"\nSummary: {GREEN}{pass_count} passed{RESET}, "
          f"{YELLOW}{warn_count} warnings{RESET}, "
          f"{RED}{fail_count} failed{RESET}")


def audit_title_description_length(strict=False):
    """Audit title and meta description lengths."""
    print_header("3. Title & Description Length Audit")
    
    issues_found = 0
    skipped_count = 0
    
    print_subheader("Title Issues")
    title_issues = []
    for url, data in PAGE_DATA.items():
        if data['status_code'] != 200:
            continue
        
        # v7.2.0: skip utility/legal pages where short titles are expected
        path = urlparse(url).path
        if path in TITLE_LENGTH_EXEMPT_PATHS:
            skipped_count += 1
            continue
        
        if data['title']:
            length = data['title_length']
            if length < TITLE_MIN_LENGTH:
                title_issues.append((url, data['title'], f"Too short ({length} chars, min {TITLE_MIN_LENGTH})"))
            elif length > TITLE_MAX_LENGTH:
                title_issues.append((url, data['title'], f"Too long ({length} chars, max {TITLE_MAX_LENGTH})"))
    
    if title_issues:
        for url, title, issue in title_issues:
            path = urlparse(url).path
            print(f"{YELLOW}⚠{RESET} {path}")
            print(f"   {DIM}{title[:70]}{'...' if len(title) > 70 else ''}{RESET}")
            print(f"   {issue}")
            issues_found += 1
    else:
        print(f"{GREEN}✓ All titles within optimal length ({TITLE_MIN_LENGTH}-{TITLE_MAX_LENGTH} chars){RESET}")
    
    if skipped_count > 0:
        print(f"   {DIM}({skipped_count} utility pages exempt from length checks){RESET}")
    
    print_subheader("Description Issues")
    desc_issues = []
    for url, data in PAGE_DATA.items():
        if data['status_code'] != 200:
            continue
        
        if data['meta_description']:
            length = data['meta_description_length']
            if length < DESC_MIN_LENGTH:
                desc_issues.append((url, data['meta_description'], f"Too short ({length} chars, min {DESC_MIN_LENGTH})"))
            elif length > DESC_MAX_LENGTH:
                desc_issues.append((url, data['meta_description'], f"Too long ({length} chars, max {DESC_MAX_LENGTH})"))
    
    if desc_issues:
        for url, desc, issue in desc_issues:
            path = urlparse(url).path
            print(f"{YELLOW}⚠{RESET} {path}")
            print(f"   {DIM}{desc[:80]}{'...' if len(desc) > 80 else ''}{RESET}")
            print(f"   {issue}")
            issues_found += 1
    else:
        print(f"{GREEN}✓ All descriptions within optimal length ({DESC_MIN_LENGTH}-{DESC_MAX_LENGTH} chars){RESET}")
    
    if strict and issues_found > 0:
        mark_failure()
        print(f"\nLength audit summary: {RED}{issues_found} issues found (--strict mode: FAIL){RESET}")
    else:
        print(f"\nLength audit summary: {YELLOW if issues_found else GREEN}{issues_found} issues found{RESET}")


def audit_meta_robots():
    """Check for noindex/nofollow directives that might block indexing."""
    print_header("4. Meta Robots Audit (Noindex Detection)")
    
    issues_found = 0
    
    for url, data in sorted(PAGE_DATA.items()):
        if data['status_code'] != 200:
            continue
        
        robots = data.get('meta_robots', '')
        if robots and ('noindex' in robots or 'none' in robots):
            path = urlparse(url).path
            print(f"{RED}✗{RESET} {path}")
            print(f"   {DIM}robots: {robots}{RESET}")
            print(f"   {DIM}This page will NOT be indexed by Google{RESET}")
            issues_found += 1
            mark_failure()
    
    if issues_found == 0:
        print(f"{GREEN}✓ No noindex directives found - all pages indexable{RESET}")
    else:
        print(f"\nMeta robots summary: {RED}{issues_found} pages blocked from indexing{RESET}")


def audit_open_graph():
    """Check Open Graph tags for social sharing."""
    print_header("5. Open Graph Tags Audit")
    
    missing_og = []
    partial_og = []
    
    for url, data in PAGE_DATA.items():
        if data['status_code'] != 200:
            continue
        
        has_title = bool(data.get('og_title'))
        has_desc = bool(data.get('og_description'))
        has_image = bool(data.get('og_image'))
        
        if not has_title and not has_desc and not has_image:
            missing_og.append(url)
        elif not (has_title and has_desc and has_image):
            missing = []
            if not has_title:
                missing.append('og:title')
            if not has_desc:
                missing.append('og:description')
            if not has_image:
                missing.append('og:image')
            partial_og.append((url, missing))
    
    if missing_og:
        print_subheader(f"Missing all OG tags ({len(missing_og)} pages)")
        for url in missing_og[:10]:  # Show first 10
            print(f"{YELLOW}⚠{RESET} {urlparse(url).path}")
        if len(missing_og) > 10:
            print(f"   {DIM}... and {len(missing_og) - 10} more{RESET}")
    
    if partial_og:
        print_subheader(f"Partial OG tags ({len(partial_og)} pages)")
        for url, missing in partial_og[:10]:
            print(f"{YELLOW}⚠{RESET} {urlparse(url).path} - missing: {', '.join(missing)}")
        if len(partial_og) > 10:
            print(f"   {DIM}... and {len(partial_og) - 10} more{RESET}")
    
    if not missing_og and not partial_og:
        print(f"{GREEN}✓ All pages have complete Open Graph tags{RESET}")
    
    total_issues = len(missing_og) + len(partial_og)
    print(f"\nOpen Graph summary: {YELLOW if total_issues else GREEN}{total_issues} pages with incomplete OG tags{RESET}")


def audit_viewport():
    """Check for viewport meta tag (mobile-friendliness)."""
    print_header("6. Viewport Meta Audit (Mobile-Friendliness)")
    
    missing_viewport = []
    
    for url, data in PAGE_DATA.items():
        if data['status_code'] != 200:
            continue
        
        if not data.get('viewport'):
            missing_viewport.append(url)
    
    if missing_viewport:
        print(f"{RED}✗ {len(missing_viewport)} pages missing viewport meta tag:{RESET}")
        for url in missing_viewport[:10]:
            print(f"   {urlparse(url).path}")
        if len(missing_viewport) > 10:
            print(f"   {DIM}... and {len(missing_viewport) - 10} more{RESET}")
        mark_failure()
    else:
        print(f"{GREEN}✓ All pages have viewport meta tag{RESET}")


def audit_favicon(domain):
    """Check for favicon presence."""
    print_header("7. Favicon Audit")
    
    # Check if favicon.ico exists at root
    favicon_url = f"https://{domain}/favicon.ico"
    try:
        r = requests.head(favicon_url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 200:
            print(f"{GREEN}✓{RESET} /favicon.ico exists")
        else:
            print(f"{YELLOW}⚠{RESET} /favicon.ico returned {r.status_code}")
    except Exception as e:
        print(f"{YELLOW}⚠{RESET} /favicon.ico error: {e}")
    
    # Check if pages have favicon link tags
    pages_with_favicon = sum(1 for data in PAGE_DATA.values() 
                            if data['status_code'] == 200 and data.get('favicon'))
    total_pages = sum(1 for data in PAGE_DATA.values() if data['status_code'] == 200)
    
    if pages_with_favicon == total_pages:
        print(f"{GREEN}✓{RESET} All pages have favicon link tag")
    elif pages_with_favicon > 0:
        print(f"{YELLOW}⚠{RESET} {pages_with_favicon}/{total_pages} pages have favicon link tag")
    else:
        print(f"{YELLOW}⚠{RESET} No pages have favicon link tag (relying on /favicon.ico)")


def audit_heading_hierarchy():
    """Check heading hierarchy (h1 → h2 → h3 proper nesting)."""
    print_header("8. Heading Hierarchy Audit")
    
    issues_found = 0
    
    for url, data in sorted(PAGE_DATA.items()):
        if data['status_code'] != 200:
            continue
        
        # v7.2.0: heading_structure is now scoped to <main>/<article> in
        # collect_page_data(), so layout headings (nav, footer, CTA) are excluded
        structure = data.get('heading_structure', [])
        if not structure:
            continue
        
        issues = []
        prev_level = 0
        
        for tag, text in structure:
            level = int(tag[1])  # h1 -> 1, h2 -> 2, etc.
            
            # Check for skipped levels (e.g., h1 -> h3)
            if prev_level > 0 and level > prev_level + 1:
                issues.append(f"Skipped from <{f'h{prev_level}'}> to <{tag}>")
            
            prev_level = level
        
        # Check if first heading is h1
        if structure and structure[0][0] != 'h1':
            issues.append(f"First heading is <{structure[0][0]}>, should be <h1>")
        
        if issues:
            path = urlparse(url).path
            print(f"{YELLOW}⚠{RESET} {path}")
            for issue in issues[:3]:  # Show first 3 issues per page
                print(f"   {DIM}{issue}{RESET}")
            issues_found += 1
    
    if issues_found == 0:
        print(f"{GREEN}✓ All pages have proper heading hierarchy{RESET}")
    else:
        print(f"\nHeading hierarchy summary: {YELLOW}{issues_found} pages with hierarchy issues{RESET}")


def audit_image_alt_text():
    """Check images for alt text."""
    print_header("9. Image Alt Text Audit")
    
    pages_with_issues = []
    total_images = 0
    images_missing_alt = 0
    
    for url, data in PAGE_DATA.items():
        if data['status_code'] != 200:
            continue
        
        images = data.get('images', [])
        missing = [(src, alt) for src, alt in images if alt is None or alt.strip() == '']
        
        total_images += len(images)
        images_missing_alt += len(missing)
        
        if missing:
            pages_with_issues.append((url, len(missing), len(images)))
    
    if pages_with_issues:
        print_subheader(f"Pages with missing alt text ({len(pages_with_issues)} pages)")
        for url, missing_count, total in sorted(pages_with_issues, key=lambda x: -x[1])[:15]:
            path = urlparse(url).path
            print(f"{YELLOW}⚠{RESET} {path} - {missing_count}/{total} images missing alt")
        if len(pages_with_issues) > 15:
            print(f"   {DIM}... and {len(pages_with_issues) - 15} more pages{RESET}")
    
    if total_images > 0:
        coverage = ((total_images - images_missing_alt) / total_images) * 100
        color = GREEN if coverage == 100 else YELLOW if coverage >= 80 else RED
        print(f"\nAlt text coverage: {color}{coverage:.1f}%{RESET} ({total_images - images_missing_alt}/{total_images} images)")
    else:
        print(f"{GREEN}✓ No images found to audit{RESET}")


def audit_canonicals(domain):
    """Audit canonical tags on a random sample of pages."""
    print_header("10. Canonical Tag Audit")
    
    sample_size = 5
    samples = [f"https://{domain}/"]
    
    if len(DYNAMIC_URLS) > 0:
        candidates = [u for u in DYNAMIC_URLS if u != f"https://{domain}/"]
        if len(candidates) >= sample_size:
            samples.extend(random.sample(candidates, sample_size))
        else:
            samples.extend(candidates)
    
    print(f"Auditing {len(samples)} random pages...")
    issues_found = 0
    
    for full_url in samples:
        data = PAGE_DATA.get(full_url, {})
        path = full_url.replace(f"https://{domain}", "")
        
        if data.get('canonical'):
            print(f"{GREEN}✓{RESET} {path} -> {data['canonical']}")
        else:
            print(f"{RED}✗{RESET} {path} -> NO CANONICAL TAG FOUND")
            mark_failure()
            issues_found += 1
    
    if issues_found > 0:
        print(f"\nCanonical summary: {RED}{issues_found} pages missing canonical{RESET}")


def audit_trailing_slash(domain, full_audit=False):
    """Audit trailing slash behaviour."""
    print_header("11. Trailing Slash Canonical Audit (GSC 'Alternate Page' Check)")
    
    trailing_slash_urls = [u for u in DYNAMIC_URLS if u.endswith('/') and u != f"https://{domain}/"]
    
    if not trailing_slash_urls:
        print(f"{YELLOW}No trailing-slash URLs found to audit.{RESET}")
        return
    
    if full_audit:
        samples = trailing_slash_urls
        print(f"Full audit: checking ALL {len(samples)} trailing-slash URLs...")
    else:
        sample_size = min(20, len(trailing_slash_urls))
        if len(trailing_slash_urls) > sample_size:
            samples = random.sample(trailing_slash_urls, sample_size)
            print(f"Sampling {sample_size} of {len(trailing_slash_urls)} trailing-slash URLs (use --full for all)...")
        else:
            samples = trailing_slash_urls
            print(f"Checking {len(samples)} trailing-slash URLs...")
    
    issues_found = 0
    
    for canonical_url in sorted(samples):
        non_slash_url = canonical_url.rstrip('/')
        path = canonical_url.replace(f"https://{domain}", "")
        non_slash_path = non_slash_url.replace(f"https://{domain}", "")
        
        result = follow_redirects(non_slash_url)
        
        if result['error']:
            print(f"{RED}✗{RESET} {non_slash_path} -> Error: {result['error']}")
            mark_failure()
            issues_found += 1
            continue
        
        chain = result['chain']
        final_url = result['final_url']
        
        if len(chain) == 2 and chain[0][1] == 301 and final_url == canonical_url:
            print(f"{GREEN}✓{RESET} {non_slash_path} -> 301 -> {path} {DIM}(correct){RESET}")
        elif len(chain) == 2 and chain[0][1] == 302 and final_url == canonical_url:
            print(f"{YELLOW}⚠{RESET} {non_slash_path} -> 302 -> {path} {DIM}(should be 301){RESET}")
            issues_found += 1
        elif len(chain) > 2:
            chain_str = " -> ".join([f"{c[1]}" for c in chain])
            print(f"{YELLOW}⚠{RESET} {non_slash_path} -> CHAIN: {chain_str}")
            issues_found += 1
        elif len(chain) == 1 and chain[0][1] == 200:
            print(f"{RED}✗{RESET} {non_slash_path} -> 200 (no redirect)")
            print(f"   {CYAN}└─ GSC will flag as 'Alternate page with proper canonical tag'{RESET}")
            mark_failure()
            issues_found += 1
        elif final_url != canonical_url:
            print(f"{YELLOW}⚠{RESET} {non_slash_path} -> unexpected destination")
            issues_found += 1
    
    print(f"\nTrailing slash summary: {RED if issues_found else GREEN}{issues_found} issues found{RESET}")


def audit_redirect_chains(domain, full_audit=False):
    """Audit for redirect chains."""
    print_header("12. Redirect Chain Audit")
    
    if not DYNAMIC_URLS:
        print(f"{YELLOW}No URLs to check.{RESET}")
        return
    
    test_urls = []
    
    if domain.startswith('www.'):
        non_www = domain[4:]
        test_urls.append(f"https://{non_www}/")
    else:
        test_urls.append(f"https://www.{domain}/")
    
    test_urls.append(f"http://{domain}/")
    
    if full_audit:
        test_urls.extend(DYNAMIC_URLS)
        print(f"Full audit: checking {len(test_urls)} URLs for redirect chains...")
    else:
        sample_size = min(10, len(DYNAMIC_URLS))
        test_urls.extend(random.sample(DYNAMIC_URLS, sample_size))
        print(f"Checking {len(test_urls)} URLs for redirect chains (use --full for all)...")
    
    chain_issues = 0
    
    for url in test_urls:
        result = follow_redirects(url)
        chain = result['chain']
        path = url.replace(f"https://{domain}", "").replace(f"http://{domain}", "")
        if not path:
            path = url
        
        if result['error']:
            print(f"{RED}✗{RESET} {path} -> Error: {result['error']}")
            chain_issues += 1
            continue
        
        if len(chain) == 1 and chain[0][1] == 200:
            continue
        
        if len(chain) == 2:
            status = chain[0][1]
            final_path = result['final_url'].replace(f"https://{domain}", "")
            if status == 301:
                if 'http://' in url or 'www.' in url != 'www.' in result['final_url']:
                    print(f"{GREEN}✓{RESET} {path} -> 301 -> {final_path}")
            elif status == 302:
                print(f"{YELLOW}⚠{RESET} {path} -> 302 -> {final_path} {DIM}(should be 301){RESET}")
            continue
        
        if len(chain) >= 3:
            chain_str = " -> ".join([f"{c[1]}" for c in chain])
            final_path = result['final_url'].replace(f"https://{domain}", "")
            print(f"{RED}✗{RESET} {path}")
            print(f"   Chain: {chain_str}")
            print(f"   {DIM}({len(chain)-1} redirects - consolidate to single 301){RESET}")
            chain_issues += 1
            mark_failure()
    
    print(f"\nRedirect chain summary: {RED if chain_issues else GREEN}{chain_issues} chain issues found{RESET}")


def audit_duplicates():
    """Check for duplicate titles and meta descriptions."""
    print_header("13. Duplicate Content Audit")
    
    titles = defaultdict(list)
    descriptions = defaultdict(list)
    
    for url, data in PAGE_DATA.items():
        if data['status_code'] != 200:
            continue
        
        if data['title']:
            titles[data['title']].append(url)
        if data['meta_description']:
            descriptions[data['meta_description']].append(url)
    
    # Find duplicates
    dup_titles = {t: urls for t, urls in titles.items() if len(urls) > 1}
    dup_descs = {d: urls for d, urls in descriptions.items() if len(urls) > 1}
    
    issues_found = 0
    
    print_subheader("Duplicate Titles")
    if dup_titles:
        for title, urls in dup_titles.items():
            print(f"{YELLOW}⚠{RESET} \"{title[:50]}{'...' if len(title) > 50 else ''}\"")
            for url in urls:
                print(f"   {DIM}{urlparse(url).path}{RESET}")
            issues_found += 1
    else:
        print(f"{GREEN}✓ No duplicate titles found{RESET}")
    
    print_subheader("Duplicate Descriptions")
    if dup_descs:
        for desc, urls in dup_descs.items():
            print(f"{YELLOW}⚠{RESET} \"{desc[:60]}{'...' if len(desc) > 60 else ''}\"")
            for url in urls:
                print(f"   {DIM}{urlparse(url).path}{RESET}")
            issues_found += 1
    else:
        print(f"{GREEN}✓ No duplicate descriptions found{RESET}")
    
    print(f"\nDuplicate content summary: {YELLOW if issues_found else GREEN}{issues_found} duplicate groups found{RESET}")


def audit_mixed_content():
    """Check for HTTP resources on HTTPS pages (mixed content)."""
    print_header("14. Mixed Content Audit")
    
    pages_with_mixed = []
    
    for url, data in PAGE_DATA.items():
        if data['status_code'] != 200:
            continue
        
        if data.get('mixed_content'):
            pages_with_mixed.append((url, data['mixed_content']))
    
    if pages_with_mixed:
        print(f"{RED}✗ {len(pages_with_mixed)} pages with mixed content:{RESET}")
        for url, resources in pages_with_mixed[:10]:
            print(f"\n   {urlparse(url).path}")
            for resource in resources[:5]:
                print(f"   {DIM}→ {resource[:80]}{RESET}")
            if len(resources) > 5:
                print(f"   {DIM}... and {len(resources) - 5} more{RESET}")
        mark_failure()
    else:
        print(f"{GREEN}✓ No mixed content found - all resources use HTTPS{RESET}")


def audit_soft_404():
    """Detect pages that return 200 but appear to be error pages."""
    print_header("15. Soft 404 Detection")
    
    soft_404_pages = []
    
    for url, data in PAGE_DATA.items():
        if data['status_code'] != 200:
            continue
        
        # v7.2.1: skip pages where soft 404 patterns are expected content
        if urlparse(url).path in SOFT_404_EXEMPT_PATHS:
            continue
        
        if data.get('is_soft_404'):
            reason = data.get('soft_404_reason', 'unknown pattern')
            soft_404_pages.append((url, reason))
    
    if soft_404_pages:
        print(f"{YELLOW}⚠ {len(soft_404_pages)} potential soft 404 pages detected:{RESET}")
        for url, reason in soft_404_pages[:10]:
            print(f"   {urlparse(url).path}")
            print(f"   {DIM}Matched: '{reason}'{RESET}")
        if len(soft_404_pages) > 10:
            print(f"   {DIM}... and {len(soft_404_pages) - 10} more{RESET}")
        print(f"\n   {DIM}Review these pages - they return 200 but may contain error-like content{RESET}")
        print(f"   {DIM}(This is a warning, not a failure - may be false positives on technical content){RESET}")
    else:
        print(f"{GREEN}✓ No soft 404 pages detected{RESET}")


def audit_internal_links(domain):
    """Check all internal links resolve (broken link detection)."""
    print_header("16. Internal Broken Link Audit")
    
    # Collect all internal links
    all_internal_links = set()
    link_sources = defaultdict(list)  # link -> [pages that contain it]
    
    for url, data in PAGE_DATA.items():
        if data['status_code'] != 200:
            continue
        
        for link in data.get('internal_links', []):
            all_internal_links.add(link)
            link_sources[link].append(url)
    
    # Filter to links not in our PAGE_DATA (not already checked)
    sitemap_urls = set(DYNAMIC_URLS)
    links_to_check = all_internal_links - sitemap_urls
    
    print(f"Found {len(all_internal_links)} unique internal links")
    print(f"Checking {len(links_to_check)} links not in sitemap...")
    
    broken_links = []
    
    def check_link(link):
        try:
            r = requests.head(link, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
            return (link, r.status_code)
        except Exception as e:
            return (link, f"Error: {str(e)[:30]}")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(check_link, links_to_check))
    
    for link, status in results:
        if isinstance(status, str) or status >= 400:
            broken_links.append((link, status, link_sources[link]))
    
    if broken_links:
        print(f"\n{RED}✗ {len(broken_links)} broken internal links found:{RESET}")
        for link, status, sources in broken_links[:15]:
            print(f"\n   {RED}{status}{RESET} {urlparse(link).path}")
            print(f"   {DIM}Linked from:{RESET}")
            for source in sources[:3]:
                print(f"      {DIM}{urlparse(source).path}{RESET}")
            if len(sources) > 3:
                print(f"      {DIM}... and {len(sources) - 3} more pages{RESET}")
        if len(broken_links) > 15:
            print(f"\n   {DIM}... and {len(broken_links) - 15} more broken links{RESET}")
        mark_failure()
    else:
        print(f"{GREEN}✓ No broken internal links found{RESET}")


def audit_orphan_pages(domain):
    """Find pages in sitemap that aren't linked from anywhere."""
    print_header("17. Orphan Page Detection")
    
    # Collect all internal links
    all_linked_pages = set()
    
    for url, data in PAGE_DATA.items():
        if data['status_code'] != 200:
            continue
        
        for link in data.get('internal_links', []):
            # Normalize the link
            parsed = urlparse(link)
            normalized = f"https://{domain}{parsed.path}"
            if not normalized.endswith('/') and '.' not in parsed.path.split('/')[-1]:
                normalized += '/'
            all_linked_pages.add(normalized)
    
    # Find sitemap URLs not linked from anywhere
    sitemap_urls = set(DYNAMIC_URLS)
    orphan_pages = sitemap_urls - all_linked_pages
    
    # Remove homepage (it's often linked differently)
    homepage = f"https://{domain}/"
    orphan_pages.discard(homepage)
    
    if orphan_pages:
        print(f"{YELLOW}⚠ {len(orphan_pages)} orphan pages (in sitemap but not linked):{RESET}")
        for url in sorted(orphan_pages)[:15]:
            print(f"   {urlparse(url).path}")
        if len(orphan_pages) > 15:
            print(f"   {DIM}... and {len(orphan_pages) - 15} more{RESET}")
        print(f"\n   {DIM}Consider adding internal links to these pages{RESET}")
    else:
        print(f"{GREEN}✓ All sitemap pages are internally linked{RESET}")


def check_files(domain):
    """Check availability of critical files."""
    print_header("18. File Checks (Robots.txt & LLMs.txt)")
    
    files = ["robots.txt", "llms.txt", "llms-full.txt"]
    for filename in files:
        url = f"https://{domain}/{filename}"
        print(f"{filename} -> ", end="", flush=True)
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                ct = r.headers.get("Content-Type", "unknown").split(";")[0]
                print(f"{GREEN}✓ 200{RESET} ({ct})")
            else:
                print(f"{RED}✗ {r.status_code}{RESET}")
                if filename == "robots.txt":
                    mark_failure()
        except Exception as e:
            print(f"{RED}✗ Error: {e}{RESET}")
            mark_failure()


def check_schema(domain):
    """Validate JSON-LD structured data on the homepage."""
    print_header("19. JSON-LD Schema Validation")
    
    homepage = f"https://{domain}/"
    data = PAGE_DATA.get(homepage, {})
    
    if not data.get('html'):
        print(f"{RED}✗ Could not analyze homepage{RESET}")
        return
    
    soup = BeautifulSoup(data['html'], 'html.parser')
    schemas = soup.find_all('script', type='application/ld+json')
    
    if schemas:
        print(f"{GREEN}✓{RESET} Found {len(schemas)} JSON-LD schema block(s)")
        for i, s in enumerate(schemas):
            raw_json = s.get_text(strip=True)
            try:
                schema_data = json.loads(raw_json)
                
                def print_types(obj, indent=3):
                    if isinstance(obj, list):
                        for item in obj:
                            print_types(item, indent)
                    elif isinstance(obj, dict):
                        if '@type' in obj:
                            t = obj.get('@type')
                            print(f"{' ' * indent}- {BOLD}{t}{RESET}")
                        for key, value in obj.items():
                            if isinstance(value, (dict, list)):
                                print_types(value, indent + 2)
                
                print_types(schema_data)
            except json.JSONDecodeError as e:
                print(f"   {RED}✗ JSON Parse Error: {e}{RESET}")
                mark_failure()
    else:
        print(f"{YELLOW}⚠{RESET} No JSON-LD schema found on homepage")


# =============================================================================
# v7.3: Image-Asset Redirect, Redirect Set, and Orphan Image Audits
# =============================================================================

def _discover_redirects(nginx_config_path):
    """
    Return (redirects, source_label) where redirects is a list of
    (source, target, status) tuples and source_label describes where
    they came from (for output). Falls back to KNOWN_REDIRECTS if the
    nginx config is unreadable.
    """
    parsed = parse_nginx_redirects(nginx_config_path)
    if parsed is None:
        return KNOWN_REDIRECTS, f"inline fallback ({nginx_config_path} unreadable)"
    return parsed, nginx_config_path


def _verify_redirect(domain, source, target, expected_status):
    """
    Verify a single redirect rule.

    Returns a dict:
      ok:           bool, True if rule is healthy
      severity:     'fail' | 'warn' | 'ok'
      message:      str, human-readable result
      chain_label:  str, e.g. "301 -> /images/foo.svg -> 200"
    """
    source_url = f"https://{domain}{source}"
    result = follow_redirects(source_url)
    chain = result['chain']

    if result['error'] and not chain:
        return {
            'ok': False,
            'severity': 'fail',
            'message': f"Error fetching source: {result['error']}",
            'chain_label': 'ERROR',
        }

    # 410 Gone case — no redirect target, the source itself returns 410.
    if expected_status == 410:
        if len(chain) == 1 and chain[0][1] == 410:
            return {
                'ok': True,
                'severity': 'ok',
                'message': f"{source} -> 410 Gone",
                'chain_label': '410',
            }
        actual = chain[0][1] if chain else '?'
        return {
            'ok': False,
            'severity': 'fail',
            'message': f"{source} -> expected 410, got {actual}",
            'chain_label': str(actual),
        }

    # 301/302/etc. cases — source must redirect, target must return 200.
    if len(chain) < 2:
        actual = chain[0][1] if chain else '?'
        return {
            'ok': False,
            'severity': 'fail',
            'message': f"{source} -> expected {expected_status}, got {actual} (no redirect)",
            'chain_label': str(actual),
        }

    source_status = chain[0][1]
    final_status = result['final_status']
    final_path = urlparse(result['final_url']).path

    # Chain length is the number of hops including source. A clean redirect
    # is exactly 2 entries: the source (with its 3xx) and the final target.
    if len(chain) > 2:
        chain_str = " -> ".join(str(c[1]) for c in chain)
        return {
            'ok': False,
            'severity': 'fail',
            'message': f"{source} -> CHAIN: {chain_str} (should be single hop)",
            'chain_label': chain_str,
        }

    if source_status != expected_status:
        # 302 where 301 expected is a warning, not a hard failure
        if source_status == 302 and expected_status == 301:
            return {
                'ok': False,
                'severity': 'warn',
                'message': f"{source} -> 302 -> {final_path} (should be 301)",
                'chain_label': f"302 -> {final_path} -> {final_status}",
            }
        return {
            'ok': False,
            'severity': 'fail',
            'message': f"{source} -> expected {expected_status}, got {source_status}",
            'chain_label': f"{source_status}",
        }

    if final_status != 200:
        return {
            'ok': False,
            'severity': 'fail',
            'message': f"{source} -> {source_status} -> {target} -> {final_status} (target broken)",
            'chain_label': f"{source_status} -> {target} -> {final_status}",
        }

    if final_path != target:
        # Target resolved to a different path than declared — config drift
        return {
            'ok': False,
            'severity': 'warn',
            'message': f"{source} -> {source_status} -> {final_path} (declared target was {target})",
            'chain_label': f"{source_status} -> {final_path} -> {final_status}",
        }

    return {
        'ok': True,
        'severity': 'ok',
        'message': f"{source} -> {source_status} -> {target} -> {final_status}",
        'chain_label': f"{source_status} -> {target} -> {final_status}",
    }


def audit_image_redirects(domain, nginx_config_path, strict=False):
    """
    Section 20: verify every image-asset redirect rule resolves cleanly,
    and cross-reference against rendered HTML to flag pages still using
    legacy slugs.
    """
    print_header("20. Image-Asset Redirect Audit")

    redirects, source_label = _discover_redirects(nginx_config_path)
    image_redirects = [
        (src, tgt, status) for (src, tgt, status) in redirects
        if is_image_path(src) and status != 410
    ]

    if not image_redirects:
        print(f"{YELLOW}No image redirect rules discovered (source: {source_label}).{RESET}")
        print(f"\nImage-asset redirect summary: {GREEN}0 issues found{RESET}")
        return

    if source_label != nginx_config_path:
        print(f"{YELLOW}⚠ Using {source_label}.{RESET}")
    print(f"Discovered {len(image_redirects)} image redirect rule(s) from {source_label}. Verifying...")

    issues = 0
    legacy_sources = {src for (src, _, _) in image_redirects}

    for source, target, status in image_redirects:
        result = _verify_redirect(domain, source, target, status)
        if result['severity'] == 'ok':
            print(f"{GREEN}✓{RESET} {source} -> {result['chain_label']}")
        elif result['severity'] == 'warn':
            print(f"{YELLOW}⚠{RESET} {result['message']}")
            issues += 1
        else:
            print(f"{RED}✗{RESET} {result['message']}")
            print(f"   {CYAN}└─ Redirect target is broken. Either restore the target or remove the redirect.{RESET}")
            mark_failure()
            issues += 1

    # Cross-reference: which rendered pages still reference legacy slugs?
    # This is INFO-level — the redirect catches it for visitors, but the
    # site should be updated to the canonical path.
    pages_with_legacy_refs = []
    for page_url, data in PAGE_DATA.items():
        if not data:
            continue
        legacy_refs_on_page = [
            ref for ref in data.get('image_sources', [])
            if ref in legacy_sources
        ]
        if legacy_refs_on_page:
            pages_with_legacy_refs.append((page_url, legacy_refs_on_page))

    if pages_with_legacy_refs:
        print(f"\n{CYAN}[INFO]{RESET} {len(pages_with_legacy_refs)} page(s) reference legacy image slugs (served via 301):")
        for page_url, refs in pages_with_legacy_refs:
            page_path = page_url.replace(f"https://{domain}", "")
            for ref in refs:
                print(f"  - {page_path} references {ref}")
        print(f"   {DIM}Consider updating the page source to use the canonical path.{RESET}")

    print(f"\nImage-asset redirect summary: {RED if issues else GREEN}{issues} issue{'s' if issues != 1 else ''} found{RESET}")


def audit_redirect_set(domain, nginx_config_path):
    """
    Section 21: verify every exact-match nginx redirect (image and non-image)
    resolves cleanly. Catches drift across the full redirect set, not just
    images.
    """
    print_header("21. Redirect Set Verification")

    redirects, source_label = _discover_redirects(nginx_config_path)

    if not redirects:
        print(f"{YELLOW}No redirect rules discovered (source: {source_label}).{RESET}")
        print(f"\nRedirect set summary: {GREEN}0 issues found{RESET}")
        return

    if source_label != nginx_config_path:
        print(f"{YELLOW}⚠ Using {source_label}. Non-image rules cannot be verified from the fallback list.{RESET}")
    print(f"Discovered {len(redirects)} exact-match redirect rule(s) from {source_label}. Verifying...")

    issues = 0
    for source, target, status in redirects:
        result = _verify_redirect(domain, source, target, status)
        target_label = target if target else "410"
        if result['severity'] == 'ok':
            print(f"{GREEN}✓{RESET} {source} -> {result['chain_label']}")
        elif result['severity'] == 'warn':
            print(f"{YELLOW}⚠{RESET} {result['message']}")
            issues += 1
        else:
            print(f"{RED}✗{RESET} {result['message']}")
            mark_failure()
            issues += 1

    print(f"\nRedirect set summary: {RED if issues else GREEN}{issues} issue{'s' if issues != 1 else ''} found{RESET}")


def audit_orphan_images(domain, dist_path, nginx_config_path, strict=False):
    """
    Section 22: walk dist/images/ and flag any asset never referenced from
    rendered HTML or from a redirect target. INFO by default; --strict
    escalates to failure.
    """
    print_header("22. Orphan Image Audit")

    dist = Path(dist_path)
    if not dist.is_dir():
        print(f"{YELLOW}⚠ dist path '{dist_path}' not found or not a directory. Skipping.{RESET}")
        print(f"   {DIM}Use --dist-path to point at the built site root.{RESET}")
        print(f"\nOrphan image summary: {GREEN}0 issues found{RESET}")
        return

    # Walk dist/images/ (and any additional dirs in IMAGE_DIRS) for assets.
    on_disk = set()
    for subdir in IMAGE_DIRS:
        scan_root = dist / subdir
        if not scan_root.is_dir():
            continue
        for path in scan_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in IMAGE_EXTENSIONS:
                # Store as the URL path the asset would serve under
                rel = path.relative_to(dist).as_posix()
                on_disk.add("/" + rel)

    if not on_disk:
        print(f"{YELLOW}No image assets found under {dist_path}/{{{','.join(IMAGE_DIRS)}}}.{RESET}")
        print(f"\nOrphan image summary: {GREEN}0 issues found{RESET}")
        return

    # Build the set of referenced image paths from PAGE_DATA + og:image + redirect targets.
    referenced = set()
    for data in PAGE_DATA.values():
        if not data:
            continue
        for ref in data.get('image_sources', []):
            referenced.add(ref)
        # og:image references too — these are not <img> tags but they are
        # legitimate references to image assets.
        og_image = data.get('og_image')
        if og_image:
            parsed_og = urlparse(urljoin(f"https://{domain}/", og_image))
            if (not parsed_og.netloc or parsed_og.netloc == domain) and is_image_path(parsed_og.path):
                referenced.add(parsed_og.path)
        # Favicon
        favicon = data.get('favicon')
        if favicon:
            parsed_fav = urlparse(urljoin(f"https://{domain}/", favicon))
            if (not parsed_fav.netloc or parsed_fav.netloc == domain) and is_image_path(parsed_fav.path):
                referenced.add(parsed_fav.path)

    # Redirect targets also count as "referenced" — a redirect points at a
    # canonical asset that should be retained even if no page links to it yet.
    redirects, _ = _discover_redirects(nginx_config_path)
    for source, target, status in redirects:
        if target and is_image_path(target):
            referenced.add(target)

    orphans = sorted(on_disk - referenced)

    print(f"{len(on_disk)} image asset(s) in {dist_path}, {len(on_disk - set(orphans))} referenced from rendered HTML or redirect targets.")

    if not orphans:
        print(f"\nOrphan image summary: {GREEN}0 issues found{RESET}")
        return

    label = f"{RED}[FAIL]{RESET}" if strict else f"{CYAN}[INFO]{RESET}"
    print(f"\n{label} {len(orphans)} orphan image{'s' if len(orphans) != 1 else ''}:")
    for orphan in orphans:
        print(f"  - {dist_path}{orphan} (no HTML references found)")

    if strict:
        mark_failure()
        print(f"\nOrphan image summary: {RED}{len(orphans)} failure{'s' if len(orphans) != 1 else ''} (strict mode){RESET}")
    else:
        print(f"\nOrphan image summary: {YELLOW}{len(orphans)} INFO finding{'s' if len(orphans) != 1 else ''} (use --strict to fail){RESET}")


# =============================================================================
# Helper Functions
# =============================================================================

def follow_redirects(url, max_redirects=10):
    """Follow redirects manually to capture the full chain."""
    chain = []
    current_url = url
    
    for _ in range(max_redirects):
        try:
            r = requests.get(
                current_url,
                headers=HEADERS,
                timeout=TIMEOUT,
                allow_redirects=False
            )
            chain.append((current_url, r.status_code))
            
            if r.status_code in (301, 302, 303, 307, 308):
                location = r.headers.get('Location')
                if not location:
                    return {
                        'chain': chain,
                        'final_url': current_url,
                        'final_status': r.status_code,
                        'error': 'Redirect without Location header'
                    }
                current_url = urljoin(current_url, location)
            else:
                return {
                    'chain': chain,
                    'final_url': current_url,
                    'final_status': r.status_code,
                    'error': None
                }
        except Exception as e:
            return {
                'chain': chain,
                'final_url': current_url,
                'final_status': None,
                'error': str(e)
            }
    
    return {
        'chain': chain,
        'final_url': current_url,
        'final_status': None,
        'error': f'Too many redirects (>{max_redirects})'
    }


# =============================================================================
# CLI
# =============================================================================

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="SEO Validator - Comprehensive site audit for CI/CD pipelines",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --domain example.com                    # Quick audit (sampling)
  %(prog)s --domain example.com --full             # Full audit (all pages)
  %(prog)s --domain example.com --full --thorough  # Full + link checking
  %(prog)s --domain example.com --full --strict    # Full + fail on length issues

Exit codes:
  0  All checks passed
  1  One or more critical failures detected
        """
    )
    parser.add_argument(
        "-d", "--domain",
        type=str,
        required=True,
        help="Domain to audit (e.g., example.com)"
    )
    parser.add_argument(
        "-s", "--sitemap",
        type=str,
        default=None,
        help="Custom sitemap URL (default: https://<domain>/sitemap-0.xml)"
    )
    parser.add_argument(
        "-t", "--timeout",
        type=int,
        default=10,
        help="Request timeout in seconds (default: 10)"
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=10,
        help="Max concurrent workers for URL checks (default: 10)"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Test ALL pages instead of sampling (slower but comprehensive)"
    )
    parser.add_argument(
        "--thorough",
        action="store_true",
        help="Include broken link and orphan page checks (requires additional HTTP requests)"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat title/description length issues as failures (default: warnings only)"
    )
    parser.add_argument(
        "--skip-og",
        action="store_true",
        help="Skip Open Graph tag audit"
    )
    parser.add_argument(
        "--skip-images",
        action="store_true",
        help="Skip image alt text audit"
    )
    parser.add_argument(
        "--skip-image-redirects",
        action="store_true",
        help="Skip the image-asset redirect audit (section 20)"
    )
    parser.add_argument(
        "--skip-redirect-set",
        action="store_true",
        help="Skip the generic redirect-set verification (section 21)"
    )
    parser.add_argument(
        "--skip-orphan-images",
        action="store_true",
        help="Skip the orphan image audit (section 22)"
    )
    parser.add_argument(
        "--nginx-config",
        type=str,
        default=DEFAULT_NGINX_CONFIG,
        help=f"Path to nginx config for redirect discovery (default: {DEFAULT_NGINX_CONFIG})"
    )
    parser.add_argument(
        "--dist-path",
        type=str,
        default="dist",
        help="Path to the built static site root for the orphan image audit (default: ./dist)"
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}"
    )
    return parser.parse_args()


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    args = parse_args()
    
    domain = args.domain
    sitemap_url = args.sitemap if args.sitemap else f"https://{domain}/sitemap-0.xml"
    TIMEOUT = args.timeout
    
    mode_parts = []
    if args.full:
        mode_parts.append("Full")
    else:
        mode_parts.append("Sampling")
    if args.thorough:
        mode_parts.append("Thorough")
    if args.strict:
        mode_parts.append("Strict")
    mode = " + ".join(mode_parts)
    
    print("=" * 60)
    print(f"SEO Validator v{__version__} - Comprehensive Site Audit")
    print(f"Domain: {domain}")
    print(f"Mode: {mode}")
    print("=" * 60)
    
    # 1. Fetch sitemap
    DYNAMIC_URLS = fetch_sitemap_urls(sitemap_url)
    
    if not DYNAMIC_URLS:
        print(f"\n{RED}No URLs found. Exiting.{RESET}")
        sys.exit(1)
    
    # 2. Collect all page data in single pass
    print(f"\nCollecting page data for {len(DYNAMIC_URLS)} URLs...")
    collect_all_page_data(DYNAMIC_URLS, max_workers=args.workers)
    print(f"Collected data for {len(PAGE_DATA)} pages.")
    
    # 3. Run audits
    audit_basic_seo()
    audit_title_description_length(strict=args.strict)
    audit_meta_robots()
    
    if not args.skip_og:
        audit_open_graph()
    
    audit_viewport()
    audit_favicon(domain)
    audit_heading_hierarchy()
    
    if not args.skip_images:
        audit_image_alt_text()
    
    audit_canonicals(domain)
    audit_trailing_slash(domain, full_audit=args.full)
    audit_redirect_chains(domain, full_audit=args.full)
    audit_duplicates()
    audit_mixed_content()
    audit_soft_404()
    
    # Thorough checks (additional HTTP requests)
    if args.thorough:
        audit_internal_links(domain)
        audit_orphan_pages(domain)
    else:
        print_header("16-17. Link Audits (Skipped)")
        print(f"{DIM}Use --thorough to enable broken link and orphan page detection{RESET}")
    
    check_files(domain)
    check_schema(domain)
    
    # v7.3: asset-redirect and orphan-image audits
    if not args.skip_image_redirects:
        audit_image_redirects(domain, args.nginx_config, strict=args.strict)
    
    if not args.skip_redirect_set:
        audit_redirect_set(domain, args.nginx_config)
    
    if not args.skip_orphan_images:
        audit_orphan_images(domain, args.dist_path, args.nginx_config, strict=args.strict)
    
    # Final summary
    print("\n" + "=" * 60)
    if ANY_FAILURE:
        print(f"{RED}AUDIT FAILED. See errors above.{RESET}")
        sys.exit(1)
    else:
        print(f"{GREEN}AUDIT PASSED. All systems nominal.{RESET}")
        sys.exit(0)
