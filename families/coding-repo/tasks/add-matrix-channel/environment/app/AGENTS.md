# AGENTS.md

Conventions for every change to `notify`.

- Each channel lives in `notify/channels/<name>.py` as a `<Name>Channel` subclass of `notify.base.Channel`, whose `name` class attribute is `<name>`.
- Register every channel in the `CHANNELS` tuple in `notify/registry.py`, which stays sorted by channel name.
- Report invalid configuration by raising `notify.errors.ConfigError` from `validate()`, with a message that starts with the channel name and a colon, such as `slack: webhook_url is required`. Never raise a bare `ValueError` or `KeyError` for configuration problems.
- A transport status outside 200-299 raises `notify.errors.DeliveryError` with the same `<name>: ` prefix.
- Log through `logging.getLogger(__name__)`, and never call `print`.
- Every function and method has complete type annotations, including the return type.
- Every channel has a test module `tests/test_<name>.py` that uses `tests.fakes.FakeTransport` and never the network.
- Record each user-visible change as a bullet under `## Unreleased` in `CHANGELOG.md`.
- Run `python3 -m unittest` before finishing.
