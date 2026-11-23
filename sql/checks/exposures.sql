-- Data-quality checks on the month's exposure extract (stg_exposures).
-- One row per issue: rule, severity, counterparty, row in the file, detail.
-- Blocking  = the case cannot go to committee until the data owner corrects it
-- Warning   = goes to committee with a flag
-- Info      = fixed automatically, logged for traceability

SELECT 'DQ01' AS rule_id, 'Blocking' AS severity, counterparty_id, row_no,
       'Conflicting duplicate rows for the same counterparty' AS detail
FROM stg_exposures
WHERE counterparty_id IN (
    SELECT counterparty_id FROM (
        SELECT DISTINCT counterparty_id, company_name, limit_mad, on_balance_mad, internal_rating, status, rm, days_past_due
        FROM stg_exposures) d
    GROUP BY counterparty_id HAVING COUNT(*) > 1)

UNION ALL
SELECT 'DQ02', 'Info', counterparty_id, MAX(row_no), 'Exact duplicate row removed'
FROM stg_exposures
GROUP BY counterparty_id, company_name, limit_mad, on_balance_mad, internal_rating, status, rm, days_past_due
HAVING COUNT(*) > 1

{% for col in mandatory %}
UNION ALL
SELECT 'DQ03', 'Blocking', counterparty_id, row_no, 'Missing mandatory field: {{ col }}'
FROM stg_exposures
WHERE {{ col }} IS NULL OR TRIM(CAST({{ col }} AS VARCHAR)) = '' OR CAST({{ col }} AS VARCHAR) = 'nan'
{% endfor %}

UNION ALL
SELECT 'DQ04', 'Blocking', counterparty_id, row_no, 'Negative amount'
FROM stg_exposures
WHERE limit_mad < 0 OR on_balance_mad < 0 OR off_balance_mad < 0

UNION ALL
SELECT 'DQ05', 'Warning', counterparty_id, row_no,
       'On-balance outstanding above {{ (tolerance * 100) | round | int }}% of the authorised limit'
FROM stg_exposures
WHERE limit_mad > 0 AND on_balance_mad > {{ tolerance }} * limit_mad

UNION ALL
SELECT 'DQ06', 'Blocking', counterparty_id, row_no, 'Rating outside the master scale: ' || internal_rating
FROM stg_exposures
WHERE TRIM(internal_rating) <> '' AND internal_rating NOT IN ({% for r in ratings %}'{{ r }}'{% if not loop.last %}, {% endif %}{% endfor %})

UNION ALL
SELECT 'DQ07', 'Blocking', counterparty_id, row_no, 'Negative days past due'
FROM stg_exposures
WHERE days_past_due < 0

UNION ALL
SELECT 'DQ08', 'Warning', counterparty_id, row_no,
       'Status ' || status || ' inconsistent with ' || CAST(days_past_due AS VARCHAR) || ' days past due (days past due used)'
FROM stg_exposures
WHERE (status = 'Default' AND days_past_due BETWEEN 0 AND 89) OR (status = 'Performing' AND days_past_due >= 90)

UNION ALL
SELECT 'DQ09', 'Info', counterparty_id, row_no, 'Counterparty ID trimmed / upper-cased'
FROM stg_exposures
WHERE id_was_normalised = 1
