-- Payroll: build the employee list from rows that carry an employee number,
-- resolve rows that only have a name, de-duplicate, then load runs and lines.

CREATE TEMP TABLE employee_rows ON COMMIT DROP AS
SELECT * FROM staging.payroll_rows WHERE employee_code IS NOT NULL;

-- Display name = the spelling used most often; position = the latest one.
WITH spelling AS (
    SELECT employee_code, name_raw, count(*) AS uses
    FROM employee_rows
    GROUP BY employee_code, name_raw
), best_name AS (
    SELECT DISTINCT ON (employee_code) employee_code, name_raw
    FROM spelling
    ORDER BY employee_code, uses DESC, name_raw
), latest AS (
    SELECT DISTINCT ON (employee_code) employee_code, position
    FROM employee_rows
    ORDER BY employee_code, period DESC, book DESC, sheet_index DESC, row_num DESC
), span AS (
    SELECT employee_code,
           min(period) AS first_period,
           max(period) AS last_period,
           mode() WITHIN GROUP (ORDER BY name_key) AS name_key
    FROM employee_rows
    GROUP BY employee_code
), up AS (
    INSERT INTO core.employees AS t (employee_code, full_name, name_key, position,
                                     first_period, last_period)
    SELECT s.employee_code, b.name_raw, s.name_key, l.position, s.first_period, s.last_period
    FROM span s
    JOIN best_name b USING (employee_code)
    JOIN latest l USING (employee_code)
    ON CONFLICT (employee_code) DO UPDATE
        SET full_name    = EXCLUDED.full_name,
            name_key     = EXCLUDED.name_key,
            position     = EXCLUDED.position,
            first_period = EXCLUDED.first_period,
            last_period  = EXCLUDED.last_period
        WHERE (t.full_name, t.name_key, t.position, t.first_period, t.last_period)
              IS DISTINCT FROM (EXCLUDED.full_name, EXCLUDED.name_key, EXCLUDED.position,
                                EXCLUDED.first_period, EXCLUDED.last_period)
    RETURNING old.employee_id IS NULL AS inserted
)
INSERT INTO load_stats
SELECT 'core.employees', count(*) FILTER (WHERE inserted), count(*) FILTER (WHERE NOT inserted)
FROM up;

-- Rows without an employee number: match the order-insensitive name key
-- against this run's employees. Exactly one match is required.
CREATE TEMP TABLE payroll_resolved ON COMMIT DROP AS
WITH names AS (
    SELECT name_key, array_agg(DISTINCT employee_code ORDER BY employee_code) AS codes
    FROM employee_rows
    GROUP BY name_key
)
SELECT p.*,
       coalesce(p.employee_code, CASE WHEN cardinality(n.codes) = 1 THEN n.codes[1] END)
           AS resolved_code,
       coalesce(cardinality(n.codes), 0) AS name_matches
FROM staging.payroll_rows p
LEFT JOIN names n ON p.employee_code IS NULL AND n.name_key = p.name_key;

INSERT INTO etl.rejects (book, sheet_index, row_num, reason_code, detail)
SELECT book, sheet_index, row_num,
       CASE WHEN name_matches = 0 THEN 'UNKNOWN_EMPLOYEE' ELSE 'AMBIGUOUS_EMPLOYEE' END,
       format('no employee number; %s employees named %L', name_matches, name_raw)
FROM payroll_resolved
WHERE resolved_code IS NULL;

CREATE TEMP TABLE payroll_ranked ON COMMIT DROP AS
SELECT r.*,
       concat_ws('|', trim_scale(gross), trim_scale(deductions), trim_scale(net)) AS fp,
       row_number() OVER w AS copy_no,
       first_value(concat_ws('|', trim_scale(gross), trim_scale(deductions),
                             trim_scale(net))) OVER w AS kept_fp,
       first_value(book)        OVER w AS kept_book,
       first_value(sheet_index) OVER w AS kept_sheet_index,
       first_value(row_num)     OVER w AS kept_row_num
FROM payroll_resolved r
WHERE r.resolved_code IS NOT NULL
WINDOW w AS (PARTITION BY period, resolved_code ORDER BY book, sheet_index, row_num);

INSERT INTO etl.merged_rows (book, sheet_index, row_num, kept_book, kept_sheet_index,
                             kept_row_num, natural_key)
SELECT book, sheet_index, row_num, kept_book, kept_sheet_index, kept_row_num,
       to_char(period, 'YYYY-MM') || ' / ' || resolved_code
FROM payroll_ranked
WHERE copy_no > 1 AND fp = kept_fp;

INSERT INTO etl.rejects (book, sheet_index, row_num, reason_code, detail)
SELECT book, sheet_index, row_num, 'CONFLICTING_DUPLICATE',
       format('%s / %s differs from %s sheet %s row %s', to_char(period, 'YYYY-MM'),
              resolved_code, kept_book, kept_sheet_index, kept_row_num)
FROM payroll_ranked
WHERE copy_no > 1 AND fp <> kept_fp;

-- One run per month, with totals over the lines that load.
WITH up AS (
    INSERT INTO core.payroll_runs AS t (period, employee_count, total_gross, total_deductions,
                                        total_net)
    SELECT period, count(*), sum(gross), sum(deductions), sum(net)
    FROM payroll_ranked
    WHERE copy_no = 1
    GROUP BY period
    ON CONFLICT (period) DO UPDATE
        SET employee_count   = EXCLUDED.employee_count,
            total_gross      = EXCLUDED.total_gross,
            total_deductions = EXCLUDED.total_deductions,
            total_net        = EXCLUDED.total_net
        WHERE (t.employee_count, t.total_gross, t.total_deductions, t.total_net)
              IS DISTINCT FROM (EXCLUDED.employee_count, EXCLUDED.total_gross,
                                EXCLUDED.total_deductions, EXCLUDED.total_net)
    RETURNING old.run_id IS NULL AS inserted
)
INSERT INTO load_stats
SELECT 'core.payroll_runs', count(*) FILTER (WHERE inserted),
       count(*) FILTER (WHERE NOT inserted)
FROM up;

WITH up AS (
    INSERT INTO core.payroll_lines AS t (run_id, employee_id, gross, deductions, net, source_ref)
    SELECT pr.run_id, e.employee_id, r.gross, r.deductions, r.net,
           concat_ws('|', r.book, r.sheet_index, r.row_num)
    FROM payroll_ranked r
    JOIN core.payroll_runs pr USING (period)
    JOIN core.employees e ON e.employee_code = r.resolved_code
    WHERE r.copy_no = 1
    ON CONFLICT (run_id, employee_id) DO UPDATE
        SET gross      = EXCLUDED.gross,
            deductions = EXCLUDED.deductions,
            net        = EXCLUDED.net,
            source_ref = EXCLUDED.source_ref
        WHERE (t.gross, t.deductions, t.net, t.source_ref)
              IS DISTINCT FROM (EXCLUDED.gross, EXCLUDED.deductions, EXCLUDED.net,
                                EXCLUDED.source_ref)
    RETURNING old.run_id IS NULL AS inserted
)
INSERT INTO load_stats
SELECT 'core.payroll_lines', count(*) FILTER (WHERE inserted),
       count(*) FILTER (WHERE NOT inserted)
FROM up;
