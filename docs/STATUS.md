# Build status

Resume from here after any interruption. Don't redo ticked items.

| step | what | status |
|---|---|---|
| 0 | GitHub repo attached (`abhijithcw/sebi-enforcement-decoder`) | ✅ 10 Nov 2026 |
| 1 | Extraction schema: `sql/schema.sql`, `src/sed/schema.py`, `prompts/extract_v1.md`, `amounts.py`, `entity.py`, `db.py`, `config.py`, tests (21 pass) | ✅ 10 Nov 2026 — awaiting user review before scale |
| 2 | Scraper (`src/sed/scrape.py`): listing POST/AJAX pagination → item HTML → iframe PDF; Hindi skip; http cache; throttle/backoff | ⬜ |
| 3 | PDF text (`src/sed/pdftext.py`): pypdfium2 page-by-page → `order_pages` + FTS; scanned detection → OCR queue | ⬜ |
| 4 | LLM extraction (`src/sed/extract.py`): section selection, cache, pydantic validation, cost estimate + cap | ⬜ |
| 5 | Storage: map `ExtractedOrder` → tables, entity resolution/merge | ⬜ |
| 6 | Site (`src/sed/site.py` + `web/`): static mobile-first HTML, JSON index, MiniSearch | ⬜ |
| — | Pi: confirm HDD mount path, set `data_root`, install cron | ⬜ blocked on Pi |

## Open decisions for Abhijith

1. **Pre-2015 backfill** — count + cost will be shown after step 2 runs the listing once.
2. **Appeal data source** — SAT orders live on sat.gov.in (not SEBI). Option A: only record appeals SEBI itself mentions (cheap, incomplete). Option B: add a second scraper for SAT. Schema supports both; default is A.
3. **Model for extraction** — config says `claude-sonnet-5-5`; Haiku 5.5 may be enough for simple adjudication orders. Decide after the cost estimate in step 4.
4. **Pricing** — `llm.price_usd_per_mtok` is deliberately `null`; fill from docs.claude.com before the first estimate.

## Conventions

* Code `~/projects/sebi-enforcement-decoder`, data `/mnt/hdd/sebi-enforcement-decoder` (placeholder), override with `SED_DATA_ROOT`.
* One commit per step. Tests must pass before commit.
* Natural keys: `orders.order_id = sha1(html_url)[:16]`; entity ids `cin:`/`isin:`/`pan:`/`name:`.
