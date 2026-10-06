-- Schema for the migration demo. Safe to run repeatedly.
--
--   staging : raw rows and parsed rows from the current run (truncated each run)
--   etl     : reject reasons, per-row rejects and merges, run history
--   core    : the normalized target tables (upserted, so reloads are idempotent)

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS etl;
CREATE SCHEMA IF NOT EXISTS core;

-- ---------------------------------------------------------------- staging --
--
-- Book names use the "C" collation. "First occurrence wins" orders rows by
-- (book, sheet, row), and the default en_US collation ignores punctuation,
-- which would sort purchases_2019_revised.xlsx before purchases_2019.xlsx.

CREATE TABLE IF NOT EXISTS staging.sheets (
    book            text    COLLATE "C" NOT NULL,
    sheet_index     int     NOT NULL,
    sheet           text    NOT NULL,
    domain          text    NOT NULL,
    header_row      int,
    columns         jsonb,
    currency_hint   text,
    skipped_reason  text,
    PRIMARY KEY (book, sheet_index),
    CHECK ((header_row IS NULL) = (skipped_reason IS NOT NULL))
);

-- Every row below a header, exactly as read. This is "rows in".
CREATE TABLE IF NOT EXISTS staging.rows (
    book         text  COLLATE "C" NOT NULL,
    sheet_index  int   NOT NULL,
    row_num      int   NOT NULL,
    sheet        text  NOT NULL,
    domain       text  NOT NULL,
    kind         text  NOT NULL CHECK (kind IN ('data', 'blank', 'subtotal', 'repeated_header')),
    cells        jsonb NOT NULL,
    PRIMARY KEY (book, sheet_index, row_num),
    FOREIGN KEY (book, sheet_index) REFERENCES staging.sheets
);

-- Parsed rows, one table per domain. Same row identity as staging.rows.
CREATE TABLE IF NOT EXISTS staging.product_rows (
    book text COLLATE "C" NOT NULL, sheet_index int NOT NULL, row_num int NOT NULL,
    sku          text NOT NULL,
    description  text NOT NULL,
    category     text NOT NULL,
    uom          text NOT NULL,
    PRIMARY KEY (book, sheet_index, row_num),
    FOREIGN KEY (book, sheet_index, row_num) REFERENCES staging.rows
);

CREATE TABLE IF NOT EXISTS staging.purchase_rows (
    book text COLLATE "C" NOT NULL, sheet_index int NOT NULL, row_num int NOT NULL,
    po_number        text    NOT NULL,
    supplier_raw     text    NOT NULL,
    order_date       date    NOT NULL,
    date_method      text    NOT NULL,
    sku              text,
    description_key  text,
    quantity         numeric NOT NULL,
    unit_price       numeric NOT NULL,
    amount           numeric NOT NULL,
    currency         text    NOT NULL,
    currency_source  text    NOT NULL,
    PRIMARY KEY (book, sheet_index, row_num),
    FOREIGN KEY (book, sheet_index, row_num) REFERENCES staging.rows
);

CREATE TABLE IF NOT EXISTS staging.sale_rows (
    book text COLLATE "C" NOT NULL, sheet_index int NOT NULL, row_num int NOT NULL,
    receipt_no       text    NOT NULL,
    sale_date        date    NOT NULL,
    date_method      text    NOT NULL,
    channel          text    NOT NULL,
    sku              text,
    description_key  text,
    quantity         numeric NOT NULL,
    unit_price       numeric NOT NULL,
    amount           numeric NOT NULL,
    currency         text    NOT NULL,
    currency_source  text    NOT NULL,
    PRIMARY KEY (book, sheet_index, row_num),
    FOREIGN KEY (book, sheet_index, row_num) REFERENCES staging.rows
);

