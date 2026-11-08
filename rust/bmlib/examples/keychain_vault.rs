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

//! Reads or writes one keychain item through `secrets::KeyringVault`, for
//! `tests/test_secrets_interop.py` (#686): Python and this binary take turns
//! on one item, on the CI runner's real keychain.
//!
//! ```text
//! keychain_vault <service> read          # the item's text, or nothing and exit 3
//! keychain_vault <service> write <text>
//! ```
//!
//! A failure prints `<kind>: <message>` to stderr and exits 1.

use std::process::ExitCode;

use bmlib::secrets::{KeyringVault, Vault, API_KEYS_ACCOUNT};

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let vault = match args.first() {
        Some(service) => KeyringVault::new(service, API_KEYS_ACCOUNT),
        None => return usage(),
    };
    let done = match (args.get(1).map(String::as_str), args.get(2), args.len()) {
        (Some("read"), None, 2) => vault.read().map(|text| match text {
            Some(text) => {
                print!("{text}");
                ExitCode::SUCCESS
            }
            None => ExitCode::from(3),
        }),
        (Some("write"), Some(text), 3) => vault.write(text).map(|()| ExitCode::SUCCESS),
        _ => return usage(),
    };
    done.unwrap_or_else(|e| {
        eprintln!("{}: {}", e.kind().as_str(), e.message());
        ExitCode::FAILURE
    })
}

fn usage() -> ExitCode {
    eprintln!("usage: keychain_vault <service> (read | write <text>)");
    ExitCode::from(2)
}
