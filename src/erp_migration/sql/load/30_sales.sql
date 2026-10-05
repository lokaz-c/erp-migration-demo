-- Sales: same shape as purchases. A sale line is identified by (receipt, product).

CREATE TEMP TABLE sale_resolved ON COMMIT DROP AS
SELECT s.*, coalesce(by_code.product_id, by_desc.product_id) AS product_id
FROM staging.sale_rows s
LEFT JOIN core.products by_code ON by_code.sku = s.sku
LEFT JOIN core.products by_desc ON s.sku IS NULL AND by_desc.description_key = s.description_key;

INSERT INTO etl.rejects (book, sheet_index, row_num, reason_code, detail)
SELECT book, sheet_index, row_num, 'UNKNOWN_PRODUCT',
       format('%s is not in the item master', coalesce(sku, description_key))
FROM sale_resolved
WHERE product_id IS NULL;

INSERT INTO etl.rejects (book, sheet_index, row_num, reason_code, detail)
SELECT r.book, r.sheet_index, r.row_num, 'FX_RATE_MISSING',
       format('no %s rate for %s', r.currency, to_char(r.sale_date, 'YYYY-MM'))
FROM sale_resolved r
WHERE r.product_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM core.fx_rates f
                  WHERE f.currency = r.currency
                    AND f.month = date_trunc('month', r.sale_date)::date);

CREATE TEMP TABLE sale_ranked ON COMMIT DROP AS
SELECT r.*,
       concat_ws('|', sale_date, channel, trim_scale(quantity), trim_scale(amount), currency) AS fp,
       row_number() OVER w AS copy_no,
       first_value(concat_ws('|', sale_date, channel, trim_scale(quantity), trim_scale(amount),
                             currency)) OVER w AS kept_fp,
       first_value(book)        OVER w AS kept_book,
       first_value(sheet_index) OVER w AS kept_sheet_index,
       first_value(row_num)     OVER w AS kept_row_num
FROM sale_resolved r
WHERE r.product_id IS NOT NULL
  AND EXISTS (SELECT 1 FROM core.fx_rates f
              WHERE f.currency = r.currency
                AND f.month = date_trunc('month', r.sale_date)::date)
WINDOW w AS (PARTITION BY receipt_no, product_id ORDER BY book, sheet_index, row_num);

INSERT INTO etl.merged_rows (book, sheet_index, row_num, kept_book, kept_sheet_index,
                             kept_row_num, natural_key)
SELECT book, sheet_index, row_num, kept_book, kept_sheet_index, kept_row_num,
       receipt_no || ' / ' || coalesce(sku, description_key)
FROM sale_ranked
WHERE copy_no > 1 AND fp = kept_fp;

INSERT INTO etl.rejects (book, sheet_index, row_num, reason_code, detail)
SELECT book, sheet_index, row_num, 'CONFLICTING_DUPLICATE',
       format('%s / %s differs from %s sheet %s row %s', receipt_no,
              coalesce(sku, description_key), kept_book, kept_sheet_index, kept_row_num)
FROM sale_ranked
WHERE copy_no > 1 AND fp <> kept_fp;

WITH up AS (
    INSERT INTO core.sales AS t (receipt_no, product_id, sale_date, channel, quantity,
                                 unit_price, amount, currency, amount_rwf, source_ref)
    SELECT r.receipt_no, r.product_id, r.sale_date, r.channel, r.quantity, r.unit_price,
           r.amount, r.currency, round(r.amount * fx.rate_to_rwf, 2),
           concat_ws('|', r.book, r.sheet_index, r.row_num)
    FROM sale_ranked r
    JOIN core.fx_rates fx ON fx.currency = r.currency
                         AND fx.month = date_trunc('month', r.sale_date)::date
    WHERE r.copy_no = 1
    ON CONFLICT (receipt_no, product_id) DO UPDATE
        SET sale_date  = EXCLUDED.sale_date,
            channel    = EXCLUDED.channel,
            quantity   = EXCLUDED.quantity,
            unit_price = EXCLUDED.unit_price,
            amount     = EXCLUDED.amount,
            currency   = EXCLUDED.currency,
            amount_rwf = EXCLUDED.amount_rwf,
            source_ref = EXCLUDED.source_ref
        WHERE (t.sale_date, t.channel, t.quantity, t.unit_price, t.amount, t.currency,
               t.amount_rwf, t.source_ref)
              IS DISTINCT FROM (EXCLUDED.sale_date, EXCLUDED.channel, EXCLUDED.quantity,
                                EXCLUDED.unit_price, EXCLUDED.amount, EXCLUDED.currency,
                                EXCLUDED.amount_rwf, EXCLUDED.source_ref)
    RETURNING old.sale_id IS NULL AS inserted
)
INSERT INTO load_stats
SELECT 'core.sales', count(*) FILTER (WHERE inserted), count(*) FILTER (WHERE NOT inserted)
FROM up;
