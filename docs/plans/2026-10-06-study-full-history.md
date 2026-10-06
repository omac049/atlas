# Study and intel reports read every observation (2026-10-06)

Owner sign-off: 2026-10-06, in session ("yes, go ahead with the plan and amendment").

## Problem

`atlas gaps study` and `atlas intel report` load gap observations through
`AtlasStore.all_gap_observations()`, whose default keeps only the newest 50,000 rows.
That cap was added on 2026-08-12 for the dashboard watch board. The table passed
50,000 rows on about 2026-08-23. Every weekly study report from 2026-08-24 on
reviewed exactly 50,000 rows (four to six days) instead of the whole study. The
divergence report has the same cap. The rows themselves were never deleted:
440,290 rows from 2026-08-12 on are in `gap_observations`.

## Change

1. `all_gap_observations(limit=None)` reads every row, oldest first. The default
   stays 50,000, so the monitor, dashboard, status and site build are unchanged.
2. `gaps_study` and `divergence_report` pass `limit=None`. `study_report()` and the
   intel section builders are not changed: same code, whole table.
3. Tests: a store that simulates a one-row cap proves that both reports see every row.
4. Charter amendment in `docs/NINETY_DAY_STUDY.md`: what changed, which metrics can
   move, and that the dated reports from 2026-08-24 to 2026-10-06 are capped.
5. The dated report files already on disk are left as they are, as the study's
   written record. The corrected reports for this week are written beside them as
   `*-20261006-full-history.*`. Nothing from them is printed while generating.

## Cost

Loading all rows took 9 s and 2.6 GB of memory on 2026-10-06 (24 GB machine). At about
8,000 rows a day, day 90 is roughly 780,000 rows, about 5 GB. That is acceptable for a
weekly job. If it stops being acceptable, stream the rows in batches; do not reintroduce
a cap.

## Not in scope

`gaps_scan` (every 5 minutes), `gaps_status`, `site_build` and the dashboard keep the
cap: they show recent activity, and loading everything every 5 minutes would cost
2.6 GB each time. TODO.md records them.
