# Cron Scheduling

Deep-dive into the CronParser implementation, expression syntax, and clock jump handling.

## CronParser Implementation

The `CronParser` class in `jobs.py` provides cron expression parsing and evaluation. It's a pure-Python implementation using only the standard library (no external cron parsing packages).

### Class Interface

```python
class CronParser:
    """5-field cron expression parser and evaluator (UTC)."""

    @staticmethod
    def validate(expression: str) -> bool:
        """Check if expression has valid syntax.

        Returns True if parseable, False otherwise.
        Does NOT check if the expression can ever match (use can_match_within_days for that).
        """
        ...

    @staticmethod
    def matches(expression: str, dt: datetime) -> bool:
        """Check if datetime matches the cron expression.

        Args:
            expression: 5-field cron string
            dt: datetime to check (should be in UTC)

        Returns:
            True if all 5 fields match the datetime's components.
        """
        ...

    @staticmethod
    def next_run(expression: str, after: datetime) -> datetime:
        """Calculate the next datetime matching the expression.

        Args:
            expression: 5-field cron string
            after: Start searching from this time (exclusive)

        Returns:
            Next matching datetime (in UTC).

        Raises:
            ValueError: If no match found within 366 days.
        """
        ...

    @staticmethod
    def can_match_within_days(expression: str, days: int = 366) -> bool:
        """Check if expression can produce at least one match within N days.

        Used to warn about unmatchable expressions (e.g., Feb 31st).

        Args:
            expression: 5-field cron string
            days: Maximum days to search (default: 366 = 1 year + 1 day)

        Returns:
            True if at least one match exists within the window.
        """
        ...
```

## 5-Field Format

```
┌───────────── minute       (0-59)
│ ┌───────────── hour       (0-23)
│ │ ┌───────────── day of month (1-31)
│ │ │ ┌───────────── month      (1-12)
│ │ │ │ ┌───────────── day of week  (0-6, Sunday=0)
│ │ │ │ │
* * * * *
```

### Field Ranges

| Field | Min | Max | Special Notes |
|-------|-----|-----|---------------|
| Minute | 0 | 59 | |
| Hour | 0 | 23 | |
| Day of Month | 1 | 31 | Combined with month for validation |
| Month | 1 | 12 | |
| Day of Week | 0 | 6 | 0 = Sunday, 6 = Saturday |

## Supported Syntax

### Wildcard: `*`

Matches every value in the field's range.

```
* * * * *     → every minute
0 * * * *     → every hour at :00
0 0 * * *     → every day at midnight UTC
```

### Comma (List): `,`

Matches any of the listed values.

```
0,30 * * * *  → at :00 and :30 of every hour
0 9,17 * * *  → at 9:00 and 17:00 UTC
0 0 1,15 * *  → on the 1st and 15th of each month
```

### Range: `-`

Matches all values in the inclusive range.

```
0 9-17 * * *  → every hour from 9:00 to 17:00 UTC
* * * * 1-5   → every minute, Monday through Friday
0 0 1-7 * *   → midnight on days 1 through 7
```

### Step: `/`

Matches every Nth value. Can be combined with `*` or a range.

```
*/5 * * * *   → every 5 minutes (0, 5, 10, 15, ...)
*/15 * * * *  → every 15 minutes (0, 15, 30, 45)
0 */2 * * *   → every 2 hours (0, 2, 4, 6, ...)
0-30/10 * * * * → minutes 0, 10, 20, 30 of every hour
```

### Combined Examples

```
30 4 * * 1-5      → 4:30 AM UTC, Monday-Friday
0 */6 * * *       → every 6 hours (00:00, 06:00, 12:00, 18:00)
0 9 1,15 * *      → 9:00 AM UTC on 1st and 15th of each month
*/10 * * * 0,6    → every 10 minutes on weekends
0 2 * * *         → 2:00 AM UTC daily (common for nightly jobs)
```

## UTC Evaluation

All cron expressions are evaluated against UTC (Coordinated Universal Time). This is a deliberate design decision:

### Why UTC?

1. **No timezone ambiguity**: No confusion about DST transitions
2. **Server-agnostic**: Same behavior regardless of system timezone
3. **Reproducible**: Job fires at the same absolute time regardless of location
4. **Simple implementation**: No timezone database needed (stays zero-dependency)

### Implications for Users

