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

//! Does the *shape* of `transaction()` survive being made async?
//!
//! Exercised against the toy `Send` backend, because no `rusqlite` type can
//! inhabit `AsyncDb` — see the `send_evidence` doc-tests. What is under test
//! here is the type system, not a database.

use bmlib_db::DbError;
use bmlib_db_async::async_db::toy::MemDb;
use bmlib_db_async::async_db::transaction;

#[tokio::test]
async fn a_lending_async_closure_composes() {
    // The case that could not be written generically before Rust 1.85: the
    // future returned by the closure borrows the block passed into it.
    let mut db = MemDb::default();
    transaction(&mut db, async |tx| {
        tx.execute_raw("INSERT a", &[]).await?;
        Ok(())
    })
    .await
    .unwrap();
    assert_eq!(db.rows(), ["a"]);
}

#[tokio::test]
async fn nested_async_blocks_commit_with_the_outer_one() {
    let mut db = MemDb::default();
    transaction(&mut db, async |tx| {
        transaction(tx, async |inner| {
            inner.execute_raw("INSERT inner", &[]).await?;
            Ok(())
        })
        .await?;
        tx.execute_raw("INSERT outer", &[]).await?;
        Ok(())
    })
    .await
    .unwrap();
    assert_eq!(db.rows(), ["inner", "outer"]);
}

#[tokio::test]
async fn an_inner_failure_discards_only_the_inner_writes() {
    let mut db = MemDb::default();
    transaction(&mut db, async |tx| {
        tx.execute_raw("INSERT outer", &[]).await?;
        let inner = transaction(tx, async |i| {
            i.execute_raw("INSERT inner", &[]).await?;
            Err::<(), _>(DbError::abort("boom"))
        })
        .await;
        assert!(inner.is_err());
        Ok(())
    })
    .await
    .unwrap();
    assert_eq!(db.rows(), ["outer"]);
}

#[tokio::test]
async fn an_outer_failure_discards_everything() {
    let mut db = MemDb::default();
    let err = transaction(&mut db, async |tx| {
        transaction(tx, async |i| {
            i.execute_raw("INSERT inner", &[]).await?;
            Ok(())
        })
        .await?;
        Err::<(), _>(DbError::abort("outer fails after the inner block finished"))
    })
    .await;
    assert!(err.is_err());
    assert!(db.rows().is_empty());
}

#[tokio::test]
async fn a_helper_composes_in_either_position() {
    // The same property the sync spike tests: one helper, callable standalone
    // or inside a batch block.
    async fn store(
        db: &mut (dyn bmlib_db_async::async_db::AsyncDb + Send + '_),
        v: &str,
    ) -> bmlib_db::Result<()> {
        let sql = format!("INSERT {v}");
        transaction(db, async move |tx| {
            tx.execute_raw(&sql, &[]).await?;
            Ok(())
        })
        .await
    }

    let mut db = MemDb::default();
    store(&mut db, "standalone").await.unwrap();
    transaction(&mut db, async |tx| {
        store(tx, "batched-1").await?;
        store(tx, "batched-2").await?;
        Ok(())
    })
    .await
    .unwrap();
    assert_eq!(db.rows(), ["standalone", "batched-1", "batched-2"]);
}
