# Join Safety Reference

Companion to `data_model.md` (which covers *structure* - what keys exist and what they point to).
This file covers *safety* - what actually happens, row-count-wise, when you execute those joins
against the real generated data, verified in `notebooks/eda.ipynb`. Read this before writing the
NL→SQL tool's join logic or prompting an LLM to generate SQL against this schema: every failure
mode below is a way the SQL Tool could silently return duplicated, inflated, or short-counted rows
for a real question like "show me everything for this patient."

All figures are from the `-p 2000` dev population (2,338 patients). Percentages/ratios should hold
at other population sizes; exact counts won't.

---

## 1. The #1 risk: joining two sibling one-to-many tables directly

`conditions`, `observations`, `medications`, `procedures`, `allergies`, `careplans`,
`immunizations`, `devices`, `supplies`, and `imaging_studies` are all **children of `encounters`**
(via `ENCOUNTER`), each independently one-to-many. They are not related to each other except
through that shared parent.

**If you join two of them directly on `ENCOUNTER`, you get their cross product for every
encounter, not their union.** Verified example: one real encounter has 4 `conditions` rows and 34
`observations` rows. `conditions JOIN observations ON ENCOUNTER` for that single visit returns
**136 rows** (4 × 34) - not the 38 underlying facts a human would expect.

```sql
-- WRONG: fans out to conditions_count * observations_count rows per encounter
SELECT * FROM conditions c
JOIN observations o ON c.ENCOUNTER = o.ENCOUNTER
WHERE c.PATIENT = :patient_id;
```

**Safe patterns:**
- Never join two child tables directly when the question needs both. Query each independently
  (scoped to the same `PATIENT`/`ENCOUNTER`) and assemble the results in application code, or use
  separate `SELECT`s combined with `UNION ALL` after tagging each row with its source table -
  never an `INNER`/`LEFT JOIN` between them.
- If you must correlate them in one query (e.g. "count conditions and observations per encounter"),
  pre-aggregate each side to one row per `ENCOUNTER` first (`GROUP BY ENCOUNTER`), then join the
  two aggregates - never join the raw detail rows.
- This is also why `notebooks/eda.ipynb`'s "conditions per visit" cells compute
  `groupby(['PATIENT','ENCOUNTER']).size()` on **one table at a time** rather than joining tables
  together before counting.

This is the single most likely way an LLM-generated SQL query silently multiplies rows when asked
something like "pull all the clinical data for this patient" - it's the natural first join to
reach for, and it's wrong here.

---

## 2. Recurring-key joins: `REASONCODE` causal-chain lookups over-match

`medications.REASONCODE` and `procedures.REASONCODE` link back to `conditions.CODE` for the same
`PATIENT` - this is the causal-chain data the design doc anchors "why was this drug started" on.
The join looks like a simple equi-join:

```sql
SELECT * FROM medications m
JOIN conditions c ON m.PATIENT = c.PATIENT AND m.REASONCODE = c.CODE;
```

**This over-matches, because a patient can be diagnosed with the same condition `CODE` more than
once** (recurring/re-screened findings - up to 191 times for one patient/code pair). Verified:

| Join | Reasoned source rows | Naive join output rows | Inflation |
|---|---|---|---|
| `medications.REASONCODE` → `conditions.CODE` | 94,988 | 107,286 | 112.9% |
| `procedures.REASONCODE` → `conditions.CODE` | 180,864 | 527,237 | 291.5% |

12,278 of 46,067 distinct `(PATIENT, CODE)` condition pairs recur more than once.

**Safe pattern:** pick the *nearest prior* condition occurrence by date, not every matching row:

```sql
SELECT m.*, c.*
FROM medications m
JOIN conditions c
  ON m.PATIENT = c.PATIENT AND m.REASONCODE = c.CODE AND c.START <= m.START
QUALIFY ROW_NUMBER() OVER (PARTITION BY m.PATIENT, m.START, m.CODE ORDER BY c.START DESC) = 1
-- or the equivalent correlated-subquery / window-function pattern for your SQL dialect
```

(`QUALIFY` is Postgres-incompatible as written above - use a window function in a subquery + outer
filter, or `DISTINCT ON (m.<row-identifying-cols>) ... ORDER BY c.START DESC` in real Postgres.)

---

## 3. `claims` is not 1:1 with `encounters`

