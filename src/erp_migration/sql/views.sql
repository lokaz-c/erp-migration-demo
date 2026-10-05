-- Views used by the report and the tests. Recreated on every run.

-- One outcome per source row. Rejected and skipped rows come from etl.rejects,
-- merged rows from etl.merged_rows; everything else should be in a core table,
-- which the report checks against etl.loaded_rows rather than assuming.
CREATE OR REPLACE VIEW etl.row_outcomes AS
SELECT r.book, r.sheet_index, r.sheet, r.row_num, r.domain, r.kind,
       CASE
           WHEN j.reason_code IS NOT NULL AND rr.category = 'noise' THEN 'skipped'
           WHEN j.reason_code IS NOT NULL THEN 'rejected'
           WHEN m.book IS NOT NULL THEN 'merged'
           ELSE 'loaded'
       END AS outcome,
       j.reason_code,
       j.detail,
       m.kept_book, m.kept_sheet_index, m.kept_row_num
FROM staging.rows r
LEFT JOIN etl.rejects j USING (book, sheet_index, row_num)
LEFT JOIN etl.reject_reasons rr ON rr.code = j.reason_code
LEFT JOIN etl.merged_rows m USING (book, sheet_index, row_num);

-- Source row of every core record ("book|sheet_index|row_num").
CREATE OR REPLACE VIEW etl.loaded_rows AS
SELECT 'core.products' AS target, source_ref FROM core.products
UNION ALL SELECT 'core.purchase_order_lines', source_ref FROM core.purchase_order_lines
UNION ALL SELECT 'core.sales', source_ref FROM core.sales
UNION ALL SELECT 'core.inventory_movements', source_ref FROM core.inventory_movements
UNION ALL SELECT 'core.payroll_lines', source_ref FROM core.payroll_lines;
