"""Jinja2 template engine for prompt files.

Loads templates from user-configurable directories with fallback to
package-shipped defaults.  Supports full Jinja2 syntax (conditionals,
loops, filters).

Usage::

    from bmlib.templates import TemplateEngine

    engine = TemplateEngine(
        user_dir=Path("~/.myapp/prompts"),
        default_dir=Path(__file__).parent / "defaults",
    )
    rendered = engine.render("scoring.txt", title="...", abstract="...")
"""

from bmlib.templates.engine import TemplateEngine

__all__ = ["TemplateEngine"]
