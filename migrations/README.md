# SQLite migration history — Phase 1

The public scope is Arona 3.0; see ../docs/ARCHITECTURE.md. Internal future-version planning documents are not shipped.
SQL is packaged with the wheel; no runtime dependency on the checkout location.

| Version | Purpose | Conditions / validation | Backup / failure |
| --- | --- | --- | --- |
| 0001 | Exact historical SQL from the original candidate; intentionally preserves known FK/FTS defects. | Only used as an intermediate schema in one transaction when bootstrapping an empty DB. Never stop at v1. | A failure rolls back schema and version entries. |
| 0002 | Rebuild Memory without nonexistent owner/workspace FK targets; preserve all fields/IDs; nullable expiry; add scope index; rebuild integer-rowid FTS and its triggers; record maintenance actor/scope. | Recognized v1 columns, sequential version history, working FTS5. Check SQLite integrity, FK integrity and FTS integrity before commit. | A populated/existing v1 database requires a verified snapshot in explicitly configured backup_dir. Any copy/check/SQL error aborts. Existing records are never silently discarded to satisfy a constraint. |
| 0003 | Add scoped auto-memory preferences and transactional Memory events. | Preserve Memory and FTS records. | Same verified pre-upgrade snapshot and atomic rollback. |
| 0004 | Add indexed, derived Unicode dedup/subject keys for active records. Keep the original Memory columns and semantics. | Triggers invalidate keys on content/scope/type/status changes and remove deleted entries, including maintenance SQL writes. Python fills pending keys on first admission in that scope, in the same write transaction. Normal repository writes fill keys immediately. Schema validation checks definitions, indexes, scope synchronization and populated key values. | Same verified snapshot and atomic rollback. First admission after upgrade costs one linear backfill per affected scope; subsequent lookups use indexes. No in-process cache or custom SQLite function is required. |

## Execution

Use the local operator CLI: `arona migrate --operator-id operator-name`.
Configuration comes from the environment. In development only, prefix with
`arona --env-file .env ...`.

No model, Policy tool, network or Candidate execution is involved.
The migration holds BEGIN IMMEDIATE while examining the version and taking an
independent read-only SQLite backup. Other writers cannot change that snapshot.
SQLite FK enforcement is disabled on the migration connection before BEGIN for
the table rebuild, then foreign_key_check must pass before COMMIT. Application
connections always enable FK enforcement. Do not use executescript inside the
transaction: each complete statement executes without an implicit commit.

Repeated execution validates the current schema and makes no new migration/event entries.
Future/gapped versions, unversioned populated databases, unexpected Memory
columns and invalid legacy records are rejected for operator inspection.

## Recovery

On failure, keep the original database and the pre-upgrade snapshot. Fix the
reported cause; never delete the database to get migration to pass. Restore a
verified snapshot to a NEW path and inspect it offline. Application/production
switching and rollback decisions belong to the operator. The program will not
replace an existing live DB, restart services or deploy itself.