`claims.APPOINTMENTID → encounters.Id` looks like a natural per-visit join, but **255,653 claim
rows span only 138,211 distinct encounters (1.85 claims/encounter on average)**. Spot-checked
example: two claim rows for the same encounter, same provider, same diagnosis, same service date -
identical except for `Id`. There's no column that explains why an encounter was split into
multiple claims.

**Safe pattern:** `encounters JOIN claims ON APPOINTMENTID` will duplicate encounter columns for
~34% of encounters that have 2+ claims (`(claims_per_encounter > 1).mean()` ≈ 0.34 at this
population size). If the question is about the encounter, aggregate `claims` to one row per
`APPOINTMENTID` first (e.g. `SUM`/`MAX` the relevant claim columns, or pick one deterministically)
before joining to `encounters`.

---

## 4. `imaging_studies`: row grain is instance, not study

A row in `imaging_studies` is one DICOM *instance* (one image/slice), not one imaging study. `Id`
(the study) and `SERIES_UID` repeat across many rows; only `INSTANCE_UID` is unique per row.

- 261,808 rows → only **11,287 distinct studies** (`Id`) - counting rows overstates "studies" by
  ~23.2x on average, and very unevenly: CT averages 391.7 instances/study, Digital Radiography
  exactly 1.0.

**Safe pattern:** `SELECT COUNT(DISTINCT Id)`, never `COUNT(*)`, for any "how many imaging studies"
question. If you need one row per study, `DISTINCT ON (Id)` / dedupe before joining to anything
else (e.g. `patients`), or a join against `imaging_studies` will inherit the same ~23x fan-out into
whatever it's joined to.

---

## 5. `claims_transactions`: `TYPE` determines which dollar column means anything

`AMOUNT`, `PAYMENTS`, and `TRANSFERS` are not three parallel measures on every row - each is only
populated (and only non-zero) for specific `TYPE` values:

| `TYPE` | `AMOUNT` | `PAYMENTS` | `TRANSFERS` |
|---|---|---|---|
| `CHARGE` | populated | **0** (not null, but always 0) | null |
| `PAYMENT` | null | populated (actual $ amount) | null |
| `TRANSFERIN` | populated | **0** | populated |
| `TRANSFEROUT` | null | **0** | populated |

`ADJUSTMENTS` is exactly `0.0` on all 2,247,679 rows - a structurally dead column, not a sparsely
used one; don't write logic expecting non-zero adjustments to ever appear at this data source.