If you're in UTC-5 (US Eastern) and want a job at 2 AM local time:
```bash
# 2 AM Eastern = 7 AM UTC (during EST)
# 2 AM Eastern = 6 AM UTC (during EDT)
kognisant job add --name backup --script backup.py --type scheduled --cron "0 7 * * *"
```

All displayed timestamps include "UTC" suffix:
```
Next run: in 8h 30m (2025-06-16T02:00 UTC)
```

### How Matching Works

```python
@staticmethod
def matches(expression: str, dt: datetime) -> bool:
    """Check if all 5 fields match the datetime."""
    fields = expression.split()
    # fields[0] = minute, fields[1] = hour, fields[2] = day,
    # fields[3] = month, fields[4] = day_of_week

    return (
        _field_matches(fields[0], dt.minute, 0, 59) and
        _field_matches(fields[1], dt.hour, 0, 23) and
        _field_matches(fields[2], dt.day, 1, 31) and
        _field_matches(fields[3], dt.month, 1, 12) and
        _field_matches(fields[4], dt.weekday_sunday_zero(), 0, 6)
    )
```

The daemon calls `CronParser.matches(expr, datetime.utcnow())` each polling cycle.

## `can_match_within_days()` Validation

### Purpose

Detect cron expressions that can never (or almost never) match. For example:
- `0 0 31 2 *` → February 31st (never exists)
- `0 0 30 2 *` → February 30th (never exists)
- `0 0 31 4 *` → April 31st (never exists)

### Algorithm

```python
@staticmethod
def can_match_within_days(expression: str, days: int = 366) -> bool:
    """Brute-force check: iterate day-by-day from now for N days."""
    start = datetime.utcnow().replace(second=0, microsecond=0)
    for day_offset in range(days):
        check_date = start + timedelta(days=day_offset)
        # For each day, check all minutes in that day
        for hour in range(24):
            for minute in range(60):
                dt = check_date.replace(hour=hour, minute=minute)
                if CronParser.matches(expression, dt):
                    return True
    return False
```

> **Performance note**: This is O(days × 1440) in the worst case (366 × 1440 = 527,040 iterations). It only runs once during job creation — not during polling. For most expressions, it short-circuits on the first match (first day or first few days).

### When It's Called

```python
# During job add:
if not CronParser.can_match_within_days(cron_expr):
    print(f"Warning: Cron expression '{cron_expr}' may never produce a match within 366 days.")
    confirm = input("Do you want to create this job anyway? [y/N] ")
    if confirm.lower() != 'y':
        return
```

## `next_run()` Calculation

### Algorithm

```python
@staticmethod
def next_run(expression: str, after: datetime) -> datetime:
    """Find the next minute that matches the expression.

    Strategy: Start from 'after' + 1 minute, check each minute sequentially.
    Optimization: skip ahead when possible (e.g., if hour doesn't match,
    jump to next hour's first minute).
    """
    candidate = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
    end = after + timedelta(days=366)

    while candidate < end:
        if CronParser.matches(expression, candidate):
            return candidate
        candidate += timedelta(minutes=1)

    raise ValueError(f"No match for '{expression}' within 366 days of {after}")
```

### Optimization Opportunities

The basic implementation checks minute-by-minute. For production optimization, the algorithm can skip forward:

1. If month doesn't match → skip to first day of next matching month
2. If day doesn't match → skip to next matching day
3. If hour doesn't match → skip to next matching hour

The current implementation prioritizes correctness and simplicity over speed, since `next_run()` is called infrequently (only for display purposes in `job list`).

### Usage in Job Listings

```python
# In the job list display:
if job["type"] == "scheduled" and job["cron_expression"]:
    next_dt = CronParser.next_run(job["cron_expression"], datetime.utcnow())
    delta = next_dt - datetime.utcnow()
    relative = format_timedelta(delta)  # "in 8h 30m"
    absolute = next_dt.strftime("%Y-%m-%dT%H:%M UTC")
    print(f"  {relative} ({absolute})")
```

## Clock Jump Handling

### Detection

The daemon detects clock jumps by comparing monotonic elapsed time against the expected poll interval:

```python
POLL_INTERVAL = 15  # seconds
JUMP_THRESHOLD = 2 * POLL_INTERVAL  # 30 seconds

_last_tick = time.monotonic()

# Each cycle:
now = time.monotonic()
elapsed = now - _last_tick

if elapsed > JUMP_THRESHOLD:
    # System was likely suspended/hibernated
    handle_clock_jump(elapsed)

_last_tick = now
```

