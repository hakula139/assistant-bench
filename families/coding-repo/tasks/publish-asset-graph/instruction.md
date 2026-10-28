# Publish complete content-addressed asset releases

The small static-site builder in `/app` already scans HTML and CSS references and walks dependencies. It currently hashes raw asset bytes and exposes its new output directory before validation finishes, so a missing nested dependency can replace a working site with broken output.

Implement the publication contract in `README.md`. Hash assets after rewriting their dependencies, preserve HTML entry paths and URL query and fragment suffixes, reject missing files, cycles and source-tree escapes, and publish only a complete immutable release through an atomic replacement of the `current` symlink.

Keep the supplied URL scanner's documented grammar and the public build API and CLI. Add focused regression tests for nested dependencies and failed publication. Use only Python 3.14's standard library. Verification runs the code as an unprivileged user from a copy of `/app` that it may not be able to write, so keep the files the code or its tests create in temporary directories.
