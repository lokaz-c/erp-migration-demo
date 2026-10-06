-- Reference data: products from the item master, then suppliers and their aliases.
--
-- Every upsert below follows the same pattern: ON CONFLICT on the natural key,
-- update only when something actually changed, and use PostgreSQL 18's
-- RETURNING old/new to count inserts and updates into load_stats. A reload of
-- the same books therefore reports zero changes.

-- Products: first occurrence of each SKU wins; exact repeats are merged and
-- repeats with a different description are rejected.
CREATE TEMP TABLE product_ranked ON COMMIT DROP AS
SELECT s.*,
       concat_ws('|', description, category, uom)                AS fp,
       row_number()  OVER w                                       AS copy_no,
       first_value(concat_ws('|', description, category, uom)) OVER w AS kept_fp,
       first_value(book)        OVER w                            AS kept_book,
       first_value(sheet_index) OVER w                            AS kept_sheet_index,
       first_value(row_num)     OVER w                            AS kept_row_num
FROM staging.product_rows s
WINDOW w AS (PARTITION BY sku ORDER BY book, sheet_index, row_num);

INSERT INTO etl.merged_rows (book, sheet_index, row_num, kept_book, kept_sheet_index,
                             kept_row_num, natural_key)
SELECT book, sheet_index, row_num, kept_book, kept_sheet_index, kept_row_num, sku
FROM product_ranked
WHERE copy_no > 1 AND fp = kept_fp;

INSERT INTO etl.rejects (book, sheet_index, row_num, reason_code, detail)
SELECT book, sheet_index, row_num, 'CONFLICTING_DUPLICATE',
       format('%s differs from %s row %s', sku, kept_book, kept_row_num)
FROM product_ranked
WHERE copy_no > 1 AND fp <> kept_fp;

WITH up AS (
    INSERT INTO core.products AS t (sku, description, category, uom, source_ref)
    SELECT sku, description, category, uom, concat_ws('|', book, sheet_index, row_num)
    FROM product_ranked
    WHERE copy_no = 1
    ON CONFLICT (sku) DO UPDATE
        SET description = EXCLUDED.description,
            category    = EXCLUDED.category,
            uom         = EXCLUDED.uom,
            source_ref  = EXCLUDED.source_ref
        WHERE (t.description, t.category, t.uom, t.source_ref)
              IS DISTINCT FROM (EXCLUDED.description, EXCLUDED.category, EXCLUDED.uom,
                                EXCLUDED.source_ref)
    RETURNING old.product_id IS NULL AS inserted
)
INSERT INTO load_stats
SELECT 'core.products', count(*) FILTER (WHERE inserted), count(*) FILTER (WHERE NOT inserted)
FROM up;

-- Suppliers: one per cluster found by the matcher, keyed by the normalized
-- key of the cluster's most frequent spelling.
WITH up AS (
    INSERT INTO core.suppliers AS t (match_key, canonical_name)
    SELECT DISTINCT canonical_key, canonical
    FROM staging.supplier_matches
    ON CONFLICT (match_key) DO UPDATE
        SET canonical_name = EXCLUDED.canonical_name
        WHERE t.canonical_name IS DISTINCT FROM EXCLUDED.canonical_name
    RETURNING old.supplier_id IS NULL AS inserted
)
INSERT INTO load_stats
SELECT 'core.suppliers', count(*) FILTER (WHERE inserted), count(*) FILTER (WHERE NOT inserted)
FROM up;

WITH up AS (
    INSERT INTO core.supplier_aliases AS t (alias, supplier_id, match_method, score, row_count)
    SELECT m.alias, s.supplier_id, m.method, m.score, m.row_count
    FROM staging.supplier_matches m
    JOIN core.suppliers s ON s.match_key = m.canonical_key
    ON CONFLICT (alias) DO UPDATE
        SET supplier_id  = EXCLUDED.supplier_id,
            match_method = EXCLUDED.match_method,
            score        = EXCLUDED.score,
            row_count    = EXCLUDED.row_count
        WHERE (t.supplier_id, t.match_method, t.score, t.row_count)
              IS DISTINCT FROM (EXCLUDED.supplier_id, EXCLUDED.match_method, EXCLUDED.score,
                                EXCLUDED.row_count)
    RETURNING old.alias IS NULL AS inserted
)
INSERT INTO load_stats
SELECT 'core.supplier_aliases', count(*) FILTER (WHERE inserted),
       count(*) FILTER (WHERE NOT inserted)
FROM up;
