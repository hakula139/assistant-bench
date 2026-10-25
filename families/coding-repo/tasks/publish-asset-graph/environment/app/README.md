# Asset publisher

`python -m siteassets SOURCE OUTPUT` builds every .html entry under SOURCE and prints JSON with directory and manifest. A failure exits nonzero with a diagnostic on stderr.

## Public interface

The package exports `build(source_root: Path, output_root: Path) -> BuildResult`, `BuildResult(directory: Path, manifest: dict[str, str])` and `BuildError`. directory is the immutable published release. manifest maps source-relative asset paths to release-relative emitted paths, using forward slashes.

All .html files are entry pages, preserving their relative paths. Only non-HTML assets reachable from entries are emitted. The supplied scanner recognizes HTML src attributes, href attributes of link elements whose rel contains stylesheet, and CSS url(...) values. HTML entities in attribute values are decoded. CSS comments and ordinary quoted strings are ignored. CSS URL values may be unquoted or quoted, with spaces and Unicode supported in quoted values. CSS backslash escapes and HTML srcset are outside the supplied grammar.

Relative and source-root-relative local references resolve against the source tree. URL path escapes are decoded for lookup. Query and fragment suffixes are preserved. Schemed URLs, protocol-relative URLs, data URLs and fragment-only references remain unchanged. Missing files, cycles and local references escaping the source tree raise BuildError before publication. Symlinks are resolved for confinement, while each logical alias retains its entry or manifest path and supplies the base for its relative references. The output directory must be distinct from the source directory. When output is nested under source, its subtree is excluded from entry discovery.

Emit an asset as `assets/<stem>.<first 12 hex SHA-256 characters><suffix>`, hashing its final bytes after dependency rewriting. References are relative to each emitted consumer and URL-escape emitted paths. Identical emitted content may share an asset. The dependency graph is acyclic.

Complete releases live under OUTPUT/releases. Atomically replace the OUTPUT/current symlink only after the complete release is built. Failed validation or building must preserve the previous link and its bytes. Repeating unchanged input produces equivalent bytes and manifest. No release cleanup policy is required.

The baseline exposes working scanning and graph discovery, but hashes raw assets and publishes the new directory before validating the full graph. Refactor these boundaries while keeping the supplied scanner's behavior.
