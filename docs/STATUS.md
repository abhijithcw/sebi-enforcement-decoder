# Build status

Resume from here after any interruption. Don't redo ticked items.

| step | what | status |
|---|---|---|
| 0 | GitHub repo attached (`abhijithcw/sebi-enforcement-decoder`) | ✅ 10 Nov 2026 |
| 1 | Extraction schema: `sql/schema.sql`, `src/sed/schema.py`, `prompts/extract_v1.md`, `amounts.py`, `entity.py`, `db.py`, `config.py`, tests (21 pass) | ✅ 10 Nov 2026 — awaiting user review before scale |
| 2 | Scraper (`src/sed/http.py`, `scrape.py`, `sedec scrape`/`sedec count`): AJAX listing → item HTML → iframe PDF; checkpoints; fixture tests (25 pass). **Not yet run live** — sandbox can't reach sebi.gov.in; first live run on the Pi (`sedec count`, then `sedec scrape --max-pages 2`) | ✅ code · ⏳ live smoke test |
| 3 | PDF text (`src/sed/pdftext.py`, `ocr.py`; `sedec extract-text` / `sedec ocr` / `sedec fts`): pypdfium2 page-by-page → `order_pages` + FTS5; scanned detection → OCR queue; 30 tests incl. OCR round-trip + memory check | ✅ 12 Nov 2026 |
| 4 | LLM extraction (`src/sed/extract.py`; `sedec estimate` / `sedec extract`): section selection, cache by (sha, prompt, model), forced-tool JSON, pydantic + 1 retry, verbatim-snippet verification, ₹ estimate, monthly cap; 35 tests with fake client. **No real API call made yet.** | ✅ 13 Nov 2026 |
| 5 | Storage (`src/sed/store.py`; `sedec rebuild`): `ExtractedOrder` → orders/entities/noticees/penalties/violations/appeals; cin>isin>pan>name resolution with promotion/merge; idempotent; amounts parsed in Python; 39 tests | ✅ 13 Nov 2026 |
| 6 | Site (`src/sed/site.py`, `web/`; `sedec build-site`): static mobile-first HTML, atomic swap, MiniSearch (vendored, MIT) fuzzy search over compact JSON index; legal rules centralised + tested; verified at 360px in headless Chrome | ✅ 13 Nov 2026 |
| 6b | `sedec run` (`src/sed/pipeline.py`): incremental cron pipeline — last 45 days, ≤40 LLM orders/run, monthly ₹ cap, run logged in `runs`; backfill stays manual | ✅ 13 Nov 2026 |
| — | Pi: confirm HDD mount path, set `data_root`, install cron | ⬜ blocked on Pi |

## First-run checklist on the Pi (in this order)

1. `df -h /mnt/hdd`, set `data_root` (or `SED_DATA_ROOT`), `cp .env.example .env`, add `ANTHROPIC_API_KEY`, `sedec init-db`
2. `sedec count` — confirm ~12,386 orders since 2015
3. `sedec scrape --smid 6 --max-pages 2 --pdf-limit 5` — live smoke test (check Hindi handling + PDF download)
4. `sedec extract-text --limit 5` → `sedec estimate` (real token sizes, Sonnet vs Haiku) → `sedec extract --limit 5` → eyeball results
5. Only then the backfill: `sedec scrape`, `sedec extract-text`, `sedec estimate`, `sedec extract --yes`
6. `sedec build-site`, point Caddy at `<data_root>/site`, install cron (`deploy/install.md`)

## Open decisions for Abhijith

0. ~~Appeals source~~ — decided 10 Nov: SEBI-order-mentions only, no SAT scraper.
1. **Pre-2015 backfill** — count + cost will be shown after step 2 runs the listing once.
2. **Appeal data source** — SAT orders live on sat.gov.in (not SEBI). Option A: only record appeals SEBI itself mentions (cheap, incomplete). Option B: add a second scraper for SAT. Schema supports both; default is A.
3. **Model for extraction** — config says `claude-sonnet-5-5`; Haiku 5.5 may be enough for simple adjudication orders. Decide after the cost estimate in step 4.
4. ~~Pricing~~ — filled in config from the Anthropic pricing page (12 Nov 2026): Sonnet 5.5 $2/$10, Haiku 5.5 $0.10/$0.50 per MTok, Batch API 50% off.

## Conventions

* Code `~/projects/sebi-enforcement-decoder`, data `/mnt/hdd/sebi-enforcement-decoder` (placeholder), override with `SED_DATA_ROOT`.
* One commit per step. Tests must pass before commit.
* Natural keys: `orders.order_id = sha1(html_url)[:16]`; entity ids `cin:`/`isin:`/`pan:`/`name:`.

## SEBI listing contract (inspected 10 Nov 2026)

* `POST /sebiweb/ajax/home/getnewslistinfo.jsp` — `sid=2 ssid=9 smid=<id> doDirect=<0-based page>`, dates `dd-mm-yyyy`, 25 rows/page, response `html#@#breadcrumb`.
* smid: 2 Chair/Members (6,433 total) · 6 AO (12,023) · 3 Settlement/Board (1,981) · 146 Settlement/Tribunal (27) · 133 ED/CGM (447) · 138 Reg 30A (52). SAT (1), Courts (7, 77) deliberately skipped.
* Since 2015-01-01: 3,114 + 7,810 + 961 + 2 + 447 + 52 = **12,386 orders** (≈ 500 listing pages).
* Rows link either to an HTML page (PDF in `<iframe src=".../web/?file=<pdf>">`) or straight to a `.pdf`. "[Corrigendum]" links are nested `<a>` inside the title anchor — stored in `orders.corrigendum_urls`.
* Hindi duplicates: no marker found in the samples inspected. Two guards: (1) listing/URL contains "hindi"/Devanagari → skipped at scrape; (2) after download, PDF text >30% Devanagari → `language='hi'`, `extraction_status='skipped'`, no pages stored, never sent to the LLM. Check `SELECT COUNT(*) FROM orders WHERE language='hi'` after the first live run.

## Site design decisions

* Only `extraction_status='done'` orders are published; `needs_review` stays private until reviewed.
* No aggregate penalty totals anywhere (joint-and-several amounts and SAT set-asides make sums misleading).
* SAT-set-aside orders link to the SAT order only when `appeals.appeal_order_url` is known; with no SAT scraper that is
  currently never, so the link falls back to sat.gov.in. Revisit if a SAT source is ever added.
* Entities merge only on CIN/ISIN/PAN or exact normalised name — two people with the same name and no identifier
  can be wrongly merged; the entity page says "identity matched by name only" in that case.

## Audit 13 Nov 2026 (fixed)

* CLI renamed `sed` → **`sedec`** (a venv script named `sed` shadows GNU sed for every shell script while the venv is active).
* Individuals are never auto-merged by name (only companies are); name-only person pages carry a warning banner.
* Duplicate printed names in one order keep separate rows (`name [noticee N]`); `lastrowid` bug removed.
* `temperature=0` falls back to the model default if a model rejects it.
* Root discovery fails loudly on non-editable installs (use `pip install -e .` or `SED_HOME`).
* Scraper circuit breaker: 2 consecutive listing failures abort the scrape stage; cron still rebuilds the site.
* Dev extras now include reportlab/pillow; `pytest` works without env vars. 50 tests, ruff clean, real-PDF end-to-end test.
* **Unverified until live:** Anthropic tool-use schema acceptance (pydantic `$defs`), real SEBI HTML edge cases, real PDF layouts.
