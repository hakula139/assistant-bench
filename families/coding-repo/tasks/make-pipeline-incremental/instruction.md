# Make pipeline builds incremental

The `pipeline` build tool in `/app` reruns every step on each `build`, which makes builds of large projects slow. Make `build` incremental, so that it runs a step exactly when the step's outputs could differ from those its last successful run left: when the files its patterns match, its declaration in `pipeline.toml` or the module that defines its function have changed, or when its outputs no longer hold what that run wrote. An incremental build must always leave the same outputs as a build of a clean copy of the project.

Keep the existing `pipeline.toml` format, step interface, command line and Python API, and report skipped steps in `BuildReport.skipped`. Document how incremental builds behave in `README.md`, and add tests for them. Use only Python 3.14's standard library.