### Why 30 Seconds?

- Normal jitter (system load, GC pauses): 1-2 seconds over the 15s interval
- 30 seconds (2× interval) is well above normal variation
- Avoids false positives from momentary system load

### Scheduler Policy Behavior

When a clock jump is detected, the daemon determines which scheduled jobs were missed during the jump period:

```python
def handle_clock_jump(elapsed_seconds: float):
    """Handle detected clock jump."""
    jump_start = datetime.utcnow() - timedelta(seconds=elapsed_seconds)
    jump_end = datetime.utcnow()

    for job in get_scheduled_jobs():
        policy = job.get("scheduler_policy", "skip")

        # Would this job have fired during the jump period?
        would_have_fired = any(
            CronParser.matches(job["cron_expression"], jump_start + timedelta(minutes=m))
            for m in range(int(elapsed_seconds / 60) + 1)
        )

        if not would_have_fired:
            continue

        if policy == "skip":
            logger.info(f"Clock jump: skipping missed execution for '{job['name']}'")

        elif policy == "catchup_once":
            logger.info(f"Clock jump: catchup execution for '{job['name']}'")
            spawn_job(job)  # Execute once
```

### Policy Comparison

| Scenario | `skip` | `catchup_once` |
|----------|--------|----------------|
| Laptop suspended 1 AM → 7 AM, job at 2 AM | Job skipped, next run tomorrow 2 AM | Job fires once at 7 AM (wake) |
| 6 hours of missed 15-min intervals | All 24 executions discarded | Fires exactly once |
| Server rebooted after 5-minute outage | Missed execution discarded | Fires once |

### Setting the Policy

```bash
# Default is "skip" — missed runs are silently dropped
kognisant job add --name backup --script backup.py --type scheduled --cron "0 3 * * *"

# For critical jobs that must run at least once:
kognisant job edit backup --scheduler-policy catchup_once
```

### `time.monotonic()` vs `time.time()`

| Property | `time.monotonic()` | `time.time()` |
|----------|-------------------|---------------|
| Affected by NTP | No | Yes |
| Affected by manual clock change | No | Yes |
| Affected by suspend/resume | Pauses during suspend | Jumps forward |
| Use case | Measuring elapsed intervals | Determining wall-clock time |

The daemon uses `time.monotonic()` for interval measurement. When monotonic elapsed time exceeds the threshold, it means the process was suspended — the system clock jumped forward while the daemon was asleep.

## Common Cron Patterns

| Pattern | Expression | Description |
|---------|-----------|-------------|
| Every minute | `* * * * *` | Run every single minute |
| Every 5 minutes | `*/5 * * * *` | Run at :00, :05, :10, ... |
| Every hour | `0 * * * *` | Run at the top of every hour |
| Every 6 hours | `0 */6 * * *` | Run at 00:00, 06:00, 12:00, 18:00 |
| Daily at midnight | `0 0 * * *` | Run once per day at 00:00 UTC |
| Daily at 2 AM | `0 2 * * *` | Common for backups/maintenance |
| Weekdays at 9 AM | `0 9 * * 1-5` | Monday through Friday |
| Weekends at noon | `0 12 * * 0,6` | Saturday and Sunday |
| Monthly on the 1st | `0 0 1 * *` | First of each month at midnight |
| Quarterly | `0 0 1 1,4,7,10 *` | First day of each quarter |

## Validation Errors

### Syntax Errors

```
Error: validation - Invalid cron expression: '* * *' (expected 5 fields, got 3).
Error: validation - Invalid cron expression: '60 * * * *' (minute must be 0-59).
Error: validation - Invalid cron expression: '*/0 * * * *' (step value must be > 0).
Error: validation - Invalid cron expression: '5-2 * * * *' (range start must be ≤ end).
```

### Unmatchable Warning

```
Warning: Cron expression '0 0 31 2 *' may never produce a match within 366 days.
Do you want to create this job anyway? [y/N]
```

## Cross-References

- [Job Lifecycle](job-lifecycle.md) — How scheduled jobs are executed
- [Execution Engine](execution-engine.md) — Clock jump detection mechanism
- [CLI Reference](cli-reference.md) — `--cron` flag usage
- [Architecture](architecture.md) — CronParser's place in the system
