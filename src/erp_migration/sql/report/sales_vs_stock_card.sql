-- The sales book and the stock card should agree on units sold per product
-- per month. Disagreements point at rows rejected from one book but not the other.
WITH book AS (
    SELECT product_id, date_trunc('month', sale_date)::date AS month, sum(quantity) AS qty
    FROM core.sales
    GROUP BY product_id, month
), card AS (
    SELECT product_id, date_trunc('month', movement_date)::date AS month, -sum(quantity) AS qty
    FROM core.inventory_movements
    WHERE movement_type = 'sale'
    GROUP BY product_id, month
)
SELECT CASE
           WHEN c.qty IS NULL THEN 'In the sales book only'
           WHEN b.qty IS NULL THEN 'On the stock card only'
           WHEN b.qty = c.qty THEN 'Agree'
           ELSE 'Quantities differ'
       END AS status,
       count(*) AS product_months
FROM book b
FULL OUTER JOIN card c USING (product_id, month)
GROUP BY 1
ORDER BY product_months DESC, status;
