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

//! Prompt templates: the oracle, the refusal, and the atomic install.

use bmlib::templates::{render_template, TemplateEngine, TemplateError, TEMPLATE_SUFFIXES};
use serde_json::{json, Value};
use std::collections::BTreeMap;

const CASES: &str = include_str!("data/templates_cases.json");
const EXPECTED: &str = include_str!("data/templates_expected.json");

fn variables(case: &Value) -> BTreeMap<String, Value> {
    case["args"]["variables"]
        .as_object()
        .map(|o| o.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default()
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let wants = expected.as_array().expect("expected is a list");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(wants.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        let source = case["args"]["source"].as_str().unwrap_or_default();
        let got = json!(render_template(source, &variables(case)).expect("renders"));
        if got != want["value"] {
            failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
                serde_json::to_string(&want["value"]).unwrap_or_default(),
                serde_json::to_string(&got).unwrap_or_default()
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} diverge:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}

/// **A construct this renderer does not implement is refused by name**, not
/// rendered blank. A half-implemented loop that silently produced nothing would
/// send a model a prompt missing its examples — which is the failure the source's
/// own comment on `install_defaults` describes: not a not-found but a prompt
/// missing its second half, sent with nothing logged.
#[test]
fn an_unsupported_construct_is_refused_by_name() {
    let vars = BTreeMap::new();
    let error =
        render_template("{% for x in items %}{{ x }}{% endfor %}", &vars).expect_err("refused");
    match &error {
        TemplateError::Unsupported { construct, offset } => {
            assert!(construct.contains("for"), "{construct}");
            assert_eq!(*offset, 0);
        }
        other => panic!("wrong error: {other:?}"),
    }
    // The message says what was refused and why the alternative was rejected.
    let message = error.to_string();
    assert!(message.contains("does not"), "{message}");

    // An `{{ }}` block that never closes is refused too, rather than taken as text.
    assert!(matches!(
        render_template("open {{ name", &vars),
        Err(TemplateError::Unclosed { .. })
    ));
}

/// **A literal brace is text**, because a prompt may hold a JSON example, a LaTeX
/// fragment or a regex — which is why the source's own renderer substitutes by
/// replacement rather than through a format string.
#[test]
fn a_literal_brace_is_not_syntax() {
    let vars = BTreeMap::new();
    assert_eq!(
        render_template(r#"{"k": 1}"#, &vars).expect("renders"),
        r#"{"k": 1}"#
    );
    assert_eq!(
        render_template(r"\frac{1}{2}", &vars).expect("renders"),
        r"\frac{1}{2}"
    );
    // A lone closing brace needs no partner.
    assert_eq!(render_template("a } b", &vars).expect("renders"), "a } b");
}

/// A Jinja2 comment renders as nothing, so dropping it is the behaviour rather
/// than a refusal. An unclosed one is refused.
#[test]
fn a_comment_renders_as_nothing() {
    let vars = BTreeMap::new();
    assert_eq!(
        render_template("a{# hidden #}b", &vars).expect("renders"),
        "ab"
    );
    assert!(matches!(
        render_template("a{# never closes", &vars),
        Err(TemplateError::Unclosed { .. })
    ));
}

/// An undefined name renders as the **empty string**, which is Jinja2's default
/// and the safer choice for a prompt: refusing the render would fail an analysis
/// over a variable the caller may not have meant to pass.
#[test]
fn an_undefined_name_renders_as_nothing() {
    let vars = BTreeMap::new();
    assert_eq!(
        render_template("[{{ absent }}]", &vars).expect("renders"),
        "[]"
    );
    // And a missing path inside a present object does too.
    let mut vars = BTreeMap::new();
    vars.insert("a".to_string(), json!({"b": "deep"}));
    assert_eq!(
        render_template("[{{ a.z }}]", &vars).expect("renders"),
        "[]"
    );
    assert_eq!(
        render_template("{{ a.not.there }}", &vars).expect("renders"),
        ""
    );
}

/// Values render with **Python's spellings**, since the source's `str()` produces
/// them and a prompt comparing against one has to agree.
#[test]
fn values_render_with_python_spellings() {
    let mut vars = BTreeMap::new();
    vars.insert("t".to_string(), json!(true));
    vars.insert("f".to_string(), json!(false));
    vars.insert("n".to_string(), Value::Null);
    vars.insert("i".to_string(), json!(42));
    vars.insert("s".to_string(), json!("text"));
    assert_eq!(
        render_template("{{ t }} {{ f }} {{ n }} {{ i }} {{ s }}", &vars).expect("renders"),
        "True False None 42 text"
    );
}

/// Trailing newlines are **kept**, which is the environment's
/// `keep_trailing_newline=True` — a prompt ending in a newline is a different
/// prompt from one that does not.
#[test]
fn a_trailing_newline_is_kept() {
    let vars = BTreeMap::new();
    assert_eq!(render_template("line\n", &vars).expect("renders"), "line\n");
    assert_eq!(
        render_template("line\n\n", &vars).expect("renders"),
        "line\n\n"
    );
    assert_eq!(render_template("line", &vars).expect("renders"), "line");
}

// ---------------------------------------------------------------------------
// The engine and its two directories
// ---------------------------------------------------------------------------

/// A temporary directory that cleans up after itself.
struct TempDir(std::path::PathBuf);

impl TempDir {
    fn new(label: &str) -> Self {
        let unique = format!(
            "bmlib-tpl-{label}-{}-{:x}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or_default()
        );
        let path = std::env::temp_dir().join(unique);
        std::fs::create_dir_all(&path).expect("temp dir");
        TempDir(path)
    }
    fn path(&self) -> &std::path::Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// The **user directory wins**, and the default is the fallback — the whole point
/// of the two-directory lookup.
#[test]
fn the_user_directory_overrides_the_default() {
    let user = TempDir::new("user");
    let defaults = TempDir::new("defaults");
    std::fs::write(defaults.path().join("prompt.txt"), "DEFAULT {{ v }}").expect("write");
    let engine = TemplateEngine::new(
        Some(user.path().to_path_buf()),
        Some(defaults.path().to_path_buf()),
    );

    let mut vars = BTreeMap::new();
    vars.insert("v".to_string(), json!("x"));
    assert_eq!(
        engine.render("prompt.txt", &vars).expect("renders"),
        "DEFAULT x"
    );
    assert!(engine.has_template("prompt.txt"));

    // The user's own file shadows it.
    std::fs::write(user.path().join("prompt.txt"), "USER {{ v }}").expect("write");
    assert_eq!(
        engine.render("prompt.txt", &vars).expect("renders"),
        "USER x"
    );

    // A name in neither is a not-found.
    assert!(!engine.has_template("absent.txt"));
    assert!(matches!(
        engine.render("absent.txt", &vars),
        Err(TemplateError::NotFound(_))
    ));
}

/// **A dangling symlink is skipped, not overwritten.** `exists()` follows
/// symlinks, so one whose target is missing reads as absent — and a rename would
/// then replace the **link itself**, deleting the user's deliberate indirection.
#[test]
fn a_dangling_symlink_is_not_replaced() {
    let user = TempDir::new("dangling-user");
    let defaults = TempDir::new("dangling-defaults");
    std::fs::write(defaults.path().join("prompt.txt"), "DEFAULT").expect("write");

    let link = user.path().join("prompt.txt");
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(user.path().join("missing-target.txt"), &link).expect("symlink");
        let engine = TemplateEngine::new(
            Some(user.path().to_path_buf()),
            Some(defaults.path().to_path_buf()),
        );
        let installed = engine.install_defaults().expect("installs");
        assert!(installed.is_empty(), "nothing installed: {installed:?}");
        assert!(link.is_symlink(), "the link survives, and is still a link");
        assert!(!link.exists(), "and still dangles");
    }
    #[cfg(not(unix))]
    {
        let _ = link;
    }
}

/// The install copies **byte for byte**, and skips what is already there — the
/// skip being correct *because* the publish is atomic.
#[test]
fn the_install_is_byte_for_byte_and_skips_what_exists() {
    let user = TempDir::new("install-user");
    let defaults = TempDir::new("install-defaults");
    // A CRLF body, which a text round-trip would rewrite.
    let body = b"Q: {{ q }}\r\nC: {{ c }}\r\n";
    std::fs::write(defaults.path().join("prompt.j2"), body).expect("write");
    // A suffix outside the tuple is not a template.
    std::fs::write(defaults.path().join("README.md"), "not a template").expect("write");

    let engine = TemplateEngine::new(
        Some(user.path().to_path_buf()),
        Some(defaults.path().to_path_buf()),
    );
    let installed = engine.install_defaults().expect("installs");
    assert_eq!(installed.len(), 1, "{installed:?}");
    assert_eq!(
        std::fs::read(user.path().join("prompt.j2")).expect("read"),
        body,
        "the copy is byte for byte, CRLF included"
    );
    assert!(
        !user.path().join("README.md").exists(),
        "the suffix tuple is a contract"
    );

    // A second call installs nothing: the destination exists.
    assert!(engine.install_defaults().expect("installs").is_empty());
    // And a pre-existing file is left alone.
    std::fs::write(user.path().join("prompt.j2"), "MINE").expect("write");
    assert!(engine.install_defaults().expect("installs").is_empty());
    assert_eq!(
        std::fs::read_to_string(user.path().join("prompt.j2")).expect("read"),
        "MINE"
    );
    assert_eq!(TEMPLATE_SUFFIXES, &[".txt", ".j2", ".jinja2"]);
}

/// An unset directory, or a default directory that is not one, installs nothing
/// **quietly** — the two are not equally quiet in intent, but neither is an error,
/// and a mistyped `default_dir` must not report success as if it had installed
/// something.
#[test]
fn an_unusable_directory_installs_nothing() {
    let user = TempDir::new("unusable");
    // Both unset.
    assert!(TemplateEngine::default()
        .install_defaults()
        .expect("ok")
        .is_empty());
    // A default dir that does not exist.
    let engine = TemplateEngine::new(
        Some(user.path().to_path_buf()),
        Some(user.path().join("not-a-directory")),
    );
    assert!(engine.install_defaults().expect("ok").is_empty());
    // And with no user dir there is nowhere to install.
    let defaults = TempDir::new("unusable-defaults");
    let engine = TemplateEngine::new(None, Some(defaults.path().to_path_buf()));
    assert!(engine.install_defaults().expect("ok").is_empty());
}
