# bmlib::templates — Prompt Templates

Prompt templates loaded from disk, with a user directory overriding a default
one, so a user can edit a prompt without touching the application. The Python
page is [templates.md](../../../../docs/manual/templates.md); this one
describes the Rust API and where it differs — chiefly that **this is not
Jinja2**: it renders `{{ variable }}` substitutions and refuses every other
construct by name.

| Need | Feature |
|---|---|
| `TemplateEngine`, `render_template`, `TemplateError`, `TEMPLATE_SUFFIXES` | none |

As in Python, bmlib ships no templates of its own: both directories are the
caller's.

## Rendering

`TemplateEngine::new(user_dir, default_dir)` takes two `Option<PathBuf>`s;
either may be `None`. A template name resolves in the user directory first,
then the default one (`source_path`, `has_template`). Variables are a
`BTreeMap<String, serde_json::Value>`:

```rust
use std::collections::BTreeMap;
use bmlib::templates::{TemplateEngine, TemplateError};
use serde_json::json;

let root = std::env::temp_dir().join("bmlib-manual-templates-render");
let (user, defaults) = (root.join("user"), root.join("defaults"));
std::fs::create_dir_all(&user)?;
std::fs::create_dir_all(&defaults)?;
std::fs::write(defaults.join("score.txt"), "Title: {{ title }}\nYear: {{ paper.year }}\n")?;

let engine = TemplateEngine::new(Some(user.clone()), Some(defaults.clone()));
let vars: BTreeMap<String, _> = [
    ("title".to_string(), json!("Statins and LDL")),
    ("paper".to_string(), json!({"year": 2021})),
]
.into();
assert_eq!(engine.render("score.txt", &vars)?, "Title: Statins and LDL\nYear: 2021\n");

// The user's copy wins once there is one.
std::fs::write(user.join("score.txt"), "Rate: {{ title }}")?;
assert_eq!(engine.render("score.txt", &vars)?, "Rate: Statins and LDL");

assert!(matches!(engine.render("absent.txt", &vars), Err(TemplateError::NotFound(_))));
# std::fs::remove_dir_all(&root).ok();
# Ok::<(), Box<dyn std::error::Error>>(())
```

`render_string(source, vars)` on the engine, or the free function
`render_template(source, vars)`, renders a string with no lookup.

## What the renderer accepts

- `{{ name }}`, `{{ a.b }}` and `{{ a["b"] }}` (or `a['b']`) look a value up,
  descending into JSON objects. A name that is not there renders as `""`, as
  Jinja2's default undefined does.
- A value renders as Python's `str()` would: `true` as `True`, `null` as
  `None`, a list as `[1, 2]`, an object as `{'b': 'deep'}`. A prompt therefore
  reads the same as the one Python rendered.
- `{# … #}` is a comment and renders as nothing.
- A lone `{` or `}` is literal text, so JSON examples in a prompt are safe.
- **Anything else is an error, not a blank.** A `{% … %}` block (a loop, a
  condition) is `TemplateError::Unsupported { construct, offset }`, and a `{{`
  or `{#` that never closes is `TemplateError::Unclosed { offset }`.
  Filters (`{{ x | upper }}`) are not implemented either: the whole expression
  is read as a name, so it renders as `""`.

```rust
use std::collections::BTreeMap;
use bmlib::templates::{render_template, TemplateError};
use serde_json::json;

let vars: BTreeMap<String, _> = [("blinded".to_string(), json!(true))].into();
assert_eq!(render_template("Blinded: {{ blinded }} {\"k\": 1}", &vars)?, "Blinded: True {\"k\": 1}");
assert_eq!(render_template("{# note #}{{ missing }}.", &vars)?, ".");

let err = render_template("{% for a in authors %}{{ a }}{% endfor %}", &vars).unwrap_err();
assert!(matches!(err, TemplateError::Unsupported { offset: 0, .. }));
# Ok::<(), TemplateError>(())
```

A Python template that uses loops, conditions, filters or `default(...)`
must be rewritten for this renderer, or rendered by the caller beforehand and
passed in as a variable.

## Installing the defaults

`install_defaults()` copies every file directly in `default_dir` whose suffix
is in `TEMPLATE_SUFFIXES` (`.txt`, `.j2`, `.jinja2`) into `user_dir`, creating
it, and returns the paths it installed:

```rust
use bmlib::templates::TemplateEngine;

let root = std::env::temp_dir().join("bmlib-manual-templates-install");
let (user, defaults) = (root.join("user"), root.join("defaults"));
std::fs::create_dir_all(&defaults)?;
std::fs::write(defaults.join("a.txt"), "A")?;
std::fs::write(defaults.join("b.j2"), "B")?;
std::fs::write(defaults.join("notes.md"), "not a template")?;

let engine = TemplateEngine::new(Some(user.clone()), Some(defaults));
assert_eq!(engine.install_defaults()?, vec![user.join("a.txt"), user.join("b.j2")]);
assert!(engine.install_defaults()?.is_empty()); // never overwrites
# std::fs::remove_dir_all(&root).ok();
# Ok::<(), Box<dyn std::error::Error>>(())
```

As in Python: each copy is byte for byte and published atomically (see
[atomic.md](atomic.md)), so an interrupted copy leaves nothing rather than a
truncated template; an existing file is never overwritten; a dangling symlink
in the user directory is left alone; the scan is sorted; and the first copy
that fails is returned as `TemplateError::Io`, leaving the rest for the next
call. Where it differs:

- It returns the installed paths; Python returns `None`.
- With either directory unset, or a `default_dir` that is not a directory, it
  returns an empty list and logs nothing. Python logs the first at DEBUG and
  the second at WARNING, and also logs a skipped dangling symlink at WARNING.
- Paths are used as given: no `~` expansion, where Python calls
  `expanduser()`.