CREATE TABLE IF NOT EXISTS staging.movement_rows (
    book text COLLATE "C" NOT NULL, sheet_index int NOT NULL, row_num int NOT NULL,
    voucher_no       text    NOT NULL,
    movement_date    date    NOT NULL,
    date_method      text    NOT NULL,
    sku              text,
    description_key  text,
    movement_type    text    NOT NULL,
    quantity         numeric NOT NULL,
    reference        text,
    po_number        text,
    PRIMARY KEY (book, sheet_index, row_num),
    FOREIGN KEY (book, sheet_index, row_num) REFERENCES staging.rows
);

CREATE TABLE IF NOT EXISTS staging.payroll_rows (
    book text COLLATE "C" NOT NULL, sheet_index int NOT NULL, row_num int NOT NULL,
    period          date    NOT NULL,
    employee_code   text,
    name_raw        text    NOT NULL,
    name_key        text    NOT NULL,
    position        text,
    gross           numeric NOT NULL,
    deductions      numeric NOT NULL,
    net             numeric NOT NULL,
    PRIMARY KEY (book, sheet_index, row_num),
    FOREIGN KEY (book, sheet_index, row_num) REFERENCES staging.rows
);

-- Output of the supplier matcher (Python), consumed by the SQL load.
CREATE TABLE IF NOT EXISTS staging.supplier_matches (
    alias        text    PRIMARY KEY,
    match_key    text    NOT NULL,
    cluster_id   int     NOT NULL,
    canonical    text    NOT NULL,
    canonical_key text   NOT NULL,
    method       text    NOT NULL,
    score        numeric NOT NULL,
    row_count    int     NOT NULL
);

CREATE TABLE IF NOT EXISTS staging.supplier_review (
    alias             text    PRIMARY KEY,
    nearest_canonical text    NOT NULL,
    score             numeric NOT NULL
);

-- -------------------------------------------------------------------- etl --

CREATE TABLE IF NOT EXISTS etl.reject_reasons (
    code         text PRIMARY KEY,
    category     text NOT NULL CHECK (category IN ('noise', 'data', 'duplicate')),
    stage        text NOT NULL CHECK (stage IN ('extract', 'parse', 'load')),
    description  text NOT NULL
);

-- Rows that were not loaded, with the reason. Layout noise (blank and
-- subtotal rows) is recorded here too, under 'noise' reason codes.
CREATE TABLE IF NOT EXISTS etl.rejects (
    book         text COLLATE "C" NOT NULL,
    sheet_index  int  NOT NULL,
    row_num      int  NOT NULL,
    reason_code  text NOT NULL REFERENCES etl.reject_reasons,
    detail       text,
    PRIMARY KEY (book, sheet_index, row_num),
    FOREIGN KEY (book, sheet_index, row_num) REFERENCES staging.rows
);

-- Exact repeats of a row that was loaded, and the row they were merged into.
CREATE TABLE IF NOT EXISTS etl.merged_rows (
    book              text COLLATE "C" NOT NULL,
    sheet_index       int  NOT NULL,
    row_num           int  NOT NULL,
    kept_book         text COLLATE "C" NOT NULL,
    kept_sheet_index  int  NOT NULL,
    kept_row_num      int  NOT NULL,
    natural_key       text NOT NULL,
    PRIMARY KEY (book, sheet_index, row_num),
    FOREIGN KEY (book, sheet_index, row_num) REFERENCES staging.rows,
    FOREIGN KEY (kept_book, kept_sheet_index, kept_row_num) REFERENCES staging.rows,
    CHECK ((book, sheet_index, row_num) <> (kept_book, kept_sheet_index, kept_row_num))
);

CREATE TABLE IF NOT EXISTS etl.load_runs (
    run_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    workbooks    int,
    rows_in      int,
    changes      jsonb   -- per core table: rows inserted and rows updated by this run
);

-- ------------------------------------------------------------------- core --

-- SYNTHETIC rates from a formula (see erp_migration.config), not market data.
CREATE TABLE IF NOT EXISTS core.fx_rates (
    currency     char(3)       NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    month        date          NOT NULL CHECK (month = date_trunc('month', month)::date),
    rate_to_rwf  numeric(12,4) NOT NULL CHECK (rate_to_rwf > 0),
    source       text          NOT NULL,
    PRIMARY KEY (currency, month),
    CHECK (currency <> 'RWF' OR rate_to_rwf = 1)
);
COMMENT ON TABLE core.fx_rates IS
    'Synthetic demo rates generated by a formula; not central-bank or market data.';

