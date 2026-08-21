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

//! Pooling, and the bridge from a tokio runtime to the synchronous `Db`.
//!
//! The load-bearing constraint is `spawn_blocking`'s signature: the closure
//! must be `Send + 'static`. A borrowed `&mut dyn Db` is neither, so **a
//! borrowed connection cannot cross into a blocking task**. What can is an
//! *owned* pooled handle, which is `Send` and `'static` when the pool is.
//!
//! That is the whole architectural consequence for a Tauri command handler:
//! check a connection out of the pool, move it into `spawn_blocking`, and run
//! ordinary synchronous `bmlib-db` code inside. No async trait is involved.

use std::ops::{Deref, DerefMut};

use bmlib_db::{DbError, Result};
use r2d2_sqlite::SqliteConnectionManager;

/// A pool of SQLite connections.
pub type Pool = r2d2::Pool<SqliteConnectionManager>;

/// A connection checked out of the pool.
pub type Checkout = r2d2::PooledConnection<SqliteConnectionManager>;

/// Open a pool against a file, or against one shared in-memory database.
///
/// `None` gives a shared-cache in-memory database, so every connection in the
/// pool sees the same data — a plain `:memory:` per connection would give each
/// one its own empty database, which is a confusing way to fail a pool test.
pub fn open_pool(path: Option<&std::path::Path>, size: u32) -> Result<Pool> {
    let manager = match path {
        Some(p) => SqliteConnectionManager::file(p),
        None => SqliteConnectionManager::file("file:bmlib_spike?mode=memory&cache=shared")
            .with_flags(rusqlite::OpenFlags::SQLITE_OPEN_URI | rusqlite::OpenFlags::default()),
    }
    // Three pragmas, and the middle one is the load-bearing part of pooling
    // SQLite: concurrent writers on one file get SQLITE_BUSY, and without a
    // busy timeout a Tauri command handler surfaces that to the user as a
    // failure rather than waiting the millisecond it needs.
    .with_init(|c| {
        c.execute_batch(
            "PRAGMA foreign_keys=ON;\
             PRAGMA busy_timeout=5000;\
             PRAGMA journal_mode=WAL;",
        )
    });

    r2d2::Pool::builder()
        .max_size(size)
        .build(manager)
        .map_err(|e| DbError::Backend(e.to_string()))
}

/// Run synchronous `Db` work on a pooled connection, off the async runtime.
///
/// This is the whole bridge. `f` is ordinary synchronous `bmlib-db` code —
/// including `transaction(db, |tx| ...)` — and it runs on tokio's blocking
/// pool, so it never stalls a runtime worker.
///
/// The bound is `FnOnce(&mut dyn Db) -> Result<T> + Send + 'static`: the
/// *closure* must be `Send + 'static`, but the `&mut dyn Db` it receives is
/// created inside the blocking task, so nothing borrowed has to cross the
/// boundary.
///
/// That indirection is not decoration. A borrowed connection cannot cross into
/// a blocking task at all — `spawn_blocking` demands `Send + 'static`, and a
/// `&mut dyn Db` is neither:
///
/// ```compile_fail
/// use bmlib_db::{open_memory, Db};
/// let mut conn = open_memory().unwrap();
/// let db: &mut dyn Db = &mut conn;
/// tokio::task::spawn_blocking(move || db.owns_commit());
/// ```
///
/// This is the one API change the async world forces on the sync design: a
/// caller hands over *work* to be run against a connection, rather than
/// handing over a connection.
pub async fn with_conn<T, F>(pool: &Pool, f: F) -> Result<T>
where
    F: FnOnce(&mut dyn bmlib_db::Db) -> Result<T> + Send + 'static,
    T: Send + 'static,
{
    let pool = pool.clone();
    tokio::task::spawn_blocking(move || {
        let mut checkout = pool.get().map_err(|e| DbError::Backend(e.to_string()))?;
        f(&mut *checkout)
    })
    .await
    .map_err(|e| DbError::Backend(format!("blocking task failed: {e}")))?
}

/// A pooled connection that is itself a [`bmlib_db::Db`].
///
/// Usually unnecessary: `Checkout` derefs to `rusqlite::Connection`, which
/// already implements `Db`, so `&mut *checkout` is a `&mut dyn Db` with no
/// wrapper at all. This newtype exists for the case where the *pooled handle
/// itself* has to be a `Db` — stored in a struct field, or boxed as
/// `Box<dyn Db>`.
///
/// It has to be a newtype because of the orphan rule: `Db` belongs to
/// `bmlib-db` and `PooledConnection` to `r2d2`, so this crate may not
/// implement one for the other directly. Worth knowing before designing the
/// backend story — a third party writing *their own* backend type has no such
/// problem, but anyone wrapping someone else's connection type pays a newtype.
pub struct PooledDb(pub Checkout);

impl Deref for PooledDb {
    type Target = rusqlite::Connection;
    fn deref(&self) -> &rusqlite::Connection {
        &self.0
    }
}

impl DerefMut for PooledDb {
    fn deref_mut(&mut self) -> &mut rusqlite::Connection {
        &mut self.0
    }
}

impl bmlib_db::Db for PooledDb {
    fn dialect(&self) -> bmlib_db::Dialect {
        bmlib_db::Dialect::Sqlite
    }
    fn execute_raw(&mut self, sql: &str, params: &[bmlib_db::Value]) -> Result<u64> {
        (**self).execute_raw(sql, params)
    }
    fn query_raw(&mut self, sql: &str, params: &[bmlib_db::Value]) -> Result<Vec<bmlib_db::Row>> {
        (**self).query_raw(sql, params)
    }
    fn last_insert_rowid(&self) -> Option<i64> {
        bmlib_db::Db::last_insert_rowid(&**self)
    }
    fn begin(&mut self) -> Result<Box<dyn bmlib_db::Db + '_>> {
        (**self).begin()
    }
    fn commit(self: Box<Self>) -> Result<()> {
        Ok(())
    }
    fn rollback(self: Box<Self>) -> Result<()> {
        Ok(())
    }
    fn owns_commit(&self) -> bool {
        true
    }
}
