"""Consumer-visible asset graph and immutable publication contracts."""

import dataclasses
import hashlib
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from textwrap import dedent
from urllib.parse import unquote, urlsplit

import sandbox


class HtmlReferences(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        source = values.get('src')
        if source is not None:
            self.urls.append(source)
        href = values.get('href')
        if tag == 'link' and href is not None:
            self.urls.append(href)


@dataclasses.dataclass(frozen=True)
class Release:
    """The directory and manifest a successful build reports."""

    directory: Path
    manifest: dict[str, str]


@dataclasses.dataclass(frozen=True)
class Entry:
    """What a path in the sandbox is, where it links, and where it resolves."""

    is_file: bool
    link: str | None
    resolved: Path


class TestAssetPublication(unittest.TestCase):
    def setUp(self) -> None:
        self.root = sandbox.scratch()

    def write(
        self, source: Path, files: dict[str, str], links: dict[str, str] | None = None
    ) -> None:
        sandbox.observe(
            'assets', 'write', root=str(source), files=files, links=links or {}
        )

    def build(self, source: Path, output: Path) -> Release:
        seen = sandbox.observe(
            'assets', 'build', source=str(source), output=str(output)
        )
        if seen['error'] is not None:
            self.fail(f'the build fails: {seen["error"]}')
        return Release(Path(seen['directory']), seen['manifest'])

    def entry(self, path: Path) -> Entry:
        seen = sandbox.observe('assets', 'entry', path=str(path))
        return Entry(seen['is_file'], seen['link'], Path(seen['resolved']))

    def resolve(self, path: Path) -> Path:
        return self.entry(path).resolved

    def read(self, path: Path) -> bytes:
        return bytes.fromhex(sandbox.observe('assets', 'read', path=str(path)))

    def files(self, root: Path) -> dict[str, bytes]:
        seen = sandbox.observe('assets', 'files', root=str(root))
        return {name: bytes.fromhex(content) for name, content in seen.items()}

    def html_urls(self, path: Path) -> list[str]:
        parser = HtmlReferences()
        parser.feed(self.read(path).decode())
        return parser.urls

    def css_urls(self, path: Path) -> list[str]:
        return [
            match.group(1)
            for match in re.finditer(r"url\(['\"]?([^'\")]+)", self.read(path).decode())
        ]

    def target(self, consumer: Path, url: str) -> Path:
        return self.resolve(consumer.parent / unquote(urlsplit(url).path))

    def assert_hashed(self, release: Path, source: str, emitted: str) -> None:
        original = Path(source)
        digest = hashlib.sha256(self.read(release / emitted)).hexdigest()[:12]
        self.assertEqual(
            emitted,
            f'assets/{original.stem}.{digest}{original.suffix}',
        )

    def test_nested_dependencies_hash_final_bytes_and_rewrite_each_consumer(
        self,
    ) -> None:
        source, output = self.root / 'source', self.root / 'output'
        self.write(
            source,
            {
                'pages/detail.html': dedent(
                    """
                    <link rel="preload stylesheet"
                          href="../styles/base.css?theme=dark#screen">
                    """
                ),
                'styles/base.css': dedent(
                    """
                    @import url('nested/extra.css');
                    .icon { background: url('/media/logo.svg'); }
                    """
                ),
                'styles/nested/extra.css': dedent(
                    """
                    .extra {
                        background: url("../../media/caf%C3%A9%20logo.svg?size=2#icon");
                    }
                    """
                ),
                'media/logo.svg': '<svg>plain</svg>',
                'media/café logo.svg': '<svg>accent</svg>',
                'unused.svg': '<svg>unused</svg>',
            },
        )

        result = self.build(source, output)

        self.assertEqual(
            set(result.manifest),
            {
                'styles/base.css',
                'styles/nested/extra.css',
                'media/logo.svg',
                'media/café logo.svg',
            },
        )
        self.assertIsNotNone(self.entry(output / 'current').link)
        self.assertEqual(
            self.resolve(output / 'current'), self.resolve(result.directory)
        )
        base_url = self.html_urls(result.directory / 'pages/detail.html')[0]
        self.assertEqual(urlsplit(base_url).query, 'theme=dark')
        self.assertEqual(urlsplit(base_url).fragment, 'screen')
        base = result.directory / result.manifest['styles/base.css']
        self.assertEqual(
            self.target(result.directory / 'pages/detail.html', base_url),
            self.resolve(base),
        )
        urls = self.css_urls(base)
        self.assertEqual(
            [self.target(base, url) for url in urls],
            [
                self.resolve(
                    result.directory / result.manifest['styles/nested/extra.css']
                ),
                self.resolve(result.directory / result.manifest['media/logo.svg']),
            ],
        )
        extra = result.directory / result.manifest['styles/nested/extra.css']
        extra_url = self.css_urls(extra)[0]
        self.assertEqual(urlsplit(extra_url).query, 'size=2')
        self.assertEqual(urlsplit(extra_url).fragment, 'icon')
        self.assertEqual(
            self.target(extra, extra_url),
            self.resolve(result.directory / result.manifest['media/café logo.svg']),
        )
        for original, emitted in result.manifest.items():
            with self.subTest(asset=original):
                self.assert_hashed(result.directory, original, emitted)

    def test_child_changes_propagate_hashes_without_mutating_old_release(self) -> None:
        source, output = self.root / 'source', self.root / 'output'
        self.write(
            source,
            {
                'index.html': '<link rel="stylesheet" href="style.css">',
                'style.css': '.x { background: url("image.svg"); }',
                'image.svg': '<svg>first</svg>',
            },
        )
        first = self.build(source, output)
        original = self.files(first.directory)
        self.write(source, {'image.svg': '<svg>second</svg>'})

        second = self.build(source, output)
        repeated = self.build(source, output)

        self.assertNotEqual(first.manifest['image.svg'], second.manifest['image.svg'])
        self.assertNotEqual(first.manifest['style.css'], second.manifest['style.css'])
        self.assertEqual(self.files(first.directory), original)
        self.assertEqual(second.manifest, repeated.manifest)
        self.assertEqual(self.files(second.directory), self.files(repeated.directory))
        for original_path, emitted in second.manifest.items():
            with self.subTest(asset=original_path):
                self.assert_hashed(second.directory, original_path, emitted)

    def test_in_tree_entry_and_asset_aliases_keep_their_relative_dependencies(
        self,
    ) -> None:
        source, output = self.root / 'source', self.root / 'output'
        self.write(
            source,
            {
                'original/page.html': '<link rel="stylesheet" href="theme.css">',
                'original/theme.css': '.x { background: url("image.svg"); }',
                'original/image.svg': '<svg>original</svg>',
                'alias/image.svg': '<svg>alias</svg>',
            },
            {
                'alias/page.html': '../original/page.html',
                'alias/theme.css': '../original/theme.css',
            },
        )

        result = self.build(source, output)

        self.assertEqual(
            set(result.manifest),
            {
                'original/theme.css',
                'original/image.svg',
                'alias/theme.css',
                'alias/image.svg',
            },
        )
        for base in ('original', 'alias'):
            with self.subTest(base=base):
                page = result.directory / base / 'page.html'
                self.assertTrue(self.entry(page).is_file)
                css = self.target(page, self.html_urls(page)[0])
                self.assertEqual(
                    css, (result.directory / result.manifest[f'{base}/theme.css'])
                )
                image = self.target(css, self.css_urls(css)[0])
                self.assertEqual(
                    image, (result.directory / result.manifest[f'{base}/image.svg'])
                )
                self.assertEqual(self.read(image).decode(), f'<svg>{base}</svg>')
                self.assert_hashed(
                    result.directory,
                    f'{base}/theme.css',
                    result.manifest[f'{base}/theme.css'],
                )

    def test_supported_external_and_non_reference_text_is_preserved(self) -> None:
        source, output = self.root / 'source', self.root / 'output'
        untouched = [
            'https://example.com/app.js?x=1&y=2#top',
            '//example.com/image.svg',
            'data:image/svg+xml;base64,PHN2Zy8+',
            '#logo',
        ]
        css = dedent(
            """
            /* url(missing-comment.svg) */
            .x::after { content: "url(missing-string.svg)"; }
            .x { --custom: noturl("missing-function.svg"); }
            .x { background: url("https://example.com/background.svg?q=1#x"); }
            """
        )
        self.write(
            source,
            {
                'index.html': dedent(
                    """
                    <script src="https://example.com/app.js?x=1&amp;y=2#top"></script>
                    <img src="//example.com/image.svg">
                    <img src="data:image/svg+xml;base64,PHN2Zy8+">
                    <img src="#logo">
                    <link rel="stylesheet" href="style.css">
                    """
                ),
                'style.css': css,
            },
        )

        result = self.build(source, output)

        self.assertEqual(self.html_urls(result.directory / 'index.html')[:4], untouched)
        self.assertEqual(
            self.read(result.directory / result.manifest['style.css']).decode(),
            css,
        )
        self.assertEqual(set(result.manifest), {'style.css'})

    def test_duplicate_stems_remain_distinct_and_nested_output_is_not_an_entry(
        self,
    ) -> None:
        source = self.root / 'source'
        output = source / 'build'
        self.write(
            source,
            {
                'index.html': '<img src="a/logo.svg"><img src="b/logo.svg">',
                'a/logo.svg': '<svg>A</svg>',
                'b/logo.svg': '<svg>B</svg>',
            },
        )

        first = self.build(source, output)
        second = self.build(source, output)

        self.assertEqual(set(first.manifest), {'a/logo.svg', 'b/logo.svg'})
        self.assertNotEqual(first.manifest['a/logo.svg'], first.manifest['b/logo.svg'])
        self.assertEqual(first.manifest, second.manifest)
        self.assertEqual(
            [
                self.read(path)
                for path in (
                    self.target(second.directory / 'index.html', url)
                    for url in self.html_urls(second.directory / 'index.html')
                )
            ],
            [b'<svg>A</svg>', b'<svg>B</svg>'],
        )
        self.assertEqual(
            [
                Path(path).relative_to(second.directory).as_posix()
                for path in sandbox.observe(
                    'assets', 'glob', root=str(second.directory), pattern='*.html'
                )
            ],
            ['index.html'],
        )

    def test_invalid_graph_preserves_current_link_and_complete_bytes(self) -> None:
        for failure in ('missing', 'cycle', 'traversal', 'symlink'):
            with self.subTest(failure=failure):
                source, output = (
                    self.root / failure / 'source',
                    self.root / failure / 'output',
                )
                self.write(
                    source,
                    {
                        'index.html': '<link rel="stylesheet" href="style.css">',
                        'style.css': '.x { background: url("image.svg"); }',
                        'image.svg': '<svg>good</svg>',
                    },
                )
                previous = self.build(source, output)
                previous_link = self.entry(output / 'current').link
                previous_files = self.files(previous.directory)
                if failure == 'missing':
                    self.write(
                        source,
                        {
                            'style.css': '@import url("nested.css");',
                            'nested.css': '.x { background: url("missing.svg"); }',
                        },
                    )
                elif failure == 'cycle':
                    self.write(
                        source,
                        {
                            'style.css': '@import url("nested.css");',
                            'nested.css': '@import url("style.css");',
                        },
                    )
                elif failure == 'traversal':
                    self.write(
                        source,
                        {'style.css': '.x { background: url("../outside.svg"); }'},
                    )
                    self.write(source.parent, {'outside.svg': '<svg>outside</svg>'})
                else:
                    self.write(
                        source, {'style.css': '.x { background: url("escape.svg"); }'}
                    )
                    outside = source.parent / 'outside.svg'
                    self.write(source.parent, {'outside.svg': '<svg>outside</svg>'})
                    self.write(source, {}, {'escape.svg': str(outside)})

                seen = sandbox.observe(
                    'assets', 'build', source=str(source), output=str(output)
                )

                self.assertIsNotNone(seen['error'], 'the build succeeds')
                self.assertEqual(self.entry(output / 'current').link, previous_link)
                self.assertEqual(
                    self.files(self.resolve(output / 'current')), previous_files
                )

    def test_output_write_failure_in_another_process_preserves_publication(
        self,
    ) -> None:
        source, output = self.root / 'source', self.root / 'output'
        self.write(
            source,
            {'index.html': '<img src="image.bin">', 'image.bin': 'previous image'},
        )
        previous = self.build(source, output)
        previous_link = self.entry(output / 'current').link
        previous_files = self.files(previous.directory)
        self.write(source, {'image.bin': 'x' * 65536})

        failed = sandbox.observe(
            'assets',
            'limited_build',
            source=str(source),
            output=str(output),
            limit=4096,
        )

        self.assertEqual(failed['returncode'], 1, failed['stdout'] + failed['stderr'])
        self.assertEqual(failed['stdout'].strip(), 'Build failed.')
        self.assertEqual(self.entry(output / 'current').link, previous_link)
        self.assertEqual(self.files(self.resolve(output / 'current')), previous_files)
        self.assertEqual(self.files(previous.directory), previous_files)

    def test_cli_publishes_a_complete_readable_release(self) -> None:
        source, output = self.root / 'source', self.root / 'output'
        self.write(
            source,
            {'index.html': '<img src="image.svg">', 'image.svg': '<svg>cli</svg>'},
        )

        completed = sandbox.observe(
            'assets', 'cli', source=str(source), output=str(output)
        )
        self.assertEqual(completed['returncode'], 0, completed['stderr'])

        page = output / 'current' / 'index.html'
        self.assertEqual(
            self.read(self.target(page, self.html_urls(page)[0])), b'<svg>cli</svg>'
        )


if __name__ == '__main__':
    unittest.main()
