-- sebi-enforcement-decoder — SQLite schema v1
-- Applied idempotently by sed.db.init_db(). One writer process; WAL; busy_timeout 30s.
-- Natural keys everywhere so re-runs upsert instead of duplicating.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
  version     INTEGER PRIMARY KEY,
  applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- orders
-- One row per SEBI order (English copy only; Hindi duplicates are skipped).
CREATE TABLE IF NOT EXISTS orders (
  order_id            TEXT PRIMARY KEY,           -- sha1(html_url)[:16]; stable across runs
  sebi_ref            TEXT,                       -- e.g. "WTM/AB/IVD/ID3/27563/2023-24" or "Order/AK/RS/2024-25/30101"
  source_smid         INTEGER,                    -- sebi.gov.in sub-category id (2=Chair/Members, 6=AO, 3/146=Settlement, 133=ED/CGM, 138=Reg 30A)
  corrigendum_urls    TEXT,                       -- JSON list from listing '[Corrigendum]' links
  source_category     TEXT NOT NULL CHECK (source_category IN
                        ('chairman_members','adjudicating_officer','settlement','ed_cgm','other')),
  listing_title       TEXT NOT NULL,              -- title as shown on sebi.gov.in listing
  listing_date        TEXT,                       -- ISO date from listing
  order_date          TEXT,                       -- ISO date printed on the order (LLM/regex), may differ
  issuing_authority   TEXT,                       -- "Whole Time Member", "Adjudicating Officer", "Chief General Manager", ...
  signatory           TEXT,                       -- name of officer, if printed
  order_type          TEXT NOT NULL DEFAULT 'other' CHECK (order_type IN
                        ('interim','confirmatory','final','settlement','adjudication','revocation','other')),
  -- 'interim' covers interim AND ex-parte ad-interim orders; is_ex_parte flags the latter.
  is_ex_parte         INTEGER NOT NULL DEFAULT 0,
  appeal_status       TEXT NOT NULL DEFAULT 'none_known' CHECK (appeal_status IN
                        ('none_known','appealed_to_sat','sat_set_aside','sat_modified','sat_upheld')),
  html_url            TEXT NOT NULL UNIQUE,       -- listing item page (PDF sits in an iframe)
  pdf_url             TEXT,
  pdf_sha256          TEXT,                       -- cache key for LLM + dedupe across categories
  pdf_path            TEXT,                       -- relative to data_root
  page_count          INTEGER,
  has_text_layer      INTEGER,                    -- NULL=unknown, 0=scanned -> OCR queue, 1=text
  ocr_status          TEXT NOT NULL DEFAULT 'not_needed' CHECK (ocr_status IN
                        ('not_needed','queued','done','failed')),
  language            TEXT NOT NULL DEFAULT 'en',
  fetch_status        TEXT NOT NULL DEFAULT 'listed' CHECK (fetch_status IN
                        ('listed','html_fetched','pdf_fetched','text_extracted','fetch_failed')),
  extraction_status   TEXT NOT NULL DEFAULT 'pending' CHECK (extraction_status IN
                        ('pending','done','needs_review','failed','skipped')),
  extraction_model    TEXT,                       -- model name stored with every extracted row
  prompt_version      TEXT,
  extraction_notes    TEXT,                       -- validator / reviewer notes
  first_seen_at       TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_orders_date      ON orders(order_date DESC);
CREATE INDEX IF NOT EXISTS ix_orders_listdate  ON orders(listing_date DESC);
CREATE INDEX IF NOT EXISTS ix_orders_type      ON orders(order_type);
CREATE INDEX IF NOT EXISTS ix_orders_cat       ON orders(source_category);
CREATE INDEX IF NOT EXISTS ix_orders_appeal    ON orders(appeal_status);
CREATE INDEX IF NOT EXISTS ix_orders_extr      ON orders(extraction_status);
CREATE INDEX IF NOT EXISTS ix_orders_sha       ON orders(pdf_sha256);

-- -------------------------------------------------------------- entities
-- Resolved companies / people. Canonical key = CIN or ISIN where determinable,
-- else PAN (if printed), else normalised name. PAN is stored in full and MASKED in the UI.
CREATE TABLE IF NOT EXISTS entities (
  entity_id           TEXT PRIMARY KEY,           -- 'cin:L12345MH...', 'isin:INE...', 'pan:ABCDE1234F', 'name:<normalised>'
  canonical_name      TEXT NOT NULL,
  normalized_name     TEXT NOT NULL,              -- sed.entity.normalise(): upper, strip Ltd/Pvt/punct/extra spaces
  entity_kind         TEXT NOT NULL CHECK (entity_kind IN ('company','individual','other')),
  pan                 TEXT,                       -- only when printed in an order
  cin                 TEXT,
  isin                TEXT,
  din                 TEXT,                       -- directors, when printed
  resolution_method   TEXT,                       -- 'cin_printed','isin_lookup','pan_printed','name_exact','name_fuzzy','manual'
  resolution_conf     REAL,                       -- 0..1
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_entities_norm ON entities(normalized_name);
CREATE INDEX IF NOT EXISTS ix_entities_cin  ON entities(cin);
CREATE INDEX IF NOT EXISTS ix_entities_isin ON entities(isin);
CREATE INDEX IF NOT EXISTS ix_entities_pan  ON entities(pan);

-- Every spelling ever seen for an entity (feeds fuzzy search + resolution).
CREATE TABLE IF NOT EXISTS entity_aliases (
  alias_norm          TEXT NOT NULL,
  entity_id           TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
  alias_raw           TEXT NOT NULL,
  seen_count          INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (alias_norm, entity_id)
);

-- -------------------------------------------------------------- noticees
-- Many-to-many orders <-> entities, one row per noticee per order.
CREATE TABLE IF NOT EXISTS noticees (
  noticee_id          INTEGER PRIMARY KEY,
  order_id            TEXT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
  noticee_no          INTEGER,                    -- "Noticee No. 3" as printed, if any
  name_as_printed     TEXT NOT NULL,
  entity_id           TEXT REFERENCES entities(entity_id),   -- NULL until resolved
  role                TEXT NOT NULL CHECK (role IN
                        ('company','promoter','director','kmp','broker','other')),
  role_detail         TEXT,                       -- "Managing Director", "Compliance Officer", "Stock broker (NSE)"
  outcome             TEXT NOT NULL CHECK (outcome IN
                        ('penalty','directions','disgorgement','warning','disposed','settled','unknown')),
  -- 'directions' = debarment / restraint / cease-and-desist; 'disposed' = exonerated / proceedings dropped
  outcome_detail      TEXT,                       -- free text: "debarred 2 years from securities market"
  debarment_months    INTEGER,                    -- when a period is stated
  page_ref            INTEGER,                    -- evidence: page number
  snippet             TEXT,                       -- evidence: verbatim snippet
  confidence          REAL,
  UNIQUE (order_id, name_as_printed)
);
CREATE INDEX IF NOT EXISTS ix_noticees_order  ON noticees(order_id);
CREATE INDEX IF NOT EXISTS ix_noticees_entity ON noticees(entity_id);
CREATE INDEX IF NOT EXISTS ix_noticees_outcome ON noticees(outcome);

-- ------------------------------------------------------------- penalties
CREATE TABLE IF NOT EXISTS penalties (
  penalty_id          INTEGER PRIMARY KEY,
  noticee_id          INTEGER NOT NULL REFERENCES noticees(noticee_id) ON DELETE CASCADE,
  order_id            TEXT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
  amount_inr          INTEGER NOT NULL,           -- parsed from Indian notation (5,00,000 / 10 lakh / 1.5 crore)
  amount_as_printed   TEXT NOT NULL,              -- verbatim
  kind                TEXT NOT NULL DEFAULT 'penalty' CHECK (kind IN ('penalty','disgorgement','settlement_amount','interest')),
  joint_and_several   INTEGER NOT NULL DEFAULT 0,
  joint_group         TEXT,                       -- label grouping noticees who share one J&S amount, e.g. 'A'
  statutory_provision TEXT,                       -- "Section 15HA, SEBI Act 1992"
  page_ref            INTEGER,
  snippet             TEXT,
  UNIQUE (noticee_id, kind, statutory_provision)
);
CREATE INDEX IF NOT EXISTS ix_penalties_order  ON penalties(order_id);
CREATE INDEX IF NOT EXISTS ix_penalties_amount ON penalties(amount_inr DESC);

-- ------------------------------------------------------------ violations
CREATE TABLE IF NOT EXISTS violations (
  violation_id        INTEGER PRIMARY KEY,
  order_id            TEXT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
  noticee_id          INTEGER REFERENCES noticees(noticee_id) ON DELETE CASCADE,  -- NULL = order-wide
  regulation          TEXT NOT NULL,              -- "SEBI (PIT) Regulations, 2015" / "SEBI Act, 1992" / "PFUTP Regulations, 2003"
  clause              TEXT,                       -- "Regulation 4(1)", "Section 12A(d)", "Reg. 3(a) r/w 4(1)"
  regulation_code     TEXT,                       -- normalised short code: PIT, PFUTP, SAST, LODR, ICDR, SEBI_ACT, SCRA, MB, ...
  finding             TEXT NOT NULL DEFAULT 'alleged' CHECK (finding IN ('alleged','established','not_established','settled')),
  page_ref            INTEGER,
  snippet             TEXT,
  UNIQUE (order_id, noticee_id, regulation, clause)
);
CREATE INDEX IF NOT EXISTS ix_violations_order ON violations(order_id);
CREATE INDEX IF NOT EXISTS ix_violations_code  ON violations(regulation_code);

-- --------------------------------------------------------------- appeals
-- SAT / SC history, so set-aside orders can be struck through and linked.
CREATE TABLE IF NOT EXISTS appeals (
  appeal_id           INTEGER PRIMARY KEY,
  order_id            TEXT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
  forum               TEXT NOT NULL CHECK (forum IN ('SAT','SC','HC')),
  appeal_no           TEXT,
  decided_on          TEXT,
  result              TEXT CHECK (result IN ('pending','set_aside','modified','upheld','remanded','withdrawn')),
  appeal_order_url    TEXT,
  source              TEXT,                       -- 'sat_site','sebi_listing','manual'
  UNIQUE (order_id, forum, appeal_no)
);
CREATE INDEX IF NOT EXISTS ix_appeals_order ON appeals(order_id);

-- ------------------------------------------------------- order_text / FTS
-- Page-wise text so the LLM gets sections, not whole documents.
CREATE TABLE IF NOT EXISTS order_pages (
  order_id            TEXT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
  page_no             INTEGER NOT NULL,
  text                TEXT NOT NULL,
  char_count          INTEGER NOT NULL,
  PRIMARY KEY (order_id, page_no)
);

CREATE VIRTUAL TABLE IF NOT EXISTS orders_fts USING fts5(
  order_id UNINDEXED,
  page_no  UNINDEXED,
  text,
  tokenize = 'porter unicode61'
);

-- ----------------------------------------------------------- llm bookkeeping
CREATE TABLE IF NOT EXISTS llm_cache (
  cache_key           TEXT PRIMARY KEY,           -- sha256(pdf_sha256 + prompt_version + model)
  order_id            TEXT,
  model               TEXT NOT NULL,
  prompt_version      TEXT NOT NULL,
  input_tokens        INTEGER NOT NULL,
  output_tokens       INTEGER NOT NULL,
  cost_inr            REAL NOT NULL,
  response_json       TEXT NOT NULL,
  valid               INTEGER NOT NULL,           -- passed pydantic validation
  created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS llm_spend (
  month               TEXT PRIMARY KEY,           -- 'YYYY-MM'
  input_tokens        INTEGER NOT NULL DEFAULT 0,
  output_tokens       INTEGER NOT NULL DEFAULT 0,
  cost_inr            REAL NOT NULL DEFAULT 0
);

-- ---------------------------------------------------------------- http cache
CREATE TABLE IF NOT EXISTS http_cache (
  url_sha             TEXT PRIMARY KEY,
  url                 TEXT NOT NULL,
  method              TEXT NOT NULL DEFAULT 'GET',
  status              INTEGER,
  path                TEXT NOT NULL,              -- raw response body on disk, relative to data_root
  fetched_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------ checkpoints
-- Scraper progress per (category, window): resume after interruption without redoing pages.
CREATE TABLE IF NOT EXISTS checkpoints (
  name                TEXT PRIMARY KEY,
  value               TEXT NOT NULL,
  updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- --------------------------------------------------------------- run log
CREATE TABLE IF NOT EXISTS runs (
  run_id              INTEGER PRIMARY KEY,
  started_at          TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at         TEXT,
  stage               TEXT NOT NULL,              -- 'scrape','pdf','extract','site'
  ok                  INTEGER,
  stats_json          TEXT
);

INSERT OR IGNORE INTO schema_version(version) VALUES (1);
