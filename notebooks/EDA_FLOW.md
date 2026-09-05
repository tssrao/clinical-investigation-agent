# EDA exploration flow

Order to explore the 18 Synthea CSV tables, based on how they join (see `data_model.md`) — not alphabetical. Each table builds on the join patterns learned from the previous one.

## Done
1. ~~`patients.csv`~~ — root entity
2. ~~`encounters.csv`~~ — the hub every clinical/claims table hangs off via `ENCOUNTER`

## Next
3. **`organizations.csv` + `providers.csv`** — small dimension tables referenced by `encounters.ORGANIZATION`/`PROVIDER`. Quick, and resolves two columns in `encounters_df` that are currently opaque UUIDs.

## Then — core clinical events (causal chain, do together)
4. `conditions.csv` — diagnoses; other tables' `REASONCODE` points here
5. `observations.csv` — labs/vitals (the creatinine-trend anchor data)
6. `medications.csv` — has `REASONCODE` linking back to `conditions` (~81% coverage per design doc)
7. `procedures.csv`

## Then — secondary clinical events (same PATIENT+ENCOUNTER pattern, lower priority)
8. `allergies.csv` (started, not finished)
9. `careplans.csv` (started, not finished)
10. `immunizations.csv`
11. `devices.csv`
12. `supplies.csv`
13. `imaging_studies.csv`

## Last — insurance/claims (separate RBAC domain, own join spine)
14. `payers.csv`
15. `payer_transitions.csv`
16. `claims.csv`
17. `claims_transactions.csv` (started, not finished — do `claims.csv` first since `claims_transactions.CLAIMID` FKs into it)

---

**Per-table checklist** (apply consistently, don't just chase the first interesting column):
- `.info()` + missing-value % (not just eyeballing)
- dtype conversion (dates → `datetime`) before anything else
- duplicate / primary-key uniqueness check
- referential integrity check against the parent table(s) it FKs into
- `.describe()` on genuinely numeric columns only (skip ID/code columns)
- at least one plot where mean/median disagree or skew is suspected
- a short written insights summary at the end
