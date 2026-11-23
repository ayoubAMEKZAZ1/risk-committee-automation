-- Committee actions as of the committee date: status, days overdue and aging bucket.
-- Open actions on names that have since left the watch list are closed with the case, not aged.
WITH a AS (
    SELECT s.*,
           CASE WHEN s.completed_date = '' AND d.decision IN
                     ('Exit watch list', 'Transfer to recovery', 'Continue recovery', 'Propose write-off', 'No action')
                THEN 1 ELSE 0 END AS closed_with_case
    FROM stg_actions s
    LEFT JOIN v_last_decision d ON d.counterparty_id = s.counterparty_id
)
SELECT a.action_id, a.counterparty_id, a.committee_date, a.action, a.owner, a.due_date, a.completed_date,
       CASE WHEN a.closed_with_case = 1 THEN 'Closed with the case'
            WHEN a.completed_date <> '' AND a.completed_date <= a.due_date THEN 'Completed on time'
            WHEN a.completed_date <> '' THEN 'Completed late'
            WHEN a.due_date >= '{{ asof }}' THEN 'Open, not yet due'
            ELSE 'Overdue' END AS status,
       CASE WHEN a.completed_date = '' AND a.closed_with_case = 0 AND a.due_date < '{{ asof }}'
            THEN {{ days('a.due_date', "'" ~ asof ~ "'") }} ELSE 0 END AS days_overdue,
       CASE WHEN a.completed_date = '' AND a.closed_with_case = 0 AND a.due_date < '{{ asof }}' THEN
            CASE {% for lo, hi, label in buckets %}
                 WHEN {{ days('a.due_date', "'" ~ asof ~ "'") }} BETWEEN {{ lo }} AND {{ hi }} THEN '{{ label }}'
            {% endfor %} END
       END AS aging_bucket
FROM a
