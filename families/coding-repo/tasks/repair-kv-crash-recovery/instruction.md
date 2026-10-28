# Make tinykv survive crashes

Users of the `tinykv` store in `/app` report three problems. A store that crashed during `compact` sometimes refuses to open. Writes made after recovering from a crash can vanish at the next restart. A long-lived store once returned an old value for a key after a restart.

Find and fix the causes so that the store meets the durability contract in `README.md`, keeping its Python API, command line and file format, so stores written by the current version still open. Add regression tests that reproduce each failure. Use only Python 3.14's standard library. Verification runs the code as an unprivileged user from a copy of `/app` that it may not be able to write, so keep the files the code or its tests create in temporary directories.