CREATE TABLE IF NOT EXISTS core.products (
    product_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku              text NOT NULL UNIQUE CHECK (sku ~ '^[A-Z]{3}-[A-Z]{3}-[0-9]{3}$'),
    description      text NOT NULL,
    description_key  text GENERATED ALWAYS AS
                         (btrim(regexp_replace(lower(description), '[^a-z0-9]+', ' ', 'g'))) STORED,
    category         text NOT NULL
                         CHECK (category IN ('fabric', 'trims', 'packaging', 'services', 'finished')),
    uom              text NOT NULL CHECK (uom IN ('m', 'pc', 'job')),
    source_ref       text NOT NULL,
    UNIQUE (description_key)
);

CREATE TABLE IF NOT EXISTS core.suppliers (
    supplier_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_key       text NOT NULL UNIQUE CHECK (match_key <> ''),
    canonical_name  text NOT NULL CHECK (canonical_name <> '')
);

CREATE TABLE IF NOT EXISTS core.supplier_aliases (
    alias         text          PRIMARY KEY,
    supplier_id   bigint        NOT NULL REFERENCES core.suppliers,
    match_method  text          NOT NULL CHECK (match_method IN ('exact', 'normalized', 'fuzzy')),
    score         numeric(5,2)  NOT NULL CHECK (score BETWEEN 0 AND 100),
    row_count     int           NOT NULL CHECK (row_count > 0)
);
CREATE INDEX IF NOT EXISTS supplier_aliases_supplier_idx ON core.supplier_aliases (supplier_id);

CREATE TABLE IF NOT EXISTS core.purchase_orders (
    po_id        bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    po_number    text    NOT NULL UNIQUE CHECK (po_number ~ '^PO-[0-9]{4}-[0-9]{5}$'),
    supplier_id  bigint  NOT NULL REFERENCES core.suppliers,
    order_date   date    NOT NULL,
    currency     char(3) NOT NULL CHECK (currency IN ('RWF', 'USD')),
    fx_month     date    GENERATED ALWAYS AS (date_trunc('month', order_date::timestamp)::date) STORED,
    source_ref   text    NOT NULL,
    UNIQUE (po_id, currency),
    FOREIGN KEY (currency, fx_month) REFERENCES core.fx_rates (currency, month)
);
CREATE INDEX IF NOT EXISTS purchase_orders_supplier_idx ON core.purchase_orders (supplier_id);

-- A line repeats its order's currency so a composite foreign key can enforce
-- that every line is in the same currency as its purchase order.
CREATE TABLE IF NOT EXISTS core.purchase_order_lines (
    po_id       bigint        NOT NULL,
    product_id  bigint        NOT NULL REFERENCES core.products,
    currency    char(3)       NOT NULL,
    quantity    numeric(12,2) NOT NULL CHECK (quantity > 0),
    unit_price  numeric(14,4) NOT NULL CHECK (unit_price >= 0),
    amount      numeric(16,2) NOT NULL CHECK (amount > 0),
    amount_rwf  numeric(18,2) NOT NULL CHECK (amount_rwf > 0),
    source_ref  text          NOT NULL,
    PRIMARY KEY (po_id, product_id),
    FOREIGN KEY (po_id, currency) REFERENCES core.purchase_orders (po_id, currency)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CHECK (abs(amount - quantity * unit_price) <= greatest(0.01, 0.001 * amount))
);
CREATE INDEX IF NOT EXISTS purchase_order_lines_product_idx ON core.purchase_order_lines (product_id);

