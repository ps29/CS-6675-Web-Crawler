# Canonical, block-level audits (results, 2026-11-08)

M3 audits compare an exact hash of the re-fetched page with the record's hash. This fails
honest peers whenever a rebuild changes something invisible, such as a build ID, a CSRF token or
a "last updated" line. The fix here (`canonical.py`, `canonical_audit.py`) commits each record to
a versioned list of content blocks and audits block by block:

1. **Canonicalize.** Keep the main content container only (`div[role=main]`, `main`, `article`,
   ...). Drop nav, footer, sidebars, comments, inline scripts and volatile lines ("Last updated
   ...", copyright, "Built with ..."). Emit heading, text, code and link blocks; external script
   paths (query string removed) become link blocks. A PyPI JSON document becomes one block per
   field, without `last_serial` and download counts.
2. **Commit.** Hash each block with `CANON_VERSION`. The record's optional `canon` field holds
   the Merkle root and is signed only when set, so existing record IDs do not change.
3. **Audit.** The verdict is PASS when the roots match. Otherwise the auditor fetches a second
   time, and the verdict is INCONCLUSIVE when its own two fetches disagree or the source signals
   a change. It is FAIL when a *sensitive* block differs (code, link, install command, version
   string, URL, package-metadata field) or when the claim has blocks the page lacks. Any other
   difference, such as a reworded sentence, is INCONCLUSIVE and goes to re-audit.

SimHash (64-bit, accept within 3 bits) is measured only as a baseline.

## Method

`python -m crawl_ledger.audit_eval collect` fetched the 25 seed packages' source pages and up to
3 linked pages per docs site, 3 times (robots.txt, 1 s per host, identifying User-Agent). 104
pages were fetched successfully in all 3 passes: 18 PyPI JSON, 8 Read the Docs, 61 self-hosted
docs and 17 docs.python.org. Pass 0 is the peer's fetch, and passes 1 and 2 are the auditor's two
fetches.

`evaluate` judges an honest claim (pass 0 as fetched) and forged claims (pass 0 with one edit
from `forgery.py`) under each scheme. It does this once on the real fetches and once with
synthetic rebuild noise added to every fetch: a CSRF meta tag, an analytics ID, a build comment,
a "last updated" line inside the main content, a footer and new `?v=` cache busters.

There are two kinds of forgery:

- **Page-scope** (`version`, `href`, ...) edits the first match anywhere in the raw page, head
  and chrome included. `comment` is M3's forgery, an appended HTML comment.
- **Content-scope** (`...@content`) parses the page and makes the edit inside the content the
  canonicalizer keeps. A page counts only if re-serializing it unedited leaves its digest
  unchanged, so a detection is caused by the edit itself.

Raw outputs: `results/m3/audit_eval.json`.

## Results

Honest rows show the false-FAIL rate. Forged rows show the detection (FAIL) rate. The number of
pages each case applies to is in brackets.

| Case | raw (M3) | canon (exact) | blocks (new) | SimHash |
|---|---|---|---|---|
| honest, real drift | 0% (104) | 0% | 0% | 0% |
| honest, real + rebuild noise | **100%** (104) | 0% | 0% | 0% |
| `install@content` | 100% (6) | 100% | 100% | 0% |
| `href@content` | 100% (66) | 100% | 100% | 0% |
| `code@content` | 100% (48) | 100% | 100% | 8% |
| `version@content` | 100% (21) | 100% | 100% | 0% |
| `json_url`, `json_requires` | 100% (18) | 100% | 100% | 0% |
| `script` (injected external script) | 100% (86) | 99-100% | 99-100% | 3-5% |
| `prose@content` (one "not" inserted) | 100% (70) | 100% | 6% FAIL, rest INCONCLUSIVE | 1% |
| `href` (page scope) | 100% (85) | 8% | 8% | 0% |
| `version` (page scope) | 100% (90) | 24% | 24% | 0% |
| `comment` (M3 forgery) | 100% (86) | 0% | 0% | 0% |

The forged rows were the same with and without rebuild noise, except `script` (99% vs. 100%).
No honest claim was INCONCLUSIVE under any scheme.

## What this shows

- **Rebuild noise.** On synthetic rebuild noise, raw hashing failed every honest claim, and the
  block audit failed none. Every sensitive content edit was still detected (install, link, code,
  version, metadata, injected script).
- **SimHash is unsafe as a verdict.** It accepted almost every forgery, a typosquatted install
  command included, because a one-token edit moves very few of the 64 bits.
- **Prose edits go to re-audit.** The block audit treats a negated sentence as INCONCLUSIVE
  rather than FAIL, as designed; the 6% that failed contained a version or URL. Exact canonical
  hashing fails them, but it also fails every honest wording change.
- **Edits outside the kept content are not detected.** This covers a link in the nav or footer,
  a version string in `<head>` or an SVG path, and the M3 appended comment. That is the cost of
  ignoring chrome. A redirected nav link is a real attack the block audit misses, so `raw` stays
  useful as a second signal: a raw mismatch with a canonical match could be logged for review
  rather than failed.
- **The M3 forgery model must change** before `sim.py` can use canonical audits, since its
  forged pages differ only by an appended comment.

## Limits

- **The real drift window was short.** The median gap between the peer's and the auditor's fetch
  was 414 s, and no page's `Last-Modified` or PyPI upload time changed. So the 0% honest
  false-FAIL rate of raw hashing on *real* drift says only that these pages were byte-stable for
  minutes (after M3's Cloudflare nonce removal). The 100% raw false-FAIL rate rests on
  synthetic noise. A longer run, `collect --interval 86400` over 1-2 weeks, is needed to measure
  real rebuild drift and real content changes.
- **The sample is small:** 104 pages from 25 packages, with only 6 pages containing a
  `pip install` command.
- **Extraction depends on the page template.** Main-content extraction relies on common
  documentation templates; a site with none of the selectors falls back to `<body>`.
