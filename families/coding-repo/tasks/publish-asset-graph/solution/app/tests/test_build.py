import hashlib
import tempfile
import unittest
from pathlib import Path

from siteassets import BuildError, build


class TestLeafAsset(unittest.TestCase):
    def test_simple_page_publishes_its_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            image = b'<svg>example</svg>'
            (source / 'index.html').write_text('<img src="image.svg">')
            (source / 'image.svg').write_bytes(image)

            result = build(source, root / 'output')

            emitted = f'assets/image.{hashlib.sha256(image).hexdigest()[:12]}.svg'
            self.assertEqual(result.manifest, {'image.svg': emitted})
            self.assertEqual((result.directory / emitted).read_bytes(), image)
            self.assertIn(emitted, (result.directory / 'index.html').read_text())

    def test_dependency_hash_and_previous_publication_survive_failed_rebuild(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'source', root / 'output'
            source.mkdir()
            (source / 'index.html').write_text(
                '<link rel="stylesheet" href="style.css">'
            )
            (source / 'style.css').write_text('.x { background: url("image.svg"); }')
            (source / 'image.svg').write_bytes(b'<svg>image</svg>')

            published = build(source, output)

            css_path = published.manifest['style.css']
            css = (published.directory / css_path).read_bytes()
            self.assertEqual(
                css_path, f'assets/style.{hashlib.sha256(css).hexdigest()[:12]}.css'
            )
            self.assertIn(
                published.manifest['image.svg'].removeprefix('assets/'), css.decode()
            )
            previous_link = (output / 'current').readlink()
            previous_files = {
                path.relative_to(published.directory): path.read_bytes()
                for path in published.directory.rglob('*')
                if path.is_file()
            }
            (source / 'image.svg').unlink()

            with self.assertRaises(BuildError):
                build(source, output)

            self.assertEqual((output / 'current').readlink(), previous_link)
            self.assertEqual(
                {
                    path.relative_to(published.directory): path.read_bytes()
                    for path in (output / 'current').resolve().rglob('*')
                    if path.is_file()
                },
                previous_files,
            )


if __name__ == '__main__':
    unittest.main()
