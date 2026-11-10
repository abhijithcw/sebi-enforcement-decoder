# sebi-enforcement-decoder

A searchable, phone-readable database of **SEBI enforcement and adjudication orders**, mapped to the
companies, promoters, directors and intermediaries named in them. Runs unattended on a
Raspberry Pi 5 (4 GB RAM, 1 TB HDD); the output is a static site you can open on a phone.

> Indicators derived from public filings; not an allegation, investment advice, or legal opinion.

## What it does

```
sebi.gov.in → Enforcement → Orders (all sub-categories)
   │  throttled, cached, resumable scraper                       (step 2)
   ▼
PDF per order → page-wise text (pypdfium2) → FTS5                (step 3)
   │  scanned PDFs detected → low-priority tesseract OCR queue
   ▼
Relevant sections → Claude (JSON, pydantic-validated, evidence-backed)   (step 4)
   │  every value carries page number + verbatim snippet; cached by (sha256, prompt, model)
   ▼
SQLite (WAL) — orders · noticees · penalties · violations · entities · appeals   (step 5)
   │
   ▼
Static mobile-first HTML + compact JSON index, fuzzy search in the browser   (step 6)
```

## Data model (step 1 — done)

| table | one row per | key points |
|---|---|---|
| `orders` | SEBI order (English copy) | `order_type` interim / confirmatory / final / settlement / adjudication / revocation / other; `is_ex_parte`; `appeal_status`; fetch + extraction status; model + prompt version |
| `noticees` | noticee × order | `role` company / promoter / director / kmp / broker / other; `outcome` penalty / directions / disgorgement / warning / disposed / settled; page + snippet evidence |
| `penalties` | amount per noticee | integer ₹ parsed from Indian notation, `joint_and_several` + `joint_group`, statutory provision |
| `violations` | regulation + clause | per noticee or order-wide; `finding` alleged / established / not_established / settled |
| `entities` | resolved person/company | canonical id `cin:` > `isin:` > `pan:` > `name:`; PAN stored, **masked in UI** |
| `entity_aliases` | spelling seen | feeds fuzzy matching |
| `appeals` | SAT / SC / HC event | so set-aside orders are struck through and linked |
| `order_pages` + `orders_fts` | page text | FTS5 full text; LLM gets sections, never whole PDFs |
| `llm_cache`, `llm_spend` | LLM call / month | cost in ₹, monthly cap from `config.yaml` |

Full DDL: [`sql/schema.sql`](sql/schema.sql). LLM output contract: [`src/sed/schema.py`](src/sed/schema.py).
Prompt: [`prompts/extract_v1.md`](prompts/extract_v1.md).

## Display rules (legal)

* Settlement orders: **"Settled without admission or denial of guilt."**
* Interim / ex-parte orders: **"Interim — not a final finding."**
* Orders set aside by SAT: shown struck through, linked to the SAT order.
* No entity is ever labelled a "violator" — the UI shows the order list with outcomes.
* PANs masked (`ABC******F`).

## Engineering constraints

* Peak RSS < 1 GB: PDFs parsed page-by-page with `pypdfium2` (not PyMuPDF); no pandas on full datasets.
* HTTP: ≤ 1 request / 2.5 s per host, browser-like UA, exponential backoff on 403/429/5xx (5 tries) then log + skip; every raw response cached on disk; checkpoint after every unit of work.
* SQLite: `journal_mode=WAL`, `busy_timeout=30000`, one writer, batched idempotent upserts on natural keys, every dashboard query indexed.
* LLM: Anthropic API, key in `.env`; only relevant sections sent; JSON forced and validated; retry once then `needs_review`; token + ₹ estimate shown before any bulk run.
* Cron: `deploy/run.sh` under `flock -n`, proposed 21:30 IST daily.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env            # add ANTHROPIC_API_KEY
export SED_DATA_ROOT=/tmp/sed   # anywhere with space; on the Pi this is the HDD
.venv/bin/sed init-db
.venv/bin/pytest
```

Pi deployment: [`deploy/install.md`](deploy/install.md). Build progress: [`docs/STATUS.md`](docs/STATUS.md).

## Licence

MIT. Source data © SEBI, used as public records.
