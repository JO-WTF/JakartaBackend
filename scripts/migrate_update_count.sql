-- SQL script to update update_count field for all existing DN records
-- This script counts records in dn_record table and updates the dn table

-- Step 1: Preview what will be updated (optional - run this first to see the changes)
SELECT 
    d.dn_number,
    d.update_count as current_count,
    COUNT(r.id) as actual_record_count,
    COUNT(r.id) - COALESCE(d.update_count, 0) as difference
FROM dn d
LEFT JOIN dn_record r ON d.dn_number = r.dn_number
GROUP BY d.dn_number, d.update_count
HAVING COUNT(r.id) != COALESCE(d.update_count, 0)
ORDER BY difference DESC;

-- Step 2: Update the update_count field
-- Run this to actually perform the update
UPDATE dn 
SET update_count = (
    SELECT COUNT(*)
    FROM dn_record
    WHERE dn_record.dn_number = dn.dn_number
)
WHERE dn_number IN (
    SELECT dn_number FROM dn_record
);

-- Step 3: Set update_count to 0 for DNs without any records
UPDATE dn
SET update_count = 0
WHERE dn_number NOT IN (
    SELECT DISTINCT dn_number FROM dn_record
)
AND (update_count IS NULL OR update_count != 0);

-- Step 4: Verify the results
SELECT 
    d.dn_number,
    d.update_count,
    COUNT(r.id) as actual_record_count,
    CASE 
        WHEN d.update_count = COUNT(r.id) THEN '✓ Correct'
        ELSE '✗ Mismatch'
    END as status
FROM dn d
LEFT JOIN dn_record r ON d.dn_number = r.dn_number
GROUP BY d.dn_number, d.update_count
ORDER BY d.dn_number
LIMIT 20;

-- Summary statistics
SELECT 
    COUNT(DISTINCT d.dn_number) as total_dns,
    SUM(d.update_count) as total_update_count,
    (SELECT COUNT(*) FROM dn_record) as total_dn_records,
    COUNT(CASE WHEN d.update_count > 0 THEN 1 END) as dns_with_updates,
    COUNT(CASE WHEN d.update_count = 0 THEN 1 END) as dns_without_updates
FROM dn d;
