-- Running stock balance per product (inflows booked before outflows on the
-- same day). A balance below zero means an issue or sale was recorded for
-- stock that, according to the loaded data, was never received: usually a
-- receipt that was rejected or never keyed.
WITH daily AS (
    SELECT product_id, movement_date, sum(quantity) AS net_qty
    FROM core.inventory_movements
    GROUP BY product_id, movement_date
), running AS (
    SELECT product_id, movement_date,
           sum(net_qty) OVER (PARTITION BY product_id ORDER BY movement_date
                              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS balance
    FROM daily
), first_negative AS (
    SELECT DISTINCT ON (product_id) product_id, movement_date, balance
    FROM running
    WHERE balance < 0
    ORDER BY product_id, movement_date
)
SELECT p.sku, p.description, f.movement_date AS first_negative_date,
       f.balance AS balance_that_day,
       (SELECT count(*) FROM running r
        WHERE r.product_id = f.product_id AND r.balance < 0) AS days_negative
FROM first_negative f
JOIN core.products p USING (product_id)
ORDER BY p.sku;
