-- Inventory movements. A movement is identified by its voucher number. Goods
-- receipts whose reference is a PO number are linked to that purchase order.

CREATE TEMP TABLE movement_resolved ON COMMIT DROP AS
SELECT s.*, coalesce(by_code.product_id, by_desc.product_id) AS product_id
FROM staging.movement_rows s
LEFT JOIN core.products by_code ON by_code.sku = s.sku
LEFT JOIN core.products by_desc ON s.sku IS NULL AND by_desc.description_key = s.description_key;

INSERT INTO etl.rejects (book, sheet_index, row_num, reason_code, detail)
SELECT book, sheet_index, row_num, 'UNKNOWN_PRODUCT',
       format('%s is not in the item master', coalesce(sku, description_key))
FROM movement_resolved
WHERE product_id IS NULL;

CREATE TEMP TABLE movement_ranked ON COMMIT DROP AS
SELECT r.*,
       concat_ws('|', movement_date, product_id, movement_type, trim_scale(quantity)) AS fp,
       row_number() OVER w AS copy_no,
       first_value(concat_ws('|', movement_date, product_id, movement_type,
                             trim_scale(quantity))) OVER w AS kept_fp,
       first_value(book)        OVER w AS kept_book,
       first_value(sheet_index) OVER w AS kept_sheet_index,
       first_value(row_num)     OVER w AS kept_row_num
FROM movement_resolved r
WHERE r.product_id IS NOT NULL
WINDOW w AS (PARTITION BY voucher_no ORDER BY book, sheet_index, row_num);

INSERT INTO etl.merged_rows (book, sheet_index, row_num, kept_book, kept_sheet_index,
                             kept_row_num, natural_key)
SELECT book, sheet_index, row_num, kept_book, kept_sheet_index, kept_row_num, voucher_no
FROM movement_ranked
WHERE copy_no > 1 AND fp = kept_fp;

INSERT INTO etl.rejects (book, sheet_index, row_num, reason_code, detail)
SELECT book, sheet_index, row_num, 'CONFLICTING_DUPLICATE',
       format('%s differs from %s sheet %s row %s', voucher_no, kept_book, kept_sheet_index,
              kept_row_num)
FROM movement_ranked
WHERE copy_no > 1 AND fp <> kept_fp;

WITH up AS (
    INSERT INTO core.inventory_movements AS t (voucher_no, movement_date, product_id,
                                               movement_type, quantity, po_id, reference,
                                               source_ref)
    SELECT r.voucher_no, r.movement_date, r.product_id, r.movement_type, r.quantity, po.po_id,
           r.reference, concat_ws('|', r.book, r.sheet_index, r.row_num)
    FROM movement_ranked r
    LEFT JOIN core.purchase_orders po ON po.po_number = r.po_number
    WHERE r.copy_no = 1
    ON CONFLICT (voucher_no) DO UPDATE
        SET movement_date = EXCLUDED.movement_date,
            product_id    = EXCLUDED.product_id,
            movement_type = EXCLUDED.movement_type,
            quantity      = EXCLUDED.quantity,
            po_id         = EXCLUDED.po_id,
            reference     = EXCLUDED.reference,
            source_ref    = EXCLUDED.source_ref
        WHERE (t.movement_date, t.product_id, t.movement_type, t.quantity, t.po_id, t.reference,
               t.source_ref)
              IS DISTINCT FROM (EXCLUDED.movement_date, EXCLUDED.product_id,
                                EXCLUDED.movement_type, EXCLUDED.quantity, EXCLUDED.po_id,
                                EXCLUDED.reference, EXCLUDED.source_ref)
    RETURNING old.movement_id IS NULL AS inserted
)
INSERT INTO load_stats
SELECT 'core.inventory_movements', count(*) FILTER (WHERE inserted),
       count(*) FILTER (WHERE NOT inserted)
FROM up;