**Safe pattern:** always filter on `TYPE` before aggregating `AMOUNT`/`TRANSFERS` (`PAYMENTS` is
safe to `SUM()` directly since it's genuinely 0, not null, off `PAYMENT` rows). Total `CHARGE`
amount equals total `PAYMENTS` exactly, dataset-wide ($369,376,048.08 both) - there is no unpaid
balance to reconcile against.

---

## 6. Partial coverage: `INNER JOIN` silently drops patients/encounters with no rows

Not every patient has a row in every clinical table. An `INNER JOIN` from `patients` (or
`encounters`) to one of these tables silently excludes anyone with zero matching rows - which is
the correct behavior for "list patients who have X" but silently wrong for "show me this patient's
full record" if you don't `LEFT JOIN`.

| Table | Patients with ≥1 row | Coverage |
|---|---|---|
| `conditions` | 2,338 / 2,338 | 100.0% |
| `observations` | 2,338 / 2,338 | 100.0% |
| `immunizations` | 2,338 / 2,338 | 100.0% |
| `claims` | 2,338 / 2,338 | 100.0% |
| `claims_transactions` | 2,338 / 2,338 | 100.0% |
| `procedures` | 2,330 / 2,338 | 99.7% |
| `payer_transitions` | 2,322 / 2,338 | 99.3% |
| `medications` | 2,286 / 2,338 | 97.8% |
| `supplies` | 2,222 / 2,338 | 95.0% |
| `devices` | 2,147 / 2,338 | 91.8% |
| `careplans` | 2,131 / 2,338 | 91.1% |
| `imaging_studies` | 2,052 / 2,338 | 87.8% |
| `allergies` | 388 / 2,338 | **16.6%** |

**Safe pattern:** default to `LEFT JOIN` from `patients`/`encounters` outward for any
"show me everything about this patient" query, and expect (don't error on) empty results for
`allergies` in particular - 5 out of 6 patients have none, by design, not by data-quality failure.

`observations` additionally has 62,970 rows (3.6%) with `ENCOUNTER` null - these are `QALY`/`DALY`/
`QOLS` population-health metrics computed per patient per year, not tied to a visit. An
`observations JOIN encounters ON ENCOUNTER` (inner) silently drops them; that's usually correct
(they have no visit to attach to), but don't be surprised when a patient's observation count via
that join is lower than `SELECT COUNT(*) FROM observations WHERE PATIENT = :id`.

---

## 7. Rows-per-parent reference table

For sizing expectations / spotting an obviously-wrong query result (e.g. a query that should
return "a handful of rows per visit" but returns thousands means something fanned out).

| Table | Key → parent | Rows/patient (avg, max) | Rows/encounter (avg, median, max) |
|---|---|---|---|
| `conditions` | `PATIENT`, `ENCOUNTER` | 36.4 / 472 | 1.56 / 1 / 11 |
| `observations` | `PATIENT`, `ENCOUNTER` | 754.6 / 21,583 | 22.98 / 11 / 1,293 |
| `medications` | `PATIENT`, `ENCOUNTER` | 51.4 / 2,543 | 1.91 / 1 / 72 |
| `procedures` | `PATIENT`, `ENCOUNTER` | 165.4 / 1,811 | 3.90 / 2 / 166 |
| `allergies` | `PATIENT`, `ENCOUNTER` | 5.1 / 12 (of the 388 who have any) | 5.14 / 4 / 12 |
| `careplans` | `PATIENT`, `ENCOUNTER`, own `Id` | 3.6 / 15 | 1.02 / 1 / 2 |
| `immunizations` | `PATIENT`, `ENCOUNTER` | 14.4 / 37 | 1.42 / 1 / 5 |
| `devices` | `PATIENT`, `ENCOUNTER` | 6.3 / 108 | 1.18 / 1 / 14 |
| `supplies` | `PATIENT`, `ENCOUNTER` | 28.0 / 868 | 2.63 / 3 / 102 |
| `imaging_studies` (deduped on `Id`) | `PATIENT`, `ENCOUNTER` | 5.5 / 122 | 1.12 / 1 / 10 |
| `claims` | `PATIENTID`, `APPOINTMENTID` | 109.3 / 3,039 | 1.85 / 1 / 73 |
| `claims_transactions` | `PATIENTID`, `CLAIMID`, `APPOINTMENTID` | 961.4 / 18,274 | 8.79 per **claim** (not encounter), median 5, max 837 |
| `payer_transitions` | `PATIENT` (no encounter link) | 37.2 / 89 | n/a |

Every table here shows the same shape: a heavy-tailed distribution with a small number of
high-utilization outlier patients (the same patients recur as outliers across tables - consistent
with a chronic-disease-heavy synthetic population, not independent randomness per table). Never use
a plain `AVG()` to characterize "typical" volume for these tables in a prompt or report - use
median, and consider whether the question is patient-weighted or visit-weighted (see the
"per-patient vs. per-visit average" discussion already in `notebooks/eda.ipynb`'s Conditions
section - the two can diverge, and which one is "correct" depends on the question being asked).

---

## 8. Quick checklist for the NL→SQL tool

- [ ] Does the query join two child tables (both one-to-many off the same parent) directly? →
      Aggregate each side first, or don't join them at all.
- [ ] Does the query join on `REASONCODE`/`CODE` for a causal-chain lookup? → Add a
      nearest-prior-date tiebreak, don't take every match.
- [ ] Does the query join `claims` to `encounters` (or vice versa) expecting 1:1? → It isn't;
      aggregate `claims` per `APPOINTMENTID` first if the question is encounter-scoped.
- [ ] Does the query count or join `imaging_studies` rows directly? → `DISTINCT ON (Id)` /
      `COUNT(DISTINCT Id)` first, always.
- [ ] Does the query sum `AMOUNT` or `TRANSFERS` in `claims_transactions` without filtering
      `TYPE`? → Filter first; each column is only meaningful for specific `TYPE` values.
- [ ] Does the query `INNER JOIN` from `patients`/`encounters` when the user asked for "everything"
      about a patient? → Use `LEFT JOIN`; several tables (`allergies` especially) have partial
      coverage by design, not by data error.
