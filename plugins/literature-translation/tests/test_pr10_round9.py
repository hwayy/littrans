import base64
import html
import re
from pathlib import Path

import pytest
from test_workflow_v6 import project as workflow_project

from littrans.rendering import _inline_html
from littrans.source_render import render_source_review

project = workflow_project


@pytest.mark.parametrize("literal", [r"\[^1]", r"\(x[^1]\)", r"\[x[^1]\]", "`[^1]`", "$x[^1]$"])
def test_html_preserves_literal_footnotes(literal):
    result = _inline_html(literal + " Real[^1]", "source", {"1": "note"})
    assert result.count('class="footnote-ref"') == 1
    assert 'href="#note"' in result


def test_standalone_embeds_original_png_fallback(project):
    output = render_source_review(project, "1", name="standalone", standalone=True)
    document = Path(output["html"]).read_text(encoding="utf-8")
    tags = re.findall(r"<img\b[^>]+>", document)
    assert tags
    for tag in tags:
        assert 'src="data:image/' in tag
        assert "original-assets/" not in html.unescape(tag)
        fallback = re.search(r'data-original-fallback="([^"]+)"', tag)
        assert fallback
        assert fallback[1].startswith("data:image/png;base64,")
        assert base64.b64decode(fallback[1].split(",", 1)[1]).startswith(b"\x89PNG\r\n\x1a\n")
