-- Purchases: resolve references, reject what cannot load, de-duplicate across
-- books, build purchase-order headers, then upsert headers and lines.

-- 1. Resolve the product (by code, or by description in books that had no
--    codes) and the supplier (through the alias table the matcher filled).
CREATE TEMP TABLE purchase_resolved ON COMMIT DROP AS
SELECT s.*,
       coalesce(by_code.product_id, by_desc.product_id) AS product_id,
       a.supplier_id
FROM staging.purchase_rows s
LEFT JOIN core.products by_code ON by_code.sku = s.sku
LEFT JOIN core.products by_desc ON s.sku IS NULL AND by_desc.description_key = s.description_key
JOIN core.supplier_aliases a ON a.alias = s.supplier_raw;

INSERT INTO etl.rejects (book, sheet_index, row_num, reason_code, detail)
SELECT book, sheet_index, row_num, 'UNKNOWN_PRODUCT',
       format('%s is not in the item master', coalesce(sku, description_key))
FROM purchase_resolved
WHERE product_id IS NULL;

INSERT INTO etl.rejects (book, sheet_index, row_num, reason_code, detail)
SELECT r.book, r.sheet_index, r.row_num, 'FX_RATE_MISSING',
       format('no %s rate for %s', r.currency, to_char(r.order_date, 'YYYY-MM'))
FROM purchase_resolved r
WHERE r.product_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM core.fx_rates f
                  WHERE f.currency = r.currency
                    AND f.month = date_trunc('month', r.order_date)::date);

-- 2. De-duplicate. A purchase-order line is identified by (PO number, product).
--    The first occurrence (by book, sheet, row) is kept. Later rows with the
--    same values are merged into it; later rows with different values are
--    rejected, because two clerks disagreeing is something a person must settle.
CREATE TEMP TABLE purchase_ranked ON COMMIT DROP AS
SELECT r.*,
       concat_ws('|', supplier_id, order_date, trim_scale(quantity), trim_scale(amount), currency)
           AS fp,
       row_number() OVER w AS copy_no,
       first_value(concat_ws('|', supplier_id, order_date, trim_scale(quantity),
                             trim_scale(amount), currency)) OVER w AS kept_fp,
       first_value(book)        OVER w AS kept_book,
       first_value(sheet_index) OVER w AS kept_sheet_index,
       first_value(row_num)     OVER w AS kept_row_num
FROM purchase_resolved r
WHERE r.product_id IS NOT NULL
  AND EXISTS (SELECT 1 FROM core.fx_rates f
              WHERE f.currency = r.currency
                AND f.month = date_trunc('month', r.order_date)::date)
WINDOW w AS (PARTITION BY po_number, product_id ORDER BY book, sheet_index, row_num);

INSERT INTO etl.merged_rows (book, sheet_index, row_num, kept_book, kept_sheet_index,
                             kept_row_num, natural_key)
SELECT book, sheet_index, row_num, kept_book, kept_sheet_index, kept_row_num,
       po_number || ' / ' || coalesce(sku, description_key)
FROM purchase_ranked
WHERE copy_no > 1 AND fp = kept_fp;

INSERT INTO etl.rejects (book, sheet_index, row_num, reason_code, detail)
SELECT book, sheet_index, row_num, 'CONFLICTING_DUPLICATE',
       format('%s / %s differs from %s sheet %s row %s', po_number,
              coalesce(sku, description_key), kept_book, kept_sheet_index, kept_row_num)
FROM purchase_ranked
WHERE copy_no > 1 AND fp <> kept_fp;

-- 3. The order header is taken from the first kept line of each PO. A line
--    that disagrees with its header on supplier, date or currency is rejected.
CREATE TEMP TABLE po_header ON COMMIT DROP AS
SELECT DISTINCT ON (po_number)
       po_number, supplier_id, order_date, currency,
       concat_ws('|', book, sheet_index, row_num) AS source_ref
FROM purchase_ranked
WHERE copy_no = 1
ORDER BY po_number, book, sheet_index, row_num;

INSERT INTO etl.rejects (book, sheet_index, row_num, reason_code, detail)
SELECT r.book, r.sheet_index, r.row_num, 'PO_HEADER_MISMATCH',
       format('%s: line has supplier %s / %s / %s, order has %s / %s / %s', r.po_number,
              r.supplier_id, r.order_date, r.currency, h.supplier_id, h.order_date, h.currency)
FROM purchase_ranked r
JOIN po_header h USING (po_number)
WHERE r.copy_no = 1
  AND (r.supplier_id, r.order_date, r.currency)
      IS DISTINCT FROM (h.supplier_id, h.order_date, h.currency);

-- 4. Upsert headers, then lines (amounts converted with the order month's rate).
WITH up AS (
    INSERT INTO core.purchase_orders AS t (po_number, supplier_id, order_date, currency, source_ref)
    SELECT po_number, supplier_id, order_date, currency, source_ref
    FROM po_header
    ON CONFLICT (po_number) DO UPDATE
        SET supplier_id = EXCLUDED.supplier_id,
            order_date  = EXCLUDED.order_date,
            currency    = EXCLUDED.currency,
            source_ref  = EXCLUDED.source_ref
        WHERE (t.supplier_id, t.order_date, t.currency, t.source_ref)
              IS DISTINCT FROM (EXCLUDED.supplier_id, EXCLUDED.order_date, EXCLUDED.currency,
                                EXCLUDED.source_ref)
    RETURNING old.po_id IS NULL AS inserted
)
INSERT INTO load_stats
SELECT 'core.purchase_orders', count(*) FILTER (WHERE inserted),
       count(*) FILTER (WHERE NOT inserted)
FROM up;

WITH up AS (
    INSERT INTO core.purchase_order_lines AS t
           (po_id, product_id, currency, quantity, unit_price, amount, amount_rwf, source_ref)
    SELECT po.po_id, r.product_id, r.currency, r.quantity, r.unit_price, r.amount,
           round(r.amount * fx.rate_to_rwf, 2),
           concat_ws('|', r.book, r.sheet_index, r.row_num)
    FROM purchase_ranked r
    JOIN core.purchase_orders po USING (po_number)
    JOIN core.fx_rates fx ON fx.currency = po.currency AND fx.month = po.fx_month
    WHERE r.copy_no = 1
      AND NOT EXISTS (SELECT 1 FROM etl.rejects j
                      WHERE (j.book, j.sheet_index, j.row_num) = (r.book, r.sheet_index, r.row_num))
    ON CONFLICT (po_id, product_id) DO UPDATE
        SET currency   = EXCLUDED.currency,
            quantity   = EXCLUDED.quantity,
            unit_price = EXCLUDED.unit_price,
            amount     = EXCLUDED.amount,
            amount_rwf = EXCLUDED.amount_rwf,
            source_ref = EXCLUDED.source_ref
        WHERE (t.currency, t.quantity, t.unit_price, t.amount, t.amount_rwf, t.source_ref)
              IS DISTINCT FROM (EXCLUDED.currency, EXCLUDED.quantity, EXCLUDED.unit_price,
                                EXCLUDED.amount, EXCLUDED.amount_rwf, EXCLUDED.source_ref)
    RETURNING old.po_id IS NULL AS inserted
)
INSERT INTO load_stats
SELECT 'core.purchase_order_lines', count(*) FILTER (WHERE inserted),
       count(*) FILTER (WHERE NOT inserted)
FROM up;
