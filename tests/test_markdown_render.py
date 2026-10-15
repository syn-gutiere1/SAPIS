"""
Tests for the markdown renderer used by the suggested-fixes tab.

The file is markdown, and the dashboard used to show it as one `<pre>` block, so headings
and code read as flat text. The renderer has to make it readable without ever letting the
model's text become markup: it escapes first and builds tags afterwards.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_dashboard  # noqa: E402

render = build_dashboard.render_markdown


# ---------------------------------------------------------------------------
# Block structure
# ---------------------------------------------------------------------------


def test_headings_become_heading_tags():
    html = render("## Fix Risks\n\n### 1. Something\n\ntext")

    # `##` maps to h3: the tab already carries the h2 title.
    assert "<h3>Fix Risks</h3>" in html
    assert "<h4>1. Something</h4>" in html
    assert "## Fix Risks" not in html


def test_paragraphs_are_separated():
    html = render("first line\nstill first\n\nsecond paragraph")

    assert "<p>first line still first</p>" in html
    assert "<p>second paragraph</p>" in html


def test_bullets_become_an_unordered_list():
    html = render("- one\n- two\n")

    assert "<ul><li>one</li><li>two</li></ul>" in html


def test_numbers_become_an_ordered_list():
    html = render("1. first\n2. second\n")

    assert "<ol><li>first</li><li>second</li></ol>" in html


def test_indented_bullets_nest():
    """
    The model indents a level at a time rather than by a fixed width: a real answer used 0,
    3, 5 and 7 spaces. Dividing by two put level 1 at 1.5 and collapsed the structure.
    """
    html = render(
        "1. parent\n"
        "   - child\n"
        "     - grandchild\n"
        "       - great-grandchild\n"
    )

    assert (
        "<ol><li>parent<ul><li>child<ul><li>grandchild"
        "<ul><li>great-grandchild</li></ul>"
        "</li></ul></li></ul></li></ol>"
    ) in html


def test_blockquote():
    html = render("> Written by a model.\n> Check it.")

    assert "<blockquote>Written by a model. Check it.</blockquote>" in html


def test_horizontal_rule():
    assert "<hr>" in render("before\n\n---\n\nafter")


def test_fenced_code_is_verbatim():
    html = render("```groovy\ndef x = 1 < 2\n```")

    assert '<pre class="md-code"><code>' in html
    # Escaped, and the marker characters are gone.
    assert "def x = 1 &lt; 2" in html
    assert "```" not in html


def test_code_content_is_not_treated_as_markdown():
    """A `**` inside a snippet must stay literal, not become bold."""
    html = render("```\na ** b ` c\n```")

    assert "<strong>" not in html
    assert "a ** b ` c" in html


# ---------------------------------------------------------------------------
# Inline
# ---------------------------------------------------------------------------


def test_inline_code_and_bold():
    html = render("Use **`CallActivity_77`** here")

    assert "<strong><code>CallActivity_77</code></strong>" in html


def test_inline_code_before_bold_keeps_the_snippet_intact():
    html = render("`a ** b` and **bold**")

    assert "<code>a ** b</code>" in html
    assert "<strong>bold</strong>" in html


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


def test_model_text_cannot_become_markup():
    """
    The markdown comes from a model that read an untrusted artifact. Escaping happens
    before any tag is built.
    """
    html = render("<script>alert('x')</script>\n\n<img src=x onerror=y>")

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img" not in html


def test_an_attribute_in_a_heading_is_escaped():
    html = render('## <img src=x onerror="y">')

    assert "<img" not in html
    assert "&lt;img" in html


def test_unsupported_markdown_degrades_to_text():
    """A table is not supported; it must come out as text, not as broken markup."""
    html = render("| a | b |\n|---|---|\n| 1 | 2 |")

    assert "<table" not in html
    assert "| a | b |" in html


def test_empty_input_renders_nothing():
    assert render("") == ""
    assert render(None) == ""


def test_windows_line_endings_are_handled():
    html = render("- one\r\n- two\r\n")

    assert "<ul><li>one</li><li>two</li></ul>" in html
