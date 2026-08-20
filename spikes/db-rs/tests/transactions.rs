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

//! Transaction semantics, ported from the Python suite.
//!
//! Sources: `tests/test_db.py::TestTransaction` and
//! `tests/test_backends.py::TestTransactions`. Each test names the Python test
//! it came from, so a divergence is traceable to a decision rather than to an
//! oversight.

mod common;

use bmlib_db::operations::{create_tables, execute, fetch_all, fetch_scalar};
use bmlib_db::{owns_commit, params, transaction, Db, DbError, Value};

use common::both_backends;

fn setup(db: &mut dyn Db) {
    create_tables(db, "CREATE TABLE t (v TEXT);").unwrap();
}

fn values(db: &mut dyn Db) -> Vec<String> {
    fetch_all(db, "SELECT v FROM t ORDER BY v", &[])
        .unwrap()
        .iter()
        .map(|r| r.get_str("v").unwrap().to_string())
        .collect()
}

fn insert(db: &mut dyn Db, v: &str) {
    execute(db, "INSERT INTO t (v) VALUES (?)", &params![v]).unwrap();
}

// --- test_db.py::TestTransaction ------------------------------------------

both_backends!(commit_on_success, |db: &mut dyn Db| {
    setup(db);
    transaction(db, |tx| {
        insert(tx, "committed");
        Ok(())
    })
    .unwrap();
    assert_eq!(values(db), ["committed"]);
});

both_backends!(rollback_on_error, |db: &mut dyn Db| {
    setup(db);
    let err = transaction(db, |tx| {
        insert(tx, "rollback");
        Err::<(), _>(DbError::abort("boom"))
    });
    assert!(err.is_err());
    assert!(values(db).is_empty());
});

both_backends!(
    nested_transaction_defers_commit_to_outer,
    |db: &mut dyn Db| {
        setup(db);
        // The inner block succeeding must not commit: the outer block owns the
        // commit, so its later failure takes the inner writes with it.
        let err = transaction(db, |tx| {
            transaction(tx, |inner| {
                insert(inner, "inner");
                Ok(())
            })?;
            Err::<(), _>(DbError::abort("outer fails after the inner block finished"))
        });
        assert!(err.is_err());
        assert!(values(db).is_empty());
    }
);

both_backends!(nested_transaction_commits_with_outer, |db: &mut dyn Db| {
    setup(db);
    transaction(db, |tx| {
        transaction(tx, |inner| {
            insert(inner, "inner");
            Ok(())
        })?;
        insert(tx, "outer");
        Ok(())
    })
    .unwrap();
    assert_eq!(values(db), ["inner", "outer"]);
});

// --- test_backends.py::TestTransactions -----------------------------------

both_backends!(standalone_block_commits, |db: &mut dyn Db| {
    setup(db);
    transaction(db, |tx| {
        insert(tx, "a");
        Ok(())
    })
    .unwrap();
    assert_eq!(values(db).len(), 1);
});

both_backends!(block_after_a_bare_query_still_commits, |db: &mut dyn Db| {
    // Regression guard, ported: psycopg2 opens a transaction on the first
    // statement of ANY kind, so reading the driver's status here would
    // classify this block as nested and silently skip its commit.
    //
    // In this port the guard cannot fail, because nothing reads driver
    // status — `owns_commit` is a constant per implementation. The test is
    // kept because it is the one that would catch a future change that
    // reintroduced the lookup.
    setup(db);
    fetch_all(db, "SELECT * FROM t", &[]).unwrap();
    assert!(owns_commit(db), "a bare query must not look like a block");

    transaction(db, |tx| {
        insert(tx, "a");
        Ok(())
    })
    .unwrap();
    assert_eq!(values(db).len(), 1);
});

both_backends!(failure_rolls_back, |db: &mut dyn Db| {
    setup(db);
    let err = transaction(db, |tx| {
        insert(tx, "a");
        Err::<(), _>(DbError::abort("boom"))
    });
    assert!(err.is_err());
    assert!(values(db).is_empty());
});

both_backends!(
    inner_failure_rolls_back_only_the_inner_writes,
    |db: &mut dyn Db| {
        setup(db);
        transaction(db, |tx| {
            insert(tx, "outer");
            let inner = transaction(tx, |i| {
                insert(i, "inner");
                Err::<(), _>(DbError::abort("boom"))
            });
            assert!(inner.is_err());
            Ok(())
        })
        .unwrap();
        assert_eq!(values(db), ["outer"]);
    }
);

both_backends!(owns_commit_tracks_the_block_depth, |db: &mut dyn Db| {
    // The port of `test_owns_commit_tracks_the_block_depth`. Python asks a
    // side table; here the answer is the type of the value in hand.
    assert!(owns_commit(db));
    transaction(db, |tx| {
        assert!(!owns_commit(tx));
        transaction(tx, |inner| {
            assert!(!owns_commit(inner));
            Ok(())
        })
    })
    .unwrap();
    assert!(owns_commit(db));
});

both_backends!(fetch_scalar_returns_the_first_column, |db: &mut dyn Db| {
    setup(db);
    transaction(db, |tx| {
        insert(tx, "a");
        Ok(())
    })
    .unwrap();
    assert_eq!(
        fetch_scalar(db, "SELECT v FROM t", &[]).unwrap(),
        Some(Value::Text("a".into()))
    );
    assert_eq!(
        fetch_scalar(db, "SELECT v FROM t WHERE v = ?", &params!["nope"]).unwrap(),
        None
    );
});

// --- The property the spike exists to test --------------------------------

both_backends!(a_helper_composes_in_either_position, |db: &mut dyn Db| {
    // This is the shape `publications.sync()` depends on: a helper that opens
    // its own transaction, called standalone AND inside a batch block that
    // pays one commit for the lot. In Python the helper receives the same
    // `conn` either way and the depth table decides; here it receives a
    // connection or a savepoint and the type decides.
    fn store(db: &mut dyn Db, v: &str) -> bmlib_db::Result<()> {
        transaction(db, |tx| {
            execute(tx, "INSERT INTO t (v) VALUES (?)", &params![v])?;
            Ok(())
        })
    }

    setup(db);
    store(db, "standalone").unwrap();

    transaction(db, |tx| {
        store(tx, "batched-1")?;
        store(tx, "batched-2")?;
        Ok(())
    })
    .unwrap();

    assert_eq!(values(db), ["batched-1", "batched-2", "standalone"]);
});

both_backends!(a_batch_block_rolls_back_every_helper, |db: &mut dyn Db| {
    fn store(db: &mut dyn Db, v: &str) -> bmlib_db::Result<()> {
        transaction(db, |tx| {
            execute(tx, "INSERT INTO t (v) VALUES (?)", &params![v])?;
            Ok(())
        })
    }

    setup(db);
    let err = transaction(db, |tx| {
        store(tx, "one")?;
        store(tx, "two")?;
        Err::<(), _>(DbError::abort("the day failed after both records stored"))
    });
    assert!(err.is_err());
    assert!(values(db).is_empty(), "the whole batch must be gone");
});

both_backends!(savepoints_nest_to_depth_four, |db: &mut dyn Db| {
    setup(db);
    transaction(db, |a| {
        insert(a, "1");
        transaction(a, |b| {
            insert(b, "2");
            transaction(b, |c| {
                insert(c, "3");
                let d = transaction(c, |d| {
                    insert(d, "4");
                    Err::<(), _>(DbError::abort("deepest fails"))
                });
                assert!(d.is_err());
                Ok(())
            })
        })
    })
    .unwrap();
    assert_eq!(values(db), ["1", "2", "3"]);
});
