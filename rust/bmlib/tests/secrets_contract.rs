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

//! The shared key-list contract, `tests/fixtures/secret_key_list.json` (the
//! same file Python's `tests/test_secrets.py` reads, #634): how the one
//! keychain item reads and is written, and what a `KeyBundle` does over it.

use bmlib::secrets::{
    read_key_list, write_key_list, KeyBundle, KeyList, SecretError, SecretStore, Vault,
};
use serde_json::Value;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Mutex;

const FIXTURE: &str = include_str!("../../../tests/fixtures/secret_key_list.json");

/// The fixture, parsed.
fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("fixture is JSON")
}

/// A fixture object of strings as a key list.
fn key_list(v: &Value) -> KeyList {
    v.as_object()
        .expect("an object")
        .iter()
        .map(|(k, v)| (k.clone(), v.as_str().expect("a string").to_string()))
        .collect()
}

#[test]
fn reads_agree_with_the_fixture() {
    let fx = fixture();
    let cases = fx["read"].as_array().expect("read");
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().expect("name");
        let got = read_key_list(case["text"].as_str().expect("text"));
        let expect = &case["expect"];
        if expect["error"] == "unreadable" {
            assert!(got.is_err(), "{name}: expected unreadable, got {got:?}");
        } else {
            assert_eq!(got, Ok(key_list(&expect["keys"])), "{name}");
        }
    }
}

#[test]
fn writes_agree_with_the_fixture() {
    let fx = fixture();
    let cases = fx["write"].as_array().expect("write");
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().expect("name");
        let keys = key_list(&case["keys"]);
        let text = write_key_list(&keys);
        assert_eq!(text, case["text"].as_str().expect("text"), "{name}");
        assert_eq!(read_key_list(&text), Ok(keys), "{name}: reads back");
    }
}

/// An in-memory vault that counts reads and fails a write when told to.
#[derive(Default)]
struct FakeVault {
    item: Mutex<Option<String>>,
    reads: AtomicU32,
    fail_write: AtomicBool,
}

impl Vault for FakeVault {
    fn read(&self) -> Result<Option<String>, SecretError> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        Ok(self.item.lock().unwrap().clone())
    }
    fn write(&self, text: &str) -> Result<(), SecretError> {
        if self.fail_write.load(Ordering::SeqCst) {
            return Err(SecretError("denied".into()));
        }
        *self.item.lock().unwrap() = Some(text.to_string());
        Ok(())
    }
}

#[test]
fn bundles_agree_with_the_fixture() {
    let fx = fixture();
    let cases = fx["bundle"].as_array().expect("bundle");
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().expect("name");
        let vault = FakeVault::default();
        *vault.item.lock().unwrap() = case["item"].as_str().map(str::to_string);
        let store = KeyBundle::new(vault);
        for (i, step) in case["steps"].as_array().expect("steps").iter().enumerate() {
            let provider = step["provider"].as_str().expect("provider");
            let fail = step["fail_write"].as_bool().unwrap_or(false);
            store.vault().fail_write.store(fail, Ordering::SeqCst);
            let got: Result<Value, SecretError> = match step["op"].as_str().expect("op") {
                "get" => store
                    .get(provider)
                    .map(|key| serde_json::json!({ "key": key })),
                "set" => store
                    .set(provider, step["key"].as_str().expect("key"))
                    .map(|()| Value::from("ok")),
                "delete" => store.delete(provider).map(|()| Value::from("ok")),
                op => panic!("{name}: unknown op {op}"),
            };
            store.vault().fail_write.store(false, Ordering::SeqCst);
            let got = got.unwrap_or_else(|_| Value::from("error"));
            assert_eq!(got, step["expect"], "{name}: step {i}");
        }
        let vault = store.vault();
        assert_eq!(
            vault.item.lock().unwrap().as_deref(),
            case["item_after"].as_str(),
            "{name}: item after"
        );
        assert_eq!(
            Value::from(vault.reads.load(Ordering::SeqCst)),
            case["reads"],
            "{name}: reads"
        );
    }
}
