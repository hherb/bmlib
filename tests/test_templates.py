"""Tests for bmlib.templates."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from jinja2 import TemplateNotFound

from bmlib.templates import TemplateEngine


def test_render_from_default_dir(tmp_path):
    default_dir = tmp_path / "defaults"
    default_dir.mkdir()
    (default_dir / "test.txt").write_text("Hello {{ name }}!")

    engine = TemplateEngine(default_dir=default_dir)
    assert engine.render("test.txt", name="World") == "Hello World!"


def test_user_dir_overrides_default(tmp_path):
    default_dir = tmp_path / "defaults"
    default_dir.mkdir()
    (default_dir / "test.txt").write_text("default: {{ x }}")

    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "test.txt").write_text("custom: {{ x }}")

    engine = TemplateEngine(user_dir=user_dir, default_dir=default_dir)
    assert engine.render("test.txt", x="val") == "custom: val"


def test_fallback_to_default(tmp_path):
    default_dir = tmp_path / "defaults"
    default_dir.mkdir()
    (default_dir / "only_default.txt").write_text("from default")

    user_dir = tmp_path / "user"
    user_dir.mkdir()

    engine = TemplateEngine(user_dir=user_dir, default_dir=default_dir)
    assert engine.render("only_default.txt") == "from default"


def test_missing_template_raises(tmp_path):
    engine = TemplateEngine(default_dir=tmp_path)
    with pytest.raises(TemplateNotFound):
        engine.render("nonexistent.txt")


def test_has_template(tmp_path):
    default_dir = tmp_path / "defaults"
    default_dir.mkdir()
    (default_dir / "exists.txt").write_text("yes")

    engine = TemplateEngine(default_dir=default_dir)
    assert engine.has_template("exists.txt")
    assert not engine.has_template("nope.txt")


def test_jinja_conditionals(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "cond.txt").write_text(
        "{% if include_methods %}Methods: {{ methods }}{% endif %}"
    )

    engine = TemplateEngine(default_dir=d)
    assert engine.render("cond.txt", include_methods=True, methods="RCT") == "Methods: RCT"
    assert engine.render("cond.txt", include_methods=False, methods="RCT") == ""


def test_jinja_loops(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "loop.txt").write_text(
        "{% for item in items %}- {{ item }}\n{% endfor %}"
    )

    engine = TemplateEngine(default_dir=d)
    result = engine.render("loop.txt", items=["a", "b", "c"])
    assert "- a" in result
    assert "- c" in result


def test_install_defaults(tmp_path):
    default_dir = tmp_path / "defaults"
    default_dir.mkdir()
    (default_dir / "a.txt").write_text("alpha")
    (default_dir / "b.txt").write_text("beta")

    user_dir = tmp_path / "user"
    # User dir doesn't exist yet — install_defaults should create it
    engine = TemplateEngine(user_dir=user_dir, default_dir=default_dir)
    engine.install_defaults()

    assert (user_dir / "a.txt").read_text() == "alpha"
    assert (user_dir / "b.txt").read_text() == "beta"

    # Existing files are not overwritten
    (user_dir / "a.txt").write_text("modified")
    engine.install_defaults()
    assert (user_dir / "a.txt").read_text() == "modified"
