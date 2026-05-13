from website.features.summarization_engine.source_ingest.newsletter.site_extractors import (
    StructuredNewsletter,
    extract_structured,
)


_SUBSTACK_HTML = """
<html><head><meta property="og:site_name" content="Example Substack"></head><body>
<article>
<h1 class="post-title">The Real Question About AI Moats</h1>
<h3 class="subtitle">Why scale doesn't translate to durability</h3>
<div class="body markup">
<p>Opening paragraph here.</p>
<p>Second paragraph.</p>
</div>
<div class="post-footer">
<a href="https://example.substack.com/subscribe">Subscribe now</a>
</div>
</article>
</body></html>
"""


def test_substack_extracts_title_subtitle_body():
    result = extract_structured(
        _SUBSTACK_HTML,
        url="https://example.substack.com/p/ai-moats",
    )
    assert isinstance(result, StructuredNewsletter)
    assert result.site == "substack"
    assert result.title == "The Real Question About AI Moats"
    assert "scale doesn't translate" in result.subtitle
    assert "Opening paragraph" in result.body_text
    assert any("subscribe" in cta.lower() for cta in result.cta_links)


def test_non_substack_returns_empty_structured():
    result = extract_structured(
        "<html><body><p>hi</p></body></html>",
        url="https://random.example.com/",
    )
    assert result.site == "unknown"
    assert result.title == ""


def test_custom_domain_substack_uses_dom_markers():
    result = extract_structured(
        _SUBSTACK_HTML,
        url="https://platformer.news/p/ai-moats",
    )
    assert result.site == "substack"
    assert result.title == "The Real Question About AI Moats"


def test_ghost_newsletter_extracts_publication_identity():
    html = """
    <html>
      <head>
        <meta property="og:site_name" content="Platformer">
        <meta name="generator" content="Ghost 6.33">
        <meta name="description" content="Why this matters now">
      </head>
      <body>
        <article class="gh-article post">
          <h1 class="gh-article-title is-title">Substack promotes a Nazi</h1>
          <div class="gh-content">
            <p>Opening paragraph.</p>
          </div>
        </article>
      </body>
    </html>
    """
    result = extract_structured(html, url="https://www.platformer.news/substack-nazi-push-notification/")
    assert result.site == "ghost"
    assert result.publication_identity == "Platformer"
    assert result.title == "Substack promotes a Nazi"


def test_buttondown_extracts_title_body_from_host():
    html = """
    <html>
      <head>
        <meta property="og:site_name" content="Some Buttondown Pub">
        <meta name="description" content="Weekly notes">
      </head>
      <body>
        <h1 class="email-title">Issue 42: On Curiosity</h1>
        <article class="email-content">
          <p>Body paragraph one.</p>
          <p>Body paragraph two.</p>
        </article>
        <a href="https://buttondown.email/example/subscribe">Subscribe</a>
      </body>
    </html>
    """
    result = extract_structured(
        html,
        url="https://buttondown.email/example/archive/issue-42/",
    )
    assert result.site == "buttondown"
    assert result.title == "Issue 42: On Curiosity"
    assert "Body paragraph one" in result.body_text
    assert result.publication_identity == "Some Buttondown Pub"
    assert any("subscribe" in cta.lower() for cta in result.cta_links)


def test_buttondown_custom_domain_uses_dom_markers():
    html = """
    <html><head><meta property="og:site_name" content="Pub"></head>
    <body>
      <h1>Hello</h1>
      <div class="email-body"><p>Content here.</p></div>
    </body></html>
    """
    result = extract_structured(html, url="https://news.example.com/p/hello")
    assert result.site == "buttondown"
    assert "Content here" in result.body_text


def test_non_buttondown_html_returns_unknown():
    result = extract_structured(
        "<html><body><div>nothing special</div></body></html>",
        url="https://example.org/post",
    )
    assert result.site == "unknown"


def test_beehiiv_custom_domain_extracts_rendered_post():
    html = """
    <html>
      <head>
        <meta property="og:site_name" content="Synthesis Spotlight">
        <meta property="og:title" content="Organic Synthesis @ Beehiiv">
        <meta name="description" content="Radical Sorting and More">
      </head>
      <body>
        <h1>Organic Synthesis @ Beehiiv</h1>
        <div class="rendered-post">
          <p>Paragraph one.</p>
        </div>
      </body>
    </html>
    """
    result = extract_structured(
        html,
        url="https://www.synthesisspotlight.com/p/organic-synthesis-beehiiv",
    )
    assert result.site == "beehiiv"
    assert result.publication_identity == "Synthesis Spotlight"
    assert "Paragraph one" in result.body_text
