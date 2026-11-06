# Asset publisher

`python -m siteassets SOURCE OUTPUT` builds every .html entry under SOURCE and prints JSON with directory and manifest. A failure exits nonzero with a diagnostic on stderr.

## Public interface

The package exports `build(source_root: Path, output_root: Path) -> BuildResult`, `BuildResult(directory: Path, manifest: dict[str, str])` and `BuildError`. directory is the build's own release directory under OUTPUT/releases, which later builds leave unchanged. manifest maps the source-relative path of each emitted non-HTML asset to its release-relative emitted path, using forward slashes, and lists no entry pages.

All .html files are entry pages, preserving their relative paths. Only non-HTML assets reachable from entries are emitted. The supplied scanner recognizes HTML src attributes, href attributes of link elements whose rel contains stylesheet, and CSS url(...) values. HTML entities in attribute values are decoded. CSS comments and ordinary quoted strings are ignored. CSS URL values may be unquoted or quoted, with spaces and Unicode supported in quoted values. CSS backslash escapes and HTML srcset are outside the supplied grammar.

Relative and source-root-relative local references resolve against the source tree. URL path escapes are decoded for lookup. Query and fragment suffixes are preserved. Schemed URLs, protocol-relative URLs, data URLs and fragment-only references remain unchanged. Missing files, cycles and local references escaping the source tree raise BuildError before publication.

Symlinks must resolve inside the source tree, while each file keeps the logical path it was reached by: an entry is emitted at that path, an asset appears in the manifest under it, and the relative references in its content resolve from its directory. Building into the source directory itself raises BuildError. When output is nested under source, its subtree is excluded from entry discovery.

Emit an asset as `assets/<stem>.<first 12 hex SHA-256 characters><suffix>`, hashing its final bytes after dependency rewriting. Each rewritten reference is a URL relative to the emitted consumer, with the emitted path URL-escaped. Assets whose emitted paths coincide may share one file.

Complete releases live under OUTPUT/releases. Atomically replace the OUTPUT/current symlink only after the complete release is built. Failed validation or building must preserve the previous link and its bytes. Rebuilding unchanged input produces a release with the same files, bytes and manifest. No release cleanup policy is required.
