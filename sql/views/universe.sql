-- The committee universe for one month: every name the committee must see, with its category.
-- Priority: held for data correction > recovery follow-up > transfer to recovery > exit > follow-up > new entry.
WITH trig AS (
    SELECT counterparty_id, group_concat(trigger_name, '; ') AS triggers
    FROM trigger_history WHERE month_idx = {{ m }}
    GROUP BY counterparty_id
), last_trig AS (
    SELECT counterparty_id, MAX(month_idx) AS last_idx
    FROM trigger_history WHERE month_idx <= {{ m }}
    GROUP BY counterparty_id
), held AS (
    SELECT DISTINCT counterparty_id FROM dq_issues WHERE month = '{{ month }}' AND severity = 'Blocking'
), cool AS (
    SELECT counterparty_id FROM v_last_decision WHERE decision = 'No action' AND committee_date >= '{{ cooloff_from }}'
), acts AS (
    SELECT counterparty_id,
           SUM(CASE WHEN completed_date = '' THEN 1 ELSE 0 END) AS open_actions,
           SUM(CASE WHEN completed_date = '' AND due_date < '{{ asof }}' THEN 1 ELSE 0 END) AS overdue_actions
    FROM stg_actions GROUP BY counterparty_id
), base AS (
    SELECT e.counterparty_id, e.company_name, e.segment, e.sector, e.region, e.branch, e.rm,
           e.limit_mad, e.on_balance_mad, e.off_balance_mad, e.days_past_due, e.internal_rating, e.status_derived AS status,
           trig.triggers, w.last_review, w.last_decision,
           {{ m }} - last_trig.last_idx AS months_since_last_trigger,
           rec.transfer_date, rec.litigation_stage, rec.recovered_to_date_mad,
           CASE WHEN rec.transfer_date IS NULL THEN NULL
                ELSE {{ days('rec.transfer_date', "'" ~ asof ~ "'") }} END AS days_in_recovery,
           COALESCE(acts.open_actions, 0) AS open_actions, COALESCE(acts.overdue_actions, 0) AS overdue_actions,
           CASE
             WHEN held.counterparty_id IS NOT NULL
                  AND (rec.counterparty_id IS NOT NULL OR w.counterparty_id IS NOT NULL
                       OR trig.counterparty_id IS NOT NULL OR e.status_derived = 'Default')
                  THEN 'Held for data correction'
             WHEN rec.counterparty_id IS NOT NULL THEN 'Recovery follow-up'
             WHEN e.status_derived = 'Default' THEN 'Proposed transfers to recovery'
             WHEN w.counterparty_id IS NOT NULL AND trig.counterparty_id IS NULL
                  AND {{ m }} - COALESCE(last_trig.last_idx, -999) >= {{ exit_months }} THEN 'Proposed exits'
             WHEN w.counterparty_id IS NOT NULL THEN 'Under follow-up'
             WHEN trig.counterparty_id IS NOT NULL AND cool.counterparty_id IS NULL THEN 'New entries'
           END AS category,
           CASE WHEN w.counterparty_id IS NOT NULL THEN 1 ELSE 0 END AS was_on_watch
    FROM clean_exposures e
    LEFT JOIN trig ON trig.counterparty_id = e.counterparty_id
    LEFT JOIN last_trig ON last_trig.counterparty_id = e.counterparty_id
    LEFT JOIN v_active_watch w ON w.counterparty_id = e.counterparty_id
    LEFT JOIN stg_recovery rec ON rec.counterparty_id = e.counterparty_id
    LEFT JOIN held ON held.counterparty_id = e.counterparty_id
    LEFT JOIN cool ON cool.counterparty_id = e.counterparty_id
    LEFT JOIN acts ON acts.counterparty_id = e.counterparty_id
)
SELECT * FROM base WHERE category IS NOT NULL
ORDER BY category, on_balance_mad DESC
