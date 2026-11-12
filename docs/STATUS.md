# Build status

Resume from here after any interruption. Don't redo ticked items.

| step | what | status |
|---|---|---|
| 0 | GitHub repo attached (`abhijithcw/sebi-enforcement-decoder`) | ✅ 10 Nov 2026 |
| 1 | Extraction schema: `sql/schema.sql`, `src/sed/schema.py`, `prompts/extract_v1.md`, `amounts.py`, `entity.py`, `db.py`, `config.py`, tests (21 pass) | ✅ 10 Nov 2026 — awaiting user review before scale |
| 2 | Scraper (`src/sed/http.py`, `scrape.py`, `sed scrape`/`sed count`): AJAX listing → item HTML → iframe PDF; checkpoints; fixture tests (25 pass). **Not yet run live** — sandbox can't reach sebi.gov.in; first live run on the Pi (`sed count`, then `sed scrape --max-pages 2`) | ✅ code · ⏳ live smoke test |
| 3 | PDF text (`src/sed/pdftext.py`, `ocr.py`; `sed extract-text` / `sed ocr` / `sed fts`): pypdfium2 page-by-page → `order_pages` + FTS5; scanned detection → OCR queue; 30 tests incl. OCR round-trip + memory check | ✅ 12 Nov 2026 |
| 4 | LLM extraction (`src/sed/extract.py`; `sed estimate` / `sed extract`): section selection, cache by (sha, prompt, model), forced-tool JSON, pydantic + 1 retry, verbatim-snippet verification, ₹ estimate, monthly cap; 35 tests with fake client. **No real API call made yet.** | ✅ 13 Nov 2026 |
| 5 | Storage: map `ExtractedOrder` → tables, entity resolution/merge | ⬜ |
| 6 | Site (`src/sed/site.py` + `web/`): static mobile-first HTML, JSON index, MiniSearch | ⬜ |
| — | Pi: confirm HDD mount path, set `data_root`, install cron | ⬜ blocked on Pi |

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
* Hindi duplicates: no marker found in the samples inspected; heuristic = "hindi"/Devanagari in title or URL. Verify on first live run.
