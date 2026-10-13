# DECISIONS.md

1. **Indexes.**
On `employees`: unique `{emp_code: 1}` guarantees identity uniqueness and O(1) lookups; `{department: 1, joined_on: 1}` supports department filtering and headcount aggregations.
On `attendance_logs`: compound unique `{emp_code: 1, date: 1}` guarantees one record per employee per day and eliminates punch-in races; `{emp_code: 1, punch_in: -1}` backs punch-out lookups; `{date: -1, emp_code: 1}` powers sorted attendance pagination; `{date: 1, late_minutes: 1}` serves monthly and leaderboard aggregations. I considered and rejected `{status: 1, date: -1}` because status has low cardinality (5 values), offering negligible selectivity while adding write overhead.

2. **Punch-in race.**
Both requests validate input and derive identical attendance dates concurrently. Both attempt `insert_one`. MongoDB's unique index on `(emp_code, date)` enforces document-level isolation: exactly one insert claims the key and succeeds (201 Created). The second triggers `DuplicateKeyError` (code 11000), which is caught and mapped to HTTP 409 Conflict. Check-then-insert is avoided entirely.

3. **Ties.**
Standard competition ranking via MongoDB `$setWindowFields` with `$rank` assigns tied employees identical ranks and skips subsequent ranks (e.g. 1, 2, 2, 4). Because the contract applies filtering on `rank <= limit`, both tied employees at the cutoff are returned (e.g. 3 rows for limit 2). Within tied ranks, rows are ordered deterministically by `emp_code` ascending.

4. **Headcount.**
The aggregation originates on `employees` (not `attendance_logs`), filtering employees with `joined_on <= last_day_of_month`. We `$group` by department to establish total active headcount before joining logs via `$lookup`. Employees with zero attendance logs produce an empty log array but remain fully counted in headcount.

5. **One thing you would change for 100x data.**
At 100x scale (~10M logs), I would maintain pre-aggregated daily/monthly department rollup tables updated asynchronously during punch-out/end-of-day jobs, rather than executing live `$lookup` and `$densify` pipelines across un-sharded collections.
