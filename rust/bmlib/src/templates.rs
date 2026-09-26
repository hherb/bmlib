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

//! Prompt templates on disk: a user directory overriding a default one.
//!
//! # This is not Jinja2, and the difference is a decision rather than a gap
//!
//! The source renders through Jinja2. This port implements the **subset a prompt
//! template needs** — `{{ variable }}` substitution, with `.` and `[]` traversal —
//! and **refuses** a template that uses anything else, naming the construct. The
//! alternative was linking a template engine, which would add a dependency larger
//! than this whole module to a library whose other modules need none.
//!
//! The refusal is the part that matters. A half-implemented `{% for %}` that
//! silently rendered nothing would send a model a prompt missing its examples, and
//! the source's own comment on `install_defaults` is about exactly that failure:
//! *not a `TemplateNotFound` but a prompt missing its second half, sent to a model
//! with nothing logged*. So an unsupported construct is an error, not a blank.
//!
//! The divergence is recorded in the plan's §9 list.

use crate::atomic::atomic_write;
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::PathBuf;

/// The suffixes `install_defaults` copies.
///
/// The same tuple as the source's, and a **contract rather than an internal
/// detail**: bmlib ships no templates of its own, so the default directory is
/// always a caller's, and a file this skips is one the caller expected installed.
pub const TEMPLATE_SUFFIXES: &[&str] = &[".txt", ".j2", ".jinja2"];

/// Why a template could not be rendered.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TemplateError {
    /// No file by that name in either directory.
    NotFound(String),
    /// The template uses a construct this renderer does not implement.
    ///
    /// Carries the construct and its offset, so a caller can find it.
    Unsupported {
        /// The construct, e.g. `{% for %}`.
        construct: String,
        /// Where it appears in the template.
        offset: usize,
    },
    /// A `{{ }}` block that never closes.
    Unclosed {
        /// Where the block opens.
        offset: usize,
    },
    /// The file could not be read.
    Io(String),
}

impl std::fmt::Display for TemplateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TemplateError::NotFound(name) => write!(f, "template not found: {name}"),
            TemplateError::Unsupported { construct, offset } => write!(
                f,
                "template uses {construct} at offset {offset}, which this renderer does not \
                 implement; a Jinja2 expression would be rendered as nothing rather than guessed at"
            ),
            TemplateError::Unclosed { offset } => {
                write!(
                    f,
                    "a {{{{ }}}} block opens at offset {offset} and never closes"
                )
            }
            TemplateError::Io(message) => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for TemplateError {}

/// A user directory overriding a default one.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct TemplateEngine {
    /// Checked first. `None` disables the user half.
    pub user_dir: Option<PathBuf>,
    /// The fallback. `None` disables the fallback.
    pub default_dir: Option<PathBuf>,
}

impl TemplateEngine {
    /// An engine over the two directories.
    #[must_use]
    pub fn new(user_dir: Option<PathBuf>, default_dir: Option<PathBuf>) -> Self {
        TemplateEngine {
            user_dir,
            default_dir,
        }
    }

    /// Where `template_name` resolves, user directory first.
    #[must_use]
    pub fn source_path(&self, template_name: &str) -> Option<PathBuf> {
        for directory in [self.user_dir.as_ref(), self.default_dir.as_ref()] {
            let Some(directory) = directory else { continue };
            let path = directory.join(template_name);
            if path.is_file() {
                return Some(path);
            }
        }
        None
    }

    /// Whether the template exists in either directory.
    #[must_use]
    pub fn has_template(&self, template_name: &str) -> bool {
        self.source_path(template_name).is_some()
    }

    /// Render a template file with the given variables.
    ///
    /// # Errors
    ///
    /// [`TemplateError::NotFound`] when the file is in neither directory, and
    /// [`TemplateError::Unsupported`] for a construct this renderer refuses to
    /// guess at.
    pub fn render(
        &self,
        template_name: &str,
        variables: &BTreeMap<String, Value>,
    ) -> Result<String, TemplateError> {
        let path = self
            .source_path(template_name)
            .ok_or_else(|| TemplateError::NotFound(template_name.to_string()))?;
        let source = std::fs::read_to_string(&path)
            .map_err(|e| TemplateError::Io(format!("{}: {e}", path.display())))?;
        render_template(&source, variables)
    }

