from markupsafe import Markup

from sira.rendering.inline import Run, inline_markdown_to_html, inline_markdown_to_runs


def test_links_bold_italic_code_become_html():
    html = inline_markdown_to_html(
        "See [GitHub](https://github.com/j) and **bold**, *it*, `code`."
    )
    assert isinstance(html, Markup)
    assert html == (
        'See <a href="https://github.com/j">GitHub</a> and <b>bold</b>, '
        "<i>it</i>, <code>code</code>."
    )


def test_bare_urls_become_links():
    assert inline_markdown_to_html("at https://x.io/a?b=1 now") == (
        'at <a href="https://x.io/a?b=1">https://x.io/a?b=1</a> now'
    )


def test_unsafe_scheme_renders_as_plain_text():
    assert inline_markdown_to_html("[x](javascript:alert(1))") == "x"


def test_user_text_is_escaped():
    assert inline_markdown_to_html("<script>&") == "&lt;script&gt;&amp;"
    assert inline_markdown_to_html('[a"b](https://x.io/?q="1")') == (
        '<a href="https://x.io/?q=&quot;1&quot;">a&quot;b</a>'
    )


def test_runs_round_trip_the_same_cases():
    runs = inline_markdown_to_runs("A [l](https://x) **b** *i* `c` https://y end")
    assert runs == [
        Run("A "),
        Run("l", href="https://x"),
        Run(" "),
        Run("b", bold=True),
        Run(" "),
        Run("i", italic=True),
        Run(" "),
        Run("c", code=True),
        Run(" "),
        Run("https://y", href="https://y"),
        Run(" end"),
    ]


def test_plain_text_is_one_run():
    assert inline_markdown_to_runs("plain") == [Run("plain")]
    assert inline_markdown_to_runs("") == []
