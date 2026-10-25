import hashlib
import tempfile
import unittest
from pathlib import Path

from siteassets import build


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


if __name__ == '__main__':
    unittest.main()
