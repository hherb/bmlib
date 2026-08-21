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

//! The recommended bridge: a pool plus `spawn_blocking`, running the
//! *synchronous* `bmlib-db` unchanged. This is what a Tauri command handler
//! would do.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};

use bmlib_db::operations::{create_tables, execute, fetch_scalar};
use bmlib_db::{params, transaction, Db, DbError, Value};
use bmlib_db_async::{open_pool, with_conn, Pool, PooledDb};

static COUNTER: AtomicU32 = AtomicU32::new(0);

/// A throwaway database file. SQLite's shared-cache in-memory mode serialises
/// writers in a way that hides the concurrency this file is about, so these
/// tests use a real file with WAL.
struct TempDb(PathBuf);

impl TempDb {
    fn new() -> Self {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let mut p = std::env::temp_dir();
        p.push(format!("bmlib_spike_{}_{}.db", std::process::id(), n));
        TempDb(p)
    }
    fn pool(&self, size: u32) -> Pool {
        open_pool(Some(&self.0), size).expect("pool")
    }
}

impl Drop for TempDb {
    fn drop(&mut self) {
        for suffix in ["", "-wal", "-shm"] {
            let _ = std::fs::remove_file(format!("{}{}", self.0.display(), suffix));
        }
    }
}

async fn setup(pool: &Pool) {
    with_conn(pool, |db| {
        create_tables(db, "CREATE TABLE IF NOT EXISTS t (v TEXT);")
    })
    .await
    .expect("schema");
}

#[tokio::test]
async fn sync_bmlib_db_code_runs_unchanged_inside_the_bridge() {
    let tmp = TempDb::new();
    let pool = tmp.pool(4);
    setup(&pool).await;

    // Note what crosses the boundary: nothing borrowed. The closure is
    // `Send + 'static`, and the `&mut dyn Db` is created inside the blocking
    // task from a connection the task owns.
    with_conn(&pool, |db| {
        transaction(db, |tx| {
            execute(tx, "INSERT INTO t (v) VALUES (?)", &params!["a"])?;
            Ok(())
        })
    })
    .await
    .unwrap();

    let got = with_conn(&pool, |db| fetch_scalar(db, "SELECT v FROM t", &[]))
        .await
        .unwrap();
    assert_eq!(got, Some(Value::Text("a".into())));
}

#[tokio::test]
async fn a_failed_transaction_rolls_back_through_the_bridge() {
    let tmp = TempDb::new();
    let pool = tmp.pool(4);
    setup(&pool).await;

    let err = with_conn(&pool, |db| {
        transaction(db, |tx| {
            execute(tx, "INSERT INTO t (v) VALUES (?)", &params!["doomed"])?;
            Err::<(), _>(DbError::abort("boom"))
        })
    })
    .await;
    assert!(err.is_err());

    let count = with_conn(&pool, |db| fetch_scalar(db, "SELECT COUNT(*) FROM t", &[]))
        .await
        .unwrap();
    assert_eq!(count, Some(Value::Int(0)));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn concurrent_transactions_all_commit() {
    // The question a Tauri app actually has: several command handlers writing
    // at once. Each gets its own pooled connection, so each is an independent
    // outermost block — which is exactly the case the Python port needed the
    // (thread, id(conn)) key to get right.
    let tmp = TempDb::new();
    let pool = tmp.pool(8);
    setup(&pool).await;

    let mut handles = Vec::new();
    for i in 0..32 {
        let pool = pool.clone();
        handles.push(tokio::spawn(async move {
            with_conn(&pool, move |db| {
                transaction(db, |tx| {
                    execute(
                        tx,
                        "INSERT INTO t (v) VALUES (?)",
                        &params![format!("row-{i}")],
                    )?;
                    Ok(())
                })
            })
            .await
        }));
    }
    for h in handles {
        h.await.expect("join").expect("write");
    }

    let count = with_conn(&pool, |db| fetch_scalar(db, "SELECT COUNT(*) FROM t", &[]))
        .await
        .unwrap();
    assert_eq!(count, Some(Value::Int(32)));
}

#[tokio::test]
async fn a_pooled_connection_is_a_db_without_a_wrapper() {
    // `PooledConnection` derefs to `rusqlite::Connection`, which already
    // implements `Db` — so the common case needs no newtype at all.
    let tmp = TempDb::new();
    let pool = tmp.pool(2);
    setup(&pool).await;

    let pool2 = pool.clone();
    tokio::task::spawn_blocking(move || {
        let mut checkout = pool2.get().unwrap();
        let db: &mut dyn Db = &mut *checkout;
        transaction(db, |tx| {
            execute(tx, "INSERT INTO t (v) VALUES (?)", &params!["direct"])?;
            Ok(())
        })
        .unwrap();
    })
    .await
    .unwrap();

    let got = with_conn(&pool, |db| fetch_scalar(db, "SELECT v FROM t", &[]))
        .await
        .unwrap();
    assert_eq!(got, Some(Value::Text("direct".into())));
}

#[tokio::test]
async fn the_newtype_makes_the_handle_itself_a_db() {
    // Needed only when the pooled handle must *be* a `Db` — a struct field, or
    // `Box<dyn Db>`. The orphan rule is why it cannot be a bare impl.
    let tmp = TempDb::new();
    let pool = tmp.pool(2);
    setup(&pool).await;

    let pool2 = pool.clone();
    tokio::task::spawn_blocking(move || {
        let mut boxed: Box<dyn Db> = Box::new(PooledDb(pool2.get().unwrap()));
        assert!(boxed.owns_commit());
        transaction(&mut *boxed, |tx| {
            execute(tx, "INSERT INTO t (v) VALUES (?)", &params!["boxed"])?;
            Ok(())
        })
        .unwrap();
    })
    .await
    .unwrap();

    let got = with_conn(&pool, |db| fetch_scalar(db, "SELECT v FROM t", &[]))
        .await
        .unwrap();
    assert_eq!(got, Some(Value::Text("boxed".into())));
}