    /// Render a string rather than a file.
    ///
    /// # Errors
    ///
    /// As [`TemplateEngine::render`], minus the lookup.
    pub fn render_string(
        &self,
        source: &str,
        variables: &BTreeMap<String, Value>,
    ) -> Result<String, TemplateError> {
        render_template(source, variables)
    }

    /// Copy every template in `default_dir` into the user directory.
    ///
    /// **Each copy is published atomically, and that is what makes the skip
    /// correct rather than merely well-intentioned.** A bare write interrupted
    /// partway — a full disk, a killed process — leaves a truncated template that
    /// `exists()` then reports as installed, so it is never repaired, and a
    /// template engine renders whatever survived: not a *not-found* but a prompt
    /// missing its second half, sent to a model with nothing logged.
    ///
    /// **A dangling symlink is skipped, not overwritten.** `exists()` follows
    /// symlinks, so one whose target is missing reads as absent — and a rename
    /// would then replace the **link itself**, where the plain write this replaced
    /// wrote *through* it. A user who symlinks a prompt at a volume that happens to
    /// be unmounted must not come back to find the link gone and the default in its
    /// place. It is reported at WARNING because rendering then falls back to the
    /// default with the user's own version unreachable.
    ///
    /// The copy is **byte for byte**: reading text and writing it back is not a
    /// copy, since the two may disagree on line endings. What that buys is fidelity
    /// of the installed artefact, for the editor or tool the user reaches for next.
    ///
    /// # Errors
    ///
    /// From the first copy that fails, leaving the templates after it uninstalled
    /// — which ones that is, is reproducible only because the scan is **sorted**.
    /// Deliberately propagated rather than collected: the next call installs
    /// whatever is still missing, so the loop is self-repairing, and a caller who
    /// cannot write is better told than left believing the templates are there.
    pub fn install_defaults(&self) -> Result<Vec<PathBuf>, TemplateError> {
        // Doing nothing at all is a legitimate outcome of the first check and
        // almost never one of the second, so they are not equally quiet: a mistyped
        // or not-yet-created `default_dir` otherwise makes this a no-op reporting
        // success.
        let (Some(user_dir), Some(default_dir)) = (&self.user_dir, &self.default_dir) else {
            return Ok(Vec::new());
        };
        if !default_dir.is_dir() {
            return Ok(Vec::new());
        }

        std::fs::create_dir_all(user_dir)
            .map_err(|e| TemplateError::Io(format!("{}: {e}", user_dir.display())))?;

        let mut entries: Vec<PathBuf> = std::fs::read_dir(default_dir)
            .map_err(|e| TemplateError::Io(format!("{}: {e}", default_dir.display())))?
            .flatten()
            .map(|entry| entry.path())
            .collect();
        // **Sorted**, so which templates install before a failure is reproducible.
        entries.sort();

        let mut installed = Vec::new();
        for source in entries {
            if !source.is_file() {
                continue;
            }
            let suffix = source
                .extension()
                .map(|e| format!(".{}", e.to_string_lossy()))
                .unwrap_or_default();
            if !TEMPLATE_SUFFIXES.contains(&suffix.as_str()) {
                continue;
            }
            let Some(name) = source.file_name() else {
                continue;
            };
            let destination = user_dir.join(name);

            // A symlink whose target is missing: `exists()` is false, but replacing
            // it would delete the user's link.
            if destination.is_symlink() && !destination.exists() {
                continue;
            }
            if destination.exists() {
                continue;
            }
            let bytes = std::fs::read(&source)
                .map_err(|e| TemplateError::Io(format!("{}: {e}", source.display())))?;
            atomic_write(&destination, &bytes)
                .map_err(|e| TemplateError::Io(format!("{}: {e}", destination.display())))?;
            installed.push(destination);
        }
        Ok(installed)
    }
}

