from textwrap import dedent

import pytest

from assistant_bench.html import extract_html
from support import FIXTURES


DOCUMENT = dedent("""\
    <!doctype html>
    <html lang="en">
    <head><title>Example page</title></head>
    <body><h1>Welcome</h1></body>
    </html>
""")


class TestExtractHtml:
    @pytest.mark.parametrize(
        'case',
        ['community-event', 'service-status', 'plan-comparison', 'onboarding-guide'],
    )
    def test_accepts_the_four_synthetic_reference_documents(self, case: str) -> None:
        source = (FIXTURES / 'html' / f'{case}.html').read_text()

        assert extract_html(source) == source

    @pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
    @pytest.mark.parametrize('fenced', [False, True], ids=['bare', 'fenced'])
    def test_preserves_the_document_without_normalizing_its_source(
        self, newline: str, fenced: bool
    ) -> None:
        source = DOCUMENT.replace('Welcome', 'Welcome, 世界').replace('\n', newline)
        source = ''.join(f'  {line}' for line in source.splitlines(keepends=True))
        reply = f'  ```html{newline}{source}  ```{newline}' if fenced else source

        extracted = extract_html(reply)

        assert extracted == source

    @pytest.mark.parametrize(
        'reply',
        [
            f'```html\n{DOCUMENT}',
            f'Here is the page.\n```html\n{DOCUMENT}```\n',
            f'```html\n{DOCUMENT}```\nHope this helps.',
            f'```html\n{DOCUMENT}```\n```html\n{DOCUMENT}```\n',
            f'```xml\n{DOCUMENT}```\n',
        ],
        ids=[
            'unclosed-fence',
            'leading-prose',
            'trailing-prose',
            'two-fences',
            'wrong-language',
        ],
    )
    def test_refuses_replies_without_one_complete_html_document(
        self, reply: str
    ) -> None:
        assert extract_html(reply) is None

    @pytest.mark.parametrize(
        'boundary',
        [
            '<!doctype html>',
            '<html lang="en">',
            '<head>',
            '</head>',
            '<body>',
            '</body>',
            '</html>',
        ],
    )
    def test_requires_each_explicit_document_boundary(self, boundary: str) -> None:
        source = DOCUMENT.replace(boundary, '', 1)

        assert extract_html(source) is None

    @pytest.mark.parametrize(
        'source',
        [
            DOCUMENT.replace('<body>', '<body><body>', 1),
            DOCUMENT.replace('</head>\n<body>', '<body></head>', 1),
            DOCUMENT + '<!doctype html><html><head></head><body></body></html>',
            DOCUMENT.replace('<!doctype html>', '<!doctype svg>', 1),
        ],
        ids=[
            'repeated-boundary',
            'reordered-boundaries',
            'two-documents',
            'wrong-doctype',
        ],
    )
    def test_refuses_repeated_reordered_or_non_html_boundaries(
        self, source: str
    ) -> None:
        assert extract_html(source) is None

    @pytest.mark.parametrize(
        'fake',
        [
            '<!-- </body> -->',
            '<div title="</body>"></div>',
            '<script>const text = "</body>";</script>',
            '<script><!-- <script> </body> </script> --></script>',
            '<style>p::before { content: "</body>"; }</style>',
            '<textarea>"</body>"</textarea>',
            '<title>"</body>"</title>',
        ],
        ids=[
            'comment',
            'attribute',
            'script',
            'escaped-script',
            'style',
            'textarea',
            'title',
        ],
    )
    @pytest.mark.parametrize('closed', [True, False], ids=['closed', 'unclosed'])
    def test_ignores_fake_boundaries_in_html_text_states(
        self, fake: str, closed: bool
    ) -> None:
        source = DOCUMENT.replace('</body>', fake + ('</body>' if closed else ''))

        assert extract_html(source) == (source if closed else None)

    @pytest.mark.parametrize(
        'markup',
        [
            '<base href="https://example.com/">',
            '<link rel="dns&#45;prefetch" href="//example.com">',
            '<link rel="alternate PRECONNECT" href="https://example.com">',
            '<meta http-equiv="r&#101;fresh" content="0;url=https://example.com">',
            '<meta http-equiv="X-DNS-Prefetch-Control" content=" on ">',
            '<a href="https&#58;//example.com">Continue</a>',
            '<a href="">Continue</a>',
            '<a href=" \t ">Continue</a>',
            '<area href="/next">',
            '<a href="https://example.com" href="#local">Continue</a>',
            '<a href="#local" target="_blank">Continue</a>',
            '<svg><a xlink:href="https://example.com">Continue</a></svg>',
            '<noscript><link rel="dns-prefetch" href="//example.com"></noscript>',
            '<noscript><meta http-equiv="refresh" content="0;url=/next"></noscript>',
            '<noscript><a href="/next">Continue</a></noscript>',
        ],
        ids=[
            'base',
            'encoded-hint',
            'hint-list',
            'encoded-refresh',
            'dns-enable',
            'encoded-navigation',
            'empty-navigation',
            'whitespace-navigation',
            'area-navigation',
            'first-duplicate-wins',
            'new-target',
            'svg-navigation',
            'noscript-hint',
            'noscript-refresh',
            'noscript-navigation',
        ],
    )
    def test_refuses_navigation_and_network_hints_after_html_parsing(
        self, markup: str
    ) -> None:
        source = DOCUMENT.replace('<h1>Welcome</h1>', markup)

        assert extract_html(source) is None

    def test_accepts_native_controls_fragment_links_and_data_assets(self) -> None:
        source = DOCUMENT.replace(
            '<h1>Welcome</h1>',
            '<style>summary { color: purple; }</style>'
            '<details><summary>More</summary><p id="local">Details</p></details>'
            '<a href="#local" href="https://example.com" target="_self">Jump</a>'
            '<svg><a href="#local">Jump</a></svg>'
            '<img alt="Dot" src="data:image/gif;base64,'
            'R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==">',
        )

        assert extract_html(source) == source

    def test_preserves_literal_markdown_fences_inside_a_bare_html_body(self) -> None:
        source = DOCUMENT.replace('<h1>Welcome</h1>', '\n\n```text\nExample\n```\n\n')

        assert extract_html(source) == source

    @pytest.mark.parametrize(
        'element', ['animate', 'set', 'animateMotion', 'animateTransform']
    )
    @pytest.mark.parametrize('uppercase', [False, True], ids=['canonical', 'uppercase'])
    def test_refuses_svg_declarative_mutations_of_validated_fragment_links(
        self, element: str, uppercase: bool
    ) -> None:
        name = element.upper() if uppercase else element
        markup = (
            '<svg><a href="#local">'
            f'<{name} attributeName="href" to="https://example.com/escape" '
            'dur="1ms" fill="freeze"/>'
            '<text>Go</text></a></svg><p id="local">Local</p>'
        )
        source = DOCUMENT.replace('<h1>Welcome</h1>', markup)

        assert extract_html(source) is None

    def test_accepts_static_svg_css_animation_and_inert_html_names(self) -> None:
        source = DOCUMENT.replace(
            '<h1>Welcome</h1>',
            '<style>@keyframes pulse { to { opacity: .5; } } '
            'circle { animation: pulse 1s infinite; }</style>'
            '<svg><circle cx="20" cy="20" r="10"/></svg>'
            '<set>Inert HTML element</set>',
        )

        assert extract_html(source) == source
