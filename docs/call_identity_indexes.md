# Incoming call identity indexes

Incoming routing checks whether a provider call already exists before creating a
call log. Without an index, a new UUID can require scanning the whole call table.
Do not remove this duplicate-call check or create calls without it.

The previous patches created single-column indexes without setting search_index
in the Vobiz Call Log DocType. Frappe 15 schema reconciliation can remove those
indexes on a later migration, while Patch Log prevents the old patch rerunning.
This change marks the five callback identity fields as indexed and adds a new
pre_model_sync repair for existing sites.

## Deployment

Deploy this vobiz_ai change through the site's normal Git branch/release process.
No vobiz_click_to_call or vobiz_system_call code change is required for this fix.
A Git pull alone does not repair the database.

For a large production call table, check backup readiness and database disk/I/O
capacity first, then run from a dedicated server shell before migrate:

```bash
cd /home/frappe/frappe-bench

# Read-only plan; checks all five callback identity fields.
bench --site bhomeo.buopso.net execute vobiz_ai.patches.v0_0_1.restore_call_identity_indexes.repair

# Build only missing indexes; reuse existing full leading-column indexes.
bench --site bhomeo.buopso.net execute vobiz_ai.patches.v0_0_1.restore_call_identity_indexes.repair --kwargs '{"apply":true}'

# Persist indexed field metadata and record the new migration patch.
bench --site bhomeo.buopso.net migrate

# Must report already_present.
bench --site bhomeo.buopso.net execute vobiz_ai.patches.v0_0_1.restore_call_identity_indexes.repair
```

The pre-model-sync patch also repairs missing indexes if the manual repair was
not run. It uses ALGORITHM=INPLACE, LOCK=NONE and a ten-second metadata-lock wait.
The lock wait does not bound index build duration. The build consumes database
resources; schedule appropriately for the site. Failures stop migration visibly;
there is no silent skip or blocking-DDL fallback. Rerunning the repair is safe.

The indexes are non-unique: existing duplicate historical records remain valid.
The change does not modify ownership, routing eligibility, call state or audio.
Use the same process for SRIAAS with its actual bench site name.

## Verification

- Unit tests: `env/bin/python -m unittest vobiz_ai.tests.test_call_identity_indexes vobiz_ai.tests.test_callback_history vobiz_ai.tests.test_scheduler_hooks -v`
- On 17 November 2026, all 13 tests passed, including Frappe schema reconciliation
  reproducing the old index removal and retaining the corrected index.
- A disposable MariaDB test with 10,000 synthetic calls verified the exact
  incoming query: both existing and absent UUIDs changed from an ALL scan of
  10,000 rows to ref access estimating one row. Results and row count were unchanged.
- Local localhost: repaired missing identity indexes and reloaded the real Call
  Log schema twice. All five indexed metadata flags and lookup indexes survived;
  a repeat repair reported already_present.
- After production deployment, repeat the approved incoming-call test and measure
  webhook time, caller ringback, browser Answer button, two-way audio and normal
  release after hangup. This fixes the demonstrated database bottleneck; it does
  not by itself prove every possible audio or provider-routing problem resolved.

No production index creation or migration was performed during local validation.