CREATE TABLE IF NOT EXISTS core.sales (
    sale_id     bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    receipt_no  text          NOT NULL CHECK (receipt_no ~ '^RCT-[0-9]{4}-[0-9]{5}$'),
    product_id  bigint        NOT NULL REFERENCES core.products,
    sale_date   date          NOT NULL,
    channel     text          NOT NULL CHECK (channel IN ('showroom', 'wholesale', 'export')),
    quantity    numeric(12,2) NOT NULL CHECK (quantity > 0),
    unit_price  numeric(14,4) NOT NULL CHECK (unit_price >= 0),
    amount      numeric(16,2) NOT NULL CHECK (amount > 0),
    currency    char(3)       NOT NULL CHECK (currency IN ('RWF', 'USD')),
    fx_month    date          GENERATED ALWAYS AS (date_trunc('month', sale_date::timestamp)::date) STORED,
    amount_rwf  numeric(18,2) NOT NULL CHECK (amount_rwf > 0),
    source_ref  text          NOT NULL,
    UNIQUE (receipt_no, product_id),
    FOREIGN KEY (currency, fx_month) REFERENCES core.fx_rates (currency, month),
    CHECK (abs(amount - quantity * unit_price) <= greatest(0.01, 0.001 * amount))
);
CREATE INDEX IF NOT EXISTS sales_product_date_idx ON core.sales (product_id, sale_date);

CREATE TABLE IF NOT EXISTS core.inventory_movements (
    movement_id    bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    voucher_no     text          NOT NULL UNIQUE
                                 CHECK (voucher_no ~ '^(GRN|ISS|PRD|SIV|ADJ)-[0-9]{4}-[0-9]{5}$'),
    movement_date  date          NOT NULL,
    product_id     bigint        NOT NULL REFERENCES core.products,
    movement_type  text          NOT NULL
                   CHECK (movement_type IN ('receipt', 'issue', 'production', 'sale', 'adjustment')),
    quantity       numeric(12,2) NOT NULL,
    po_id          bigint        REFERENCES core.purchase_orders,
    reference      text,
    source_ref     text          NOT NULL,
    -- Stock in is positive, stock out is negative, whatever the clerk typed.
    CHECK (CASE movement_type
               WHEN 'receipt'    THEN quantity > 0
               WHEN 'production' THEN quantity > 0
               WHEN 'issue'      THEN quantity < 0
               WHEN 'sale'       THEN quantity < 0
               ELSE quantity <> 0
           END),
    CHECK (po_id IS NULL OR movement_type = 'receipt')
);
CREATE INDEX IF NOT EXISTS inventory_movements_product_date_idx
    ON core.inventory_movements (product_id, movement_date);

CREATE TABLE IF NOT EXISTS core.employees (
    employee_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_code  text   NOT NULL UNIQUE CHECK (employee_code ~ '^E[0-9]{3}$'),
    full_name      text   NOT NULL,
    name_key       text   NOT NULL,
    position       text,
    first_period   date   NOT NULL,
    last_period    date   NOT NULL,
    CHECK (first_period <= last_period)
);

CREATE TABLE IF NOT EXISTS core.payroll_runs (
    run_id            bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period            date          NOT NULL UNIQUE CHECK (period = date_trunc('month', period)::date),
    employee_count    int           NOT NULL CHECK (employee_count > 0),
    total_gross       numeric(16,2) NOT NULL,
    total_deductions  numeric(16,2) NOT NULL,
    total_net         numeric(16,2) NOT NULL,
    CHECK (total_net = total_gross - total_deductions)
);

CREATE TABLE IF NOT EXISTS core.payroll_lines (
    run_id       bigint        NOT NULL REFERENCES core.payroll_runs ON DELETE CASCADE,
    employee_id  bigint        NOT NULL REFERENCES core.employees,
    gross        numeric(14,2) NOT NULL CHECK (gross > 0),
    deductions   numeric(14,2) NOT NULL CHECK (deductions >= 0),
    net          numeric(14,2) NOT NULL,
    source_ref   text          NOT NULL,
    PRIMARY KEY (run_id, employee_id),
    CHECK (net = gross - deductions)
);