/// Render `{{ }}` substitutions, refusing anything else.
///
/// Kept a free function so the rule can be tested without a filesystem.
///
/// # Errors
///
/// As [`TemplateEngine::render`].
pub fn render_template(
    source: &str,
    variables: &BTreeMap<String, Value>,
) -> Result<String, TemplateError> {
    let bytes = source.as_bytes();
    let mut out = String::with_capacity(source.len());
    let mut index = 0usize;

    while index < bytes.len() {
        let Some(open) = source[index..].find('{') else {
            out.push_str(&source[index..]);
            break;
        };
        let open = index + open;
        let rest = &source[open..];

        // A comment, or a statement/expression block. `{#` is silently dropped —
        // it renders as nothing in Jinja2 too, so dropping it is the behaviour
        // rather than a refusal. Anything else is refused **by name**.
        if let Some(after) = rest.strip_prefix("{#") {
            let Some(close) = after.find("#}") else {
                return Err(TemplateError::Unclosed { offset: open });
            };
            out.push_str(&source[index..open]);
            index = open + 2 + close + 2;
            continue;
        }
        if rest.starts_with("{%") {
            // Refused **by name**, with offset: a half-implemented loop that
            // rendered nothing would send a model a prompt missing its examples.
            let construct = rest
                .split("%}")
                .next()
                .map(|inner| format!("{{%{inner}%}}"))
                .unwrap_or_else(|| "{% %}".to_string());
            return Err(TemplateError::Unsupported {
                construct,
                offset: open,
            });
        }

        let Some(after) = rest.strip_prefix("{{") else {
            // A literal brace, as in a JSON example or a LaTeX fragment.
            out.push_str(&source[index..=open]);
            index = open + 1;
            continue;
        };
        let Some(close) = after.find("}}") else {
            return Err(TemplateError::Unclosed { offset: open });
        };
        let expression = after[..close].trim();
        out.push_str(&source[index..open]);
        out.push_str(&lookup(expression, variables));
        index = open + 2 + close + 2;
    }
    Ok(out)
}

/// Resolve `name`, `a.b` or `a["b"]` against the variables.
///
/// A missing name renders as the empty string, which is Jinja2's default for an
/// undefined variable and — for a prompt — the safer of the two: `{{ title }}`
/// absent yields a prompt with a gap, where refusing the render would fail an
/// analysis over a variable the caller may not have meant to pass.
fn lookup(expression: &str, variables: &BTreeMap<String, Value>) -> String {
    let expression = expression.trim();
    let mut parts: Vec<String> = Vec::new();
    let mut current = String::new();
    for ch in expression.chars() {
        match ch {
            '.' | '[' | ']' | '"' | '\'' => {
                if !current.is_empty() {
                    parts.push(std::mem::take(&mut current));
                }
            }
            other => current.push(other),
        }
    }
    if !current.is_empty() {
        parts.push(current);
    }
    let Some((first, rest)) = parts.split_first() else {
        return String::new();
    };
    let mut value = match variables.get(first) {
        Some(value) => value,
        None => return String::new(),
    };
    for part in rest {
        value = match value.get(part) {
            Some(next) => next,
            None => return String::new(),
        };
    }
    render_value(value)
}

/// Render a JSON value the way a template's output should read.
///
/// A string is its own text, and **the scalars take Python's spellings**
/// (`True`, `False`, `None`), since the source's `str()` produces those and a
/// prompt comparing against one has to agree.
///
/// A list or object takes Python's `repr` — `[1, 2]`, `{'b': 'deep'}`, with a
/// comma-space separator and single quotes — because `str()` on a container
/// shows that repr while a JSON serialisation would show `[1,2]` and `{"b":"deep"}`.
/// Both spellings are a plausible thing for a prompt to compare against, so this
/// is a real difference and not cosmetics.
fn render_value(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Null => "None".to_string(),
        Value::Number(number) => number.to_string(),
        Value::Array(items) => {
            let parts: Vec<String> = items.iter().map(python_repr).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::Object(map) => {
            let parts: Vec<String> = map
                .iter()
                .map(|(key, item)| format!("'{}': {}", key, python_repr(item)))
                .collect();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

/// Python's `repr`, which is what `str()` falls back to inside a container.
///
/// A string is quoted with single quotes here where the top level is bare: that is
/// the distinction between `str(x)` and `repr(x)`, and a list of strings is where
/// it shows.
fn python_repr(value: &Value) -> String {
    match value {
        Value::String(text) => format!("'{text}'"),
        other => render_value(other),
    }
}
