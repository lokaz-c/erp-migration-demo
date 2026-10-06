-- Goods received against what was ordered, per purchase-order line.
-- Services are never received into stock, so they are left out.
WITH ordered AS (
    SELECT l.po_id, l.product_id, l.quantity AS ordered_qty
    FROM core.purchase_order_lines l
    JOIN core.products p USING (product_id)
    WHERE p.category <> 'services'
), received AS (
    SELECT po_id, product_id, sum(quantity) AS received_qty
    FROM core.inventory_movements
    WHERE movement_type = 'receipt' AND po_id IS NOT NULL
    GROUP BY po_id, product_id
), matched AS (
    SELECT o.ordered_qty, r.received_qty
    FROM ordered o
    FULL OUTER JOIN received r USING (po_id, product_id)
)
SELECT CASE
           WHEN ordered_qty IS NULL      THEN 'Received, but the order line did not load'
           WHEN received_qty IS NULL     THEN 'Ordered, no receipt loaded'
           WHEN received_qty = ordered_qty THEN 'Received in full'
           WHEN received_qty < ordered_qty THEN 'Short delivery'
           ELSE 'Over delivery'
       END AS status,
       count(*) AS lines
FROM matched
GROUP BY 1
ORDER BY lines DESC, status;
