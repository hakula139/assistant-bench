"""Consumer-visible asset graph and immutable publication contracts."""

import hashlib
import re
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from textwrap import dedent
from urllib.parse import unquote, urlsplit

from siteassets import BuildError, build


ROOT = Path(__file__).resolve().parents[1]


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


def html_urls(path: Path) -> list[str]:
    parser = HtmlReferences()
    parser.feed(path.read_text())
    return parser.urls


def css_urls(path: Path) -> list[str]:
    return [
        match.group(1)
        for match in re.finditer(r"url\(['\"]?([^'\")]+)", path.read_text())
    ]


def target(consumer: Path, url: str) -> Path:
    return (consumer.parent / unquote(urlsplit(url).path)).resolve()


def files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob('*')
        if path.is_file()
    }


class TestAssetPublication(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, source: Path, relative: str, content: str | bytes) -> Path:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        return path

    def assert_hashed(self, release: Path, source: str, emitted: str) -> None:
        original = Path(source)
        digest = hashlib.sha256((release / emitted).read_bytes()).hexdigest()[:12]
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
            'pages/detail.html',
            dedent(
                """
                <link rel="preload stylesheet"
                      href="../styles/base.css?theme=dark#screen">
                """
            ),
        )
        self.write(
            source,
            'styles/base.css',
            dedent(
                """
                @import url('nested/extra.css');
                .icon { background: url('/media/logo.svg'); }
                """
            ),
        )
        self.write(
            source,
            'styles/nested/extra.css',
            dedent(
                """
                .extra {
                    background: url("../../media/caf%C3%A9%20logo.svg?size=2#icon");
                }
                """
            ),
        )
        self.write(source, 'media/logo.svg', '<svg>plain</svg>')
        self.write(source, 'media/café logo.svg', '<svg>accent</svg>')
        self.write(source, 'unused.svg', '<svg>unused</svg>')

        result = build(source, output)

        self.assertEqual(
            set(result.manifest),
            {
                'styles/base.css',
                'styles/nested/extra.css',
                'media/logo.svg',
                'media/café logo.svg',
            },
        )
        self.assertTrue((output / 'current').is_symlink())
        self.assertEqual((output / 'current').resolve(), result.directory.resolve())
        base_url = html_urls(result.directory / 'pages/detail.html')[0]
        self.assertEqual(urlsplit(base_url).query, 'theme=dark')
        self.assertEqual(urlsplit(base_url).fragment, 'screen')
        base = result.directory / result.manifest['styles/base.css']
        self.assertEqual(
            target(result.directory / 'pages/detail.html', base_url), base.resolve()
        )
        urls = css_urls(base)
        self.assertEqual(
            [target(base, url) for url in urls],
            [
                (
                    result.directory / result.manifest['styles/nested/extra.css']
                ).resolve(),
                (result.directory / result.manifest['media/logo.svg']).resolve(),
            ],
        )
        extra = result.directory / result.manifest['styles/nested/extra.css']
        extra_url = css_urls(extra)[0]
        self.assertEqual(urlsplit(extra_url).query, 'size=2')
        self.assertEqual(urlsplit(extra_url).fragment, 'icon')
        self.assertEqual(
            target(extra, extra_url),
            (result.directory / result.manifest['media/café logo.svg']).resolve(),
        )
        for original, emitted in result.manifest.items():
            with self.subTest(asset=original):
                self.assert_hashed(result.directory, original, emitted)

    def test_child_changes_propagate_hashes_without_mutating_old_release(self) -> None:
        source, output = self.root / 'source', self.root / 'output'
        self.write(source, 'index.html', '<link rel="stylesheet" href="style.css">')
        self.write(source, 'style.css', '.x { background: url("image.svg"); }')
        self.write(source, 'image.svg', '<svg>first</svg>')
        first = build(source, output)
        original = files(first.directory)
        self.write(source, 'image.svg', '<svg>second</svg>')

        second = build(source, output)
        repeated = build(source, output)

        self.assertNotEqual(first.manifest['image.svg'], second.manifest['image.svg'])
        self.assertNotEqual(first.manifest['style.css'], second.manifest['style.css'])
        self.assertEqual(files(first.directory), original)
        self.assertEqual(second.manifest, repeated.manifest)
        self.assertEqual(files(second.directory), files(repeated.directory))
        for original_path, emitted in second.manifest.items():
            with self.subTest(asset=original_path):
                self.assert_hashed(second.directory, original_path, emitted)

    def test_in_tree_entry_and_asset_aliases_keep_their_relative_dependencies(
        self,
    ) -> None:
        source, output = self.root / 'source', self.root / 'output'
        self.write(
            source, 'original/page.html', '<link rel="stylesheet" href="theme.css">'
        )
        self.write(source, 'original/theme.css', '.x { background: url("image.svg"); }')
        self.write(source, 'original/image.svg', '<svg>original</svg>')
        self.write(source, 'alias/image.svg', '<svg>alias</svg>')
        (source / 'alias/page.html').symlink_to('../original/page.html')
        (source / 'alias/theme.css').symlink_to('../original/theme.css')

        result = build(source, output)

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
                self.assertTrue(page.is_file())
                css = target(page, html_urls(page)[0])
                self.assertEqual(
                    css, (result.directory / result.manifest[f'{base}/theme.css'])
                )
                image = target(css, css_urls(css)[0])
                self.assertEqual(
                    image, (result.directory / result.manifest[f'{base}/image.svg'])
                )
                self.assertEqual(image.read_text(), f'<svg>{base}</svg>')
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
        self.write(
            source,
            'index.html',
            dedent(
                """
                <script src="https://example.com/app.js?x=1&amp;y=2#top"></script>
                <img src="//example.com/image.svg">
                <img src="data:image/svg+xml;base64,PHN2Zy8+">
                <img src="#logo">
                <link rel="stylesheet" href="style.css">
                """
            ),
        )
        css = dedent(
            """
            /* url(missing-comment.svg) */
            .x::after { content: "url(missing-string.svg)"; }
            .x { --custom: noturl("missing-function.svg"); }
            .x { background: url("https://example.com/background.svg?q=1#x"); }
            """
        )
        self.write(source, 'style.css', css)

        result = build(source, output)

        self.assertEqual(html_urls(result.directory / 'index.html')[:4], untouched)
        self.assertEqual(
            (result.directory / result.manifest['style.css']).read_text(),
            css,
        )
        self.assertEqual(set(result.manifest), {'style.css'})

    def test_duplicate_stems_remain_distinct_and_nested_output_is_not_an_entry(
        self,
    ) -> None:
        source = self.root / 'source'
        output = source / 'build'
        self.write(source, 'index.html', '<img src="a/logo.svg"><img src="b/logo.svg">')
        self.write(source, 'a/logo.svg', '<svg>A</svg>')
        self.write(source, 'b/logo.svg', '<svg>B</svg>')

        first = build(source, output)
        second = build(source, output)

        self.assertEqual(set(first.manifest), {'a/logo.svg', 'b/logo.svg'})
        self.assertNotEqual(first.manifest['a/logo.svg'], first.manifest['b/logo.svg'])
        self.assertEqual(first.manifest, second.manifest)
        self.assertEqual(
            [
                path.read_bytes()
                for path in (
                    target(second.directory / 'index.html', url)
                    for url in html_urls(second.directory / 'index.html')
                )
            ],
            [b'<svg>A</svg>', b'<svg>B</svg>'],
        )
        self.assertEqual(
            [
                path.relative_to(second.directory).as_posix()
                for path in second.directory.rglob('*.html')
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
                    source, 'index.html', '<link rel="stylesheet" href="style.css">'
                )
                self.write(source, 'style.css', '.x { background: url("image.svg"); }')
                self.write(source, 'image.svg', '<svg>good</svg>')
                previous = build(source, output)
                previous_link = (output / 'current').readlink()
                previous_files = files(previous.directory)
                if failure == 'missing':
                    self.write(source, 'style.css', '@import url("nested.css");')
                    self.write(
                        source, 'nested.css', '.x { background: url("missing.svg"); }'
                    )
                elif failure == 'cycle':
                    self.write(source, 'style.css', '@import url("nested.css");')
                    self.write(source, 'nested.css', '@import url("style.css");')
                elif failure == 'traversal':
                    self.write(
                        source, 'style.css', '.x { background: url("../outside.svg"); }'
                    )
                    self.write(source.parent, 'outside.svg', '<svg>outside</svg>')
                else:
                    self.write(
                        source, 'style.css', '.x { background: url("escape.svg"); }'
                    )
                    outside = self.write(
                        source.parent, 'outside.svg', '<svg>outside</svg>'
                    )
                    (source / 'escape.svg').symlink_to(outside)

                with self.assertRaises(BuildError):
                    build(source, output)

                self.assertEqual((output / 'current').readlink(), previous_link)
                self.assertEqual(files((output / 'current').resolve()), previous_files)

    def test_output_write_failure_in_another_process_preserves_publication(
        self,
    ) -> None:
        source, output = self.root / 'source', self.root / 'output'
        self.write(source, 'index.html', '<img src="image.bin">')
        self.write(source, 'image.bin', b'previous image')
        previous = build(source, output)
        previous_link = (output / 'current').readlink()
        previous_files = files(previous.directory)
        self.write(source, 'image.bin', b'x' * 65536)

        failed = subprocess.run(
            [
                sys.executable,
                '-m',
                'tests.limited_build',
                str(source),
                str(output),
                '4096',
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertEqual(failed.stdout.strip(), 'Build failed.')
        self.assertEqual((output / 'current').readlink(), previous_link)
        self.assertEqual(files((output / 'current').resolve()), previous_files)
        self.assertEqual(files(previous.directory), previous_files)

    def test_cli_publishes_a_complete_readable_release(self) -> None:
        source, output = self.root / 'source', self.root / 'output'
        self.write(source, 'index.html', '<img src="image.svg">')
        self.write(source, 'image.svg', '<svg>cli</svg>')

        completed = subprocess.run(
            [sys.executable, '-m', 'siteassets', str(source), str(output)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        page = output / 'current' / 'index.html'
        self.assertEqual(
            target(page, html_urls(page)[0]).read_bytes(), b'<svg>cli</svg>'
        )


if __name__ == '__main__':
    unittest.main()
