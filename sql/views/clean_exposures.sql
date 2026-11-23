-- One row per counterparty: exact duplicates collapsed, status derived from days past due when the two disagree.
DROP VIEW IF EXISTS clean_exposures;
CREATE VIEW clean_exposures AS
SELECT counterparty_id, company_name, segment, sector, region, branch, rm,
       limit_mad, on_balance_mad, off_balance_mad, days_past_due, internal_rating, status,
       CASE WHEN days_past_due >= 90 THEN 'Default'
            WHEN days_past_due >= 0 THEN 'Performing'
            ELSE status END AS status_derived,
       MIN(row_no) AS row_no
FROM stg_exposures
GROUP BY counterparty_id, company_name, segment, sector, region, branch, rm,
         limit_mad, on_balance_mad, off_balance_mad, days_past_due, internal_rating, status;

-- Decision history views. 'Deferred (data correction)' does not change a name's status.
DROP VIEW IF EXISTS v_last_decision;
CREATE VIEW v_last_decision AS
SELECT d.counterparty_id, d.committee_date, d.decision
FROM decisions d
JOIN (SELECT counterparty_id, MAX(committee_date) AS last_date
      FROM decisions WHERE decision <> 'Deferred (data correction)'
      GROUP BY counterparty_id) m
  ON d.counterparty_id = m.counterparty_id AND d.committee_date = m.last_date
WHERE d.decision <> 'Deferred (data correction)';

DROP VIEW IF EXISTS v_active_watch;
CREATE VIEW v_active_watch AS
SELECT counterparty_id, committee_date AS last_review, decision AS last_decision
FROM v_last_decision
WHERE decision IN ('Place on watch list', 'Keep on watch list', 'Restructure')
