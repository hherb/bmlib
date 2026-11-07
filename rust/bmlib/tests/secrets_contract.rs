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

mod common;

use bmlib::secrets::{
    read_key_list, write_key_list, KeyBundle, KeyList, SecretError, SecretErrorKind, SecretStore,
    Vault,
};
use serde_json::Value;
use std::sync::atomic::{AtomicU32, Ordering};
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
        if expect["error"] == "unreadable-item" {
            let e = got.expect_err(name);
            assert_eq!(e.kind(), SecretErrorKind::UnreadableItem, "{name}");
            assert_eq!(
                e.message(),
                "the keychain item is not a valid key list",
                "{name}"
            );
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

/// Every error kind, in the fixture's `kinds` order (`every_kind_is_listed`
/// in the crate fails to compile on a kind added later).
const KINDS: [SecretErrorKind; 3] = [
    SecretErrorKind::UnreadableItem,
    SecretErrorKind::AmbiguousItem,
    SecretErrorKind::Store,
];

#[test]
fn the_kinds_are_the_fixtures() {
    let names: Vec<Value> = KINDS.iter().map(|k| Value::from(k.as_str())).collect();
    assert_eq!(Value::from(names), fixture()["kinds"]);
}

/// The kind a fixture names.
fn kind(name: &str) -> SecretErrorKind {
    *KINDS
        .iter()
        .find(|k| k.as_str() == name)
        .unwrap_or_else(|| panic!("unknown kind {name}"))
}

/// An in-memory vault that counts reads and fails a read or a write, with a
/// given kind, when told to.
#[derive(Default)]
struct FakeVault {
    item: Mutex<Option<String>>,
    reads: AtomicU32,
    fail_read: Mutex<Option<SecretErrorKind>>,
    fail_write: Mutex<Option<SecretErrorKind>>,
}

impl Vault for FakeVault {
    fn read(&self) -> Result<Option<String>, SecretError> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        if let Some(k) = *self.fail_read.lock().unwrap() {
            return Err(SecretError::with_kind(k, "locked"));
        }
        Ok(self.item.lock().unwrap().clone())
    }
    fn write(&self, text: &str) -> Result<(), SecretError> {
        if let Some(k) = *self.fail_write.lock().unwrap() {
            return Err(SecretError::with_kind(k, "denied"));
        }
        *self.item.lock().unwrap() = Some(text.to_string());
        Ok(())
    }
}

/// Every field a bundle step may carry; a misspelt one would read as absent.
const STEP_FIELDS: [&str; 7] = [
    "op",
    "provider",
    "key",
    "expect",
    "fail_write",
    "fail_read",
    "another_writer",
];

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
            for field in step.as_object().expect("a step").keys() {
                assert!(
                    STEP_FIELDS.contains(&field.as_str()),
                    "{name}: step {i}: {field}"
                );
            }
            if let Some(text) = step["another_writer"].as_str() {
                *store.vault().item.lock().unwrap() = Some(text.to_string());
            }
            let provider = step["provider"].as_str().expect("provider");
            // A field that is there but not a kind's name panics rather than disarming.
            let fail = |field: &str| step.get(field).map(|v| kind(v.as_str().expect(field)));
            *store.vault().fail_write.lock().unwrap() = fail("fail_write");
            *store.vault().fail_read.lock().unwrap() = fail("fail_read");
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
            *store.vault().fail_write.lock().unwrap() = None;
            *store.vault().fail_read.lock().unwrap() = None;
            let got = got.unwrap_or_else(|e| serde_json::json!({ "error": e.kind().as_str() }));
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

/// Python's `test_saving_over_an_unreadable_item_says_so_once`: one WARNING
/// under `bmlib.secrets`, naming the item's state and never its contents
/// (`sk-leaky`), so a host's logger receives it (#688).
#[test]
fn saving_over_an_unreadable_item_says_so_once() {
    let vault = FakeVault::default();
    *vault.item.lock().unwrap() = Some("not json sk-leaky".to_string());
    let store = KeyBundle::new(vault);
    common::log_capture::start();
    store
        .set("openai", "k1")
        .expect("the set replaces the item");
    let got: Vec<(log::Level, String)> = common::log_capture::take("bmlib.secrets")
        .into_iter()
        .map(|record| (record.level, record.message))
        .collect();
    assert_eq!(
        got,
        [(
            log::Level::Warn,
            "the keychain item is not a valid key list; replacing it with the key being saved"
                .to_string()
        )]
    );
}
