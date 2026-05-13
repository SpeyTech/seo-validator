# v7.3.0 Verification Recipe (revised)

Run these on Axioma after staging the v7.3.0 changes. Each scenario
confirms a different audit responds correctly. Restore state between
scenarios so the final run leaves the site clean.

## Exit-code capture: read this first

Capturing a piped command's exit code with `$?` reports the **last**
command in the pipeline, not the first. The recipes below capture the
validator's exit code on a non-piped run into a log file, then inspect
the log separately. This is the pattern that matches how CI invokes the
validator in `build.sh` (no pipe, exit code drives rollback).

If you prefer to pipe through `tee` or `grep` for live output, use
`${PIPESTATUS[0]}` in bash (or write to a log file and read the log
afterwards in any shell):

```bash
# bash-specific
python3 scripts/seo_validator.py --domain speytech.com 2>&1 | tee log.txt
echo "Validator exit: ${PIPESTATUS[0]}"   # NOT $?
```

## Setup

```bash
cd /var/www/speytech.com

# Confirm v7.3 is in place
python3 scripts/seo_validator.py --version
# Expected: seo_validator.py 7.3.0
```

## Scenario 1 — clean state baseline

```bash
python3 scripts/seo_validator.py --domain speytech.com > /tmp/v73-clean.log 2>&1
EXIT=$?
echo "Exit code: $EXIT"
# Expected: 0
tail -3 /tmp/v73-clean.log
# Expected last line: "AUDIT PASSED. All systems nominal."
```

Inspect the new sections:

```bash
sed -n '/=== 20\./,/=== 21\./p' /tmp/v73-clean.log
sed -n '/=== 21\./,/=== 22\./p' /tmp/v73-clean.log
sed -n '/=== 22\./,/====/p'     /tmp/v73-clean.log
```

Section 20 should list the 5 image redirects, all green.
Section 21 should list all 17 redirects (16 × 301, 1 × 410), all green.
Section 22 either reports 0 orphans, or lists genuine orphans as INFO
findings. Both are exit-0 outcomes in default mode.

## Scenario 2 — deliberately broken image redirect target

```bash
TARGET="/var/www/speytech.com/dist/images/cryptographic-proof-execution.svg"

# Break it
sudo mv "$TARGET" "${TARGET}.bak"

# Run validator, capture exit code on the non-piped invocation
python3 scripts/seo_validator.py --domain speytech.com > /tmp/v73-broken.log 2>&1
EXIT=$?
echo "Exit code: $EXIT"
# Expected: 1

tail -3 /tmp/v73-broken.log
# Expected last line: "AUDIT FAILED. See errors above."

# Inspect section 20 specifically
sed -n '/=== 20\./,/=== 21\./p' /tmp/v73-broken.log

# Restore
sudo mv "${TARGET}.bak" "$TARGET"

# Confirm clean state restored
python3 scripts/seo_validator.py --domain speytech.com > /tmp/v73-restored.log 2>&1
EXIT=$?
echo "Exit code after restore: $EXIT"
# Expected: 0
```

Expected section 20 output during the broken run:

```
=== 20. Image-Asset Redirect Audit ===
Discovered 5 image redirect rule(s) from /etc/nginx/sites-available/speytech.com. Verifying...
✓ /images/cardiocore-litigation.svg -> 301 -> /images/implantable-device-litigation.svg -> 200
✗ /images/hash-chain-diagram.svg -> 301 -> /images/cryptographic-proof-execution.svg -> 404 (target broken)
   └─ Redirect target is broken. Either restore the target or remove the redirect.
✓ /images/mycoeco-architecture.svg -> 301 -> /images/mycoeco-kernel.svg -> 200
✓ /images/nvidia-asil-comparison.svg -> 301 -> /images/nvidia-asil-determinism.svg -> 200
✓ /images/semantic-security-hero.svg -> 301 -> /images/semantic-security-monitoring.svg -> 200

Image-asset redirect summary: 1 issue found
```

