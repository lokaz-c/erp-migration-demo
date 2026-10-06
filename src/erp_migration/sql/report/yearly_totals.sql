-- Year totals in RWF, using the synthetic exchange-rate table for USD lines.
WITH purchases AS (
    SELECT extract(year FROM po.order_date)::int AS year, sum(l.amount_rwf) AS rwf
    FROM core.purchase_order_lines l
    JOIN core.purchase_orders po USING (po_id)
    GROUP BY 1
), sales AS (
    SELECT extract(year FROM sale_date)::int AS year, sum(amount_rwf) AS rwf,
           sum(amount_rwf) FILTER (WHERE currency = 'USD') AS usd_part
    FROM core.sales
    GROUP BY 1
), payroll AS (
    SELECT extract(year FROM period)::int AS year, sum(total_gross) AS rwf,
           round(avg(employee_count), 1) AS avg_headcount
    FROM core.payroll_runs
    GROUP BY 1
)
SELECT year, p.rwf AS purchases_rwf, s.rwf AS sales_rwf, s.usd_part AS export_sales_rwf,
       y.rwf AS payroll_gross_rwf, y.avg_headcount
FROM purchases p
FULL OUTER JOIN sales s USING (year)
FULL OUTER JOIN payroll y USING (year)
ORDER BY year;
