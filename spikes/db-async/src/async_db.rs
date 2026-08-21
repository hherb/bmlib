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

//! The road not taken: a fully async `Db` trait.
//!
//! This exists to price the alternative, not to recommend it. The question was
//! whether `transaction(db, |tx| ...)` survives being made async — in
//! particular whether the closure can still *lend* the block to its body, which
//! is the case Rust historically could not express and which
//! `AsyncFnOnce` (stable since 1.85) was introduced to fix.
//!
//! It does survive. See `FINDINGS.md` for why it is still the wrong choice for
//! SQLite.

use async_trait::async_trait;
use bmlib_db::{Result, Row, Value};

/// An async counterpart to [`bmlib_db::Db`].
///
/// `#[async_trait]` boxes every returned future — one allocation per statement
/// on top of the `Box` per transaction the sync trait already pays. Native
/// `async fn` in traits would avoid that but is not dyn-compatible, and the
/// whole design rests on `&mut dyn Db`.
#[async_trait]
pub trait AsyncDb: Send {
    /// Run a statement.
    async fn execute_raw(&mut self, sql: &str, params: &[Value]) -> Result<u64>;
    /// Run a query.
    async fn query_raw(&mut self, sql: &str, params: &[Value]) -> Result<Vec<Row>>;
    /// Open a nested block.
    async fn begin(&mut self) -> Result<Box<dyn AsyncDb + Send + '_>>;
    /// Commit this block.
    async fn commit(self: Box<Self>) -> Result<()>;
    /// Roll this block back.
    async fn rollback(self: Box<Self>) -> Result<()>;
    /// True if a write right now would need its own commit.
    fn owns_commit(&self) -> bool;
}

/// The async counterpart to [`bmlib_db::transaction`].
///
/// The signature is the finding. `AsyncFnOnce(&mut dyn AsyncDb) -> Result<T>`
/// is a *lending* async closure: the future it returns borrows the block that
/// was passed in. Before Rust 1.85 this could not be written generically at
/// all — you had to name the lifetime, which then did not compose for nested
/// calls. It composes now.
pub async fn transaction<T, F>(db: &mut dyn AsyncDb, f: F) -> Result<T>
where
    F: for<'a> AsyncFnOnce(&'a mut (dyn AsyncDb + Send + 'a)) -> Result<T>,
{
    let mut block = db.begin().await?;
    match f(&mut *block).await {
        Ok(value) => {
            block.commit().await?;
            Ok(value)
        }
        Err(err) => {
            let _ = block.rollback().await;
            Err(err)
        }
    }
}

/// Compile-time evidence about what can implement [`AsyncDb`].
///
/// `AsyncDb: Send` because tokio's `spawn` requires a `Send` future, and a
/// trait object held across an `await` is part of that future. A connection
/// qualifies:
///
/// ```
/// fn assert_send<T: Send>() {}
/// assert_send::<rusqlite::Connection>();
/// ```
///
/// A transaction does not — and this is what settles the question. `Connection`
/// holds a `RefCell`, so it is `Send` but not `Sync`; `Transaction<'conn>`
/// holds a `&'conn Connection`, and `&T: Send` requires `T: Sync`:
///
/// ```compile_fail
/// fn assert_send<T: Send>() {}
/// assert_send::<rusqlite::Transaction<'static>>();
/// ```
///
/// So [`AsyncDb`] **cannot be implemented over `rusqlite`'s transaction
/// types at all**. The shape below type-checks; there is simply nothing in
/// this backend that can inhabit it. See FINDINGS.md.
pub mod send_evidence {}

/// A toy backend that *is* `Send`, so the closure shape can be exercised.
///
/// This models savepoint semantics — a block accumulates pending writes,
/// commit flushes them to its parent, rollback drops them — and nothing else.
/// It is not a database and makes no claim to be one. Its only job is to prove
/// that [`transaction`]'s lending async closure composes under nesting, which
/// no `rusqlite` type can be used to show.
pub mod toy {
    use super::{AsyncDb, Result, Row, Value};
    use async_trait::async_trait;

    /// The root of a toy store.
    #[derive(Default)]
    pub struct MemDb {
        rows: Vec<String>,
    }

    impl MemDb {
        /// Everything committed so far.
        pub fn rows(&self) -> &[String] {
            &self.rows
        }
    }

    /// A pending block over some parent.
    pub struct MemBlock<'a> {
        parent: &'a mut (dyn AsyncDb + Send + 'a),
        pending: Vec<String>,
    }

    fn inserted(sql: &str) -> Option<&str> {
        sql.strip_prefix("INSERT ")
    }

    #[async_trait]
    impl AsyncDb for MemDb {
        async fn execute_raw(&mut self, sql: &str, _params: &[Value]) -> Result<u64> {
            if let Some(v) = inserted(sql) {
                self.rows.push(v.to_string());
                return Ok(1);
            }
            Ok(0)
        }
        async fn query_raw(&mut self, _sql: &str, _params: &[Value]) -> Result<Vec<Row>> {
            Ok(Vec::new())
        }
        async fn begin(&mut self) -> Result<Box<dyn AsyncDb + Send + '_>> {
            Ok(Box::new(MemBlock {
                parent: self,
                pending: Vec::new(),
            }))
        }
        async fn commit(self: Box<Self>) -> Result<()> {
            Ok(())
        }
        async fn rollback(self: Box<Self>) -> Result<()> {
            Ok(())
        }
        fn owns_commit(&self) -> bool {
            true
        }
    }

    #[async_trait]
    impl AsyncDb for MemBlock<'_> {
        async fn execute_raw(&mut self, sql: &str, _params: &[Value]) -> Result<u64> {
            if let Some(v) = inserted(sql) {
                self.pending.push(v.to_string());
                return Ok(1);
            }
            Ok(0)
        }
        async fn query_raw(&mut self, _sql: &str, _params: &[Value]) -> Result<Vec<Row>> {
            Ok(Vec::new())
        }
        async fn begin(&mut self) -> Result<Box<dyn AsyncDb + Send + '_>> {
            Ok(Box::new(MemBlock {
                parent: self,
                pending: Vec::new(),
            }))
        }
        /// Flush this block's writes to the parent — the `RELEASE SAVEPOINT`
        /// analogue, and deliberately not a commit.
        async fn commit(mut self: Box<Self>) -> Result<()> {
            let pending = std::mem::take(&mut self.pending);
            for row in pending {
                self.parent
                    .execute_raw(&format!("INSERT {row}"), &[])
                    .await?;
            }
            Ok(())
        }
        /// Drop this block's writes and nothing else.
        async fn rollback(self: Box<Self>) -> Result<()> {
            Ok(())
        }
        fn owns_commit(&self) -> bool {
            false
        }
    }
}