## Scenario 3 — orphan image, default mode (INFO only, exit 0)

```bash
# Add a deliberate orphan
sudo cp /var/www/speytech.com/dist/images/operational-seo-observability.svg \
        /var/www/speytech.com/dist/images/test-orphan-marker.svg

python3 scripts/seo_validator.py --domain speytech.com > /tmp/v73-orphan.log 2>&1
EXIT=$?
echo "Exit code: $EXIT"
# Expected: 0 (INFO doesn't fail in default mode)
# Note: the count of orphans will include the 6 pre-existing orphans
#       on speytech.com plus the test marker = 7. The marker should
#       appear in the listed orphans.

sed -n '/=== 22\./,/====/p' /tmp/v73-orphan.log

# Confirm the test marker is in the listing
grep "test-orphan-marker" /tmp/v73-orphan.log
# Expected: line containing "dist/images/test-orphan-marker.svg"
```

## Scenario 4 — orphan image, --strict (escalates to failure, exit 1)

```bash
# Orphan still present from scenario 3

python3 scripts/seo_validator.py --domain speytech.com --strict > /tmp/v73-strict.log 2>&1
EXIT=$?
echo "Exit code: $EXIT"
# Expected: 1 (--strict escalates orphan INFO to failure)

tail -3 /tmp/v73-strict.log
# Expected last line: "AUDIT FAILED. See errors above."

sed -n '/=== 22\./,/====/p' /tmp/v73-strict.log
# Expected: "[FAIL]" label rather than "[INFO]", and
#           "Orphan image summary: N failures (strict mode)" line

# Cleanup
sudo rm /var/www/speytech.com/dist/images/test-orphan-marker.svg
```

Expected section 22 output under `--strict`:

```
=== 22. Orphan Image Audit ===
68 image asset(s) in dist, 67 referenced from rendered HTML or redirect targets.

[FAIL] 7 orphan images:
  - dist/images/ai-model-serving-patterns.svg (no HTML references found)
  - dist/images/debugging-model-behavior-production.svg (no HTML references found)
  - dist/images/ema-vs-sma-monitoring.svg (no HTML references found)
  - dist/images/floating-point-danger.svg (no HTML references found)
  - dist/images/ml-observability-gap.svg (no HTML references found)
  - dist/images/test-orphan-marker.svg (no HTML references found)
  - dist/images/when-you-dont-need-feature-store.svg (no HTML references found)

Orphan image summary: 7 failures (strict mode)
```

(Adjust the expected count if the six pre-existing orphans have been
addressed before this verification runs.)

## Cleanup

```bash
# Confirm orphan marker is gone
ls /var/www/speytech.com/dist/images/test-orphan-marker.svg 2>/dev/null
# Expected: no output / "No such file or directory"

# Final clean run
python3 scripts/seo_validator.py --domain speytech.com > /tmp/v73-final.log 2>&1
EXIT=$?
echo "Exit code: $EXIT"
tail -3 /tmp/v73-final.log
# Expected: exit 0, "AUDIT PASSED" on the last line
# (Six pre-existing orphans remain as INFO findings; exit code unaffected)
```

## What success looks like

The verification matrix is complete when all four scenarios produce the
expected exit codes:

| Scenario | Mode | State | Expected exit |
|----------|------|-------|---------------|
| 1 | default | clean | 0 |
| 2 | default | broken redirect target | 1 (restored: 0) |
| 3 | default | orphan present | 0 (INFO only) |
| 4 | --strict | orphan present | 1 (escalated) |

The verification recipe is methodologically sound when it captures
`EXIT=$?` immediately after a **non-piped** validator invocation. The
inspection of log contents happens separately and does not affect the
captured exit code.

## Captures for the commit message

Save the failed-state output from scenario 2 (`/tmp/v73-broken.log`)
for inclusion in the v7.3 PR description, as the v7.3 ticket requested.
The discipline of "ship-then-verify" closes the loop on the gap that
motivated this release in the first place.
