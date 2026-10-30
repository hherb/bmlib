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

//! A private PostgreSQL database per test, for the live suites.
//!
//! Gated as `tests/postgres_live.rs` documents: [`TestDb::new`] returns `None`
//! unless `BMLIB_PG_TESTS=1`, and reads the `BMLIB_PG_*` variables for the
//! connection.

use std::sync::atomic::{AtomicUsize, Ordering};

use postgres::{Client, NoTls};

use bmlib::db::{connect_postgresql_params, Db};

/// Distinguishes databases created by concurrently running tests.
static NEXT_DB: AtomicUsize = AtomicUsize::new(0);

/// Where and as whom to connect.
struct PgConfig {
    host: String,
    port: u16,
    user: String,
    password: String,
    admin_db: String,
}

/// The connection settings, or `None` when the suite is not enabled.
fn pg_config() -> Option<PgConfig> {
    if std::env::var("BMLIB_PG_TESTS").ok().as_deref() != Some("1") {
        return None;
    }
    Some(PgConfig {
        host: std::env::var("BMLIB_PG_HOST").unwrap_or_else(|_| "127.0.0.1".to_string()),
        port: std::env::var("BMLIB_PG_PORT")
            .ok()
            .and_then(|p| p.parse().ok())
            .unwrap_or(5432),
        user: std::env::var("BMLIB_PG_USER")
            .ok()
            .or_else(|| std::env::var("USER").ok())
            .unwrap_or_else(|| "postgres".to_string()),
        password: std::env::var("BMLIB_PG_PASSWORD").unwrap_or_default(),
        admin_db: std::env::var("BMLIB_PG_ADMIN_DB").unwrap_or_else(|_| "postgres".to_string()),
    })
}

/// A private database, dropped when the test finishes.
pub struct TestDb {
    client: Option<Box<dyn Db>>,
    admin: Client,
    name: String,
}

impl TestDb {
    /// Create the database and connect [`Db`] to it, or `None` when gated off.
    pub fn new() -> Option<TestDb> {
        let config = pg_config()?;
        let mut admin_config = postgres::Config::new();
        admin_config
            .host(&config.host)
            .port(config.port)
            .dbname(&config.admin_db)
            .user(&config.user);
        if !config.password.is_empty() {
            admin_config.password(&config.password);
        }
        let mut admin = admin_config
            .connect(NoTls)
            .expect("admin connection to PostgreSQL");

        let name = format!(
            "bmlib_rs_{}_{}",
            std::process::id(),
            NEXT_DB.fetch_add(1, Ordering::SeqCst)
        );
        // A previous run that panicked may have left the name behind.
        admin
            .batch_execute(&format!("DROP DATABASE IF EXISTS \"{name}\""))
            .expect("drop a stale test database");
        admin
            .batch_execute(&format!("CREATE DATABASE \"{name}\""))
            .expect("create the test database");

        let client = connect_postgresql_params(
            &config.host,
            config.port,
            &name,
            &config.user,
            &config.password,
        )
        .expect("bmlib's own connector");

        Some(TestDb {
            client: Some(Box::new(client)),
            admin,
            name,
        })
    }

    /// The connection under test.
    pub fn db(&mut self) -> &mut dyn Db {
        &mut **self.client.as_mut().expect("the test connection")
    }
}

impl Drop for TestDb {
    fn drop(&mut self) {
        // Close our own connection first, or the DROP finds it open.
        self.client.take();
        let _ = self.admin.batch_execute(&format!(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity \
             WHERE datname = '{}' AND pid <> pg_backend_pid()",
            self.name
        ));
        let _ = self
            .admin
            .batch_execute(&format!("DROP DATABASE IF EXISTS \"{}\"", self.name));
    }
}
