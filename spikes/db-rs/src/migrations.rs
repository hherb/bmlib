// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! Idempotent, sequential migration runner.
//!
//! A direct port. The only signature change is that Python's
//! `Callable[[Any], None]` becomes a boxed closure, because Rust has no
//! implicit function-object type.

use std::collections::HashSet;

use crate::backend::Dialect;
use crate::db::Db;
use crate::error::Result;
use crate::operations::{create_tables, execute, fetch_all, table_exists};
use crate::transactions::transaction;
use crate::value::Value;

/// What a migration does when applied.
///
/// Python's `Callable[[Any], None]`; Rust has no implicit function-object
/// type, so it is spelled out and named.
pub type MigrationFn = Box<dyn Fn(&mut dyn Db) -> Result<()>>;

/// A single database migration.
pub struct Migration {
    /// Sequential integer (1, 2, 3, ...). Must be unique.
    pub version: i64,
    /// Short descriptive name.
    pub name: String,
    /// Applies the DDL.
    pub up: MigrationFn,
}

impl Migration {
    /// Build a migration from its version, name and DDL closure.
    pub fn new<F>(version: i64, name: &str, up: F) -> Self
    where
        F: Fn(&mut dyn Db) -> Result<()> + 'static,
    {
        Migration {
            version,
            name: name.to_string(),
            up: Box::new(up),
        }
    }
}

fn ensure_version_table(db: &mut dyn Db) -> Result<()> {
    if table_exists(db, "schema_version")? {
        return Ok(());
    }
    let ddl = match db.dialect() {
        Dialect::Sqlite => {
            "CREATE TABLE schema_version (\n\
             \x20   version INTEGER PRIMARY KEY,\n\
             \x20   name TEXT NOT NULL,\n\
             \x20   applied_at TEXT NOT NULL DEFAULT (datetime('now'))\n\
             );\n"
        }
        Dialect::Postgres => {
            "CREATE TABLE schema_version (\n\
             \x20   version INTEGER PRIMARY KEY,\n\
             \x20   name TEXT NOT NULL,\n\
             \x20   applied_at TIMESTAMP NOT NULL DEFAULT NOW()\n\
             );\n"
        }
    };
    create_tables(db, ddl)
}

/// Version numbers already applied; empty if the table does not exist yet.
pub fn get_applied_versions(db: &mut dyn Db) -> Result<HashSet<i64>> {
    if !table_exists(db, "schema_version")? {
        return Ok(HashSet::new());
    }
    let rows = fetch_all(db, "SELECT version FROM schema_version", &[])?;
    rows.iter().map(|r| r.get_i64("version")).collect()
}

/// Apply all pending migrations in version order, returning how many ran.
pub fn run_migrations(db: &mut dyn Db, mut migrations: Vec<Migration>) -> Result<usize> {
    ensure_version_table(db)?;
    let applied = get_applied_versions(db)?;
    migrations.sort_by_key(|m| m.version);

    let mut count = 0usize;
    for migration in &migrations {
        if applied.contains(&migration.version) {
            continue;
        }
        transaction(db, |tx| {
            (migration.up)(tx)?;
            execute(
                tx,
                "INSERT INTO schema_version (version, name) VALUES (?, ?)",
                &[
                    Value::Int(migration.version),
                    Value::Text(migration.name.clone()),
                ],
            )?;
            Ok(())
        })?;
        count += 1;
    }
    Ok(count)
}
