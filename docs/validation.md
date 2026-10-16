# Running ImplantTrace in a validated environment

ImplantTrace is a portfolio project on synthetic data. Software used to make or release medical
devices is part of the quality system (ISO 13485 §4.1.6, FDA 21 CFR 820, and 21 CFR Part 11 for
electronic records and signatures). This page lists what is already built in and what a real
deployment would add.

## Part 11 controls

| Requirement | Built in | Production addition |
|---|---|---|
| Unique user identification | Individual accounts; accounts are deactivated, never deleted | SSO with Entra ID; badge login at stations |
| Password controls | Argon2id, policy, lockout, session invalidation on change | Password expiry per site policy |
| Audit trail: who, what, when, old → new, why | `app/audit.py`; changes without a reason are refused; masked secrets | Periodic audit-trail review procedure |
| Audit trail protection | App-level guard plus PostgreSQL trigger blocking UPDATE/DELETE | Revoke UPDATE/DELETE on `audit_log` from the app's DB role |
| Electronic signatures | Password re-entry; name, date/time and meaning stored and linked to the record (`electronic_signature`) | Signature manifestation on printed reports |
| Authority checks | Role per endpoint and page | Periodic access review |
| Operational sequence checks | Steps in order, quantity reconciliation, hold enforcement | — |
| Time stamps | UTC from the server | NTP-synchronised server; plant time zone shown in UI |
| Record retention | — | Backups, tested restores, retention per customer agreement |

## Validation approach

Risk-based, following FDA **Computer Software Assurance (CSA)** guidance and **GAMP 5**:

1. **User Requirements Specification** written with Production, Engineering and Quality.
2. **Risk assessment** per requirement: impact on product quality and patient safety.
   High: traceability, tolerance evaluation, hold enforcement, signatures, audit trail.
   Low: dashboard layout, colours.
3. **Testing proportional to risk.** High-risk requirements get scripted, documented tests;
   the automated suite provides objective evidence and re-runs on every change.
4. **IQ / OQ / PQ**: installation checks on the target server; operational tests of each
   requirement; performance qualification on the shop floor with real users and real CMM files.

## Requirement → test traceability (excerpt)

| ID | Requirement | Risk | Automated evidence |
|---|---|---|---|
| UR-01 | Each implant is traceable to its material heat and mill certificate | High | `test_trace.py::test_forward_trace_finds_every_implant_from_a_heat`, `::test_backward_trace_shows_material_operators_and_own_results` |
| UR-02 | Operations are performed in the defined route order | High | `test_production.py::test_steps_must_run_in_order` |
| UR-03 | Each step records operator, machine, time, good and scrap quantities | High | `test_production.py::test_only_the_operator_or_a_supervisor_can_finish`, `::test_quantities_flow_from_step_to_step` |
| UR-04 | A measurement exactly on the tolerance limit passes; beyond it fails | High | `test_inspection_import.py::test_value_exactly_on_limit_passes` |
| UR-05 | An out-of-tolerance result stops the order until Quality decides | High | `test_inspection_import.py::test_out_of_tolerance_puts_order_on_hold`, `test_quality.py::test_hold_blocks_production` |
| UR-06 | Corrupt or partial inspection files are rejected without loading anything | High | `test_inspection_import.py::test_one_bad_row_rejects_whole_file`, `::test_invalid_content_rejected` |
| UR-07 | The same inspection file is never counted twice | Medium | `test_inspection_import.py::test_same_file_twice_is_ignored` |
| UR-08 | Quality decisions are electronically signed by an authorised person | High | `test_quality.py::test_wrong_password_does_not_sign`, `::test_signature_is_linked_to_its_record`, `::test_disposition_api_permissions_and_validation` |
| UR-09 | Every change is recorded with who, when, old/new value and reason | High | `test_audit.py::test_update_records_old_new_and_reason`, `::test_silent_edit_is_refused` |
| UR-10 | The audit trail cannot be altered | High | `test_audit.py::test_audit_log_is_append_only`; CI step checks the PostgreSQL trigger |
| UR-11 | Only authorised roles can perform each action | High | `test_auth.py::test_roles_are_enforced`, `test_dashboard_pages.py::test_role_restricted_pages` |
| UR-12 | Sessions end when a password changes or a user is deactivated | Medium | `test_auth.py::test_password_change_invalidates_old_sessions`, `::test_deactivated_user_session_stops_working` |
| UR-13 | Process changes do not rewrite history of existing orders | High | `test_production.py::test_route_is_locked_once_orders_exist` |

## Change control

- Every change through a pull request with review and passing CI (lint, tests on SQLite and
  PostgreSQL, migration check, container start-up).
- Release notes per version with an impact assessment: does the change touch a high-risk
  requirement? If so, re-execute the affected scripted tests and record the evidence.
- Database changes only as Alembic migrations, rehearsed on a copy of production data.

## Data integrity (ALCOA+)

- **Attributable**: every record and change carries the signed-in user.
- **Legible / Enduring**: stored in PostgreSQL, exportable as CSV, backed up.
- **Contemporaneous**: operators record at the machine when the work happens.
- **Original**: CMM data is imported from the machine's own export, never retyped; the source
  file name and hash are kept.
- **Accurate**: tolerance evaluation in exact decimal arithmetic; quantities must reconcile.
