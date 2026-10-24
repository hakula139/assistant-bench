"""Validated native configuration and purpose-specific signing material."""

from typing import Protocol

from assistant_bench_arena.policy import configuration
from assistant_bench_arena.runtime_models import KeyRing, Profile


class ProfileBindings(Protocol):
    ARENA_NAMESPACE: str
    APP_ORIGIN: str
    RENDER_ORIGIN: str


class ConfigurationBindings(ProfileBindings, Protocol):
    SESSION_KEYS: str
    RENDER_KEYS: str
    NETWORK_KEYS: str
    TURNSTILE_SITEKEY: str


def profile(bindings: ProfileBindings) -> Profile:
    return Profile.model_validate(
        {
            'namespace': bindings.ARENA_NAMESPACE,
            'app_origin': bindings.APP_ORIGIN,
            'render_origin': bindings.RENDER_ORIGIN,
        }
    )


class Settings:
    def __init__(self, bindings: ConfigurationBindings) -> None:
        self.profile = profile(bindings)
        self.sessions = KeyRing.model_validate_json(bindings.SESSION_KEYS)
        self.renders = KeyRing.model_validate_json(bindings.RENDER_KEYS)
        self.network = KeyRing.model_validate_json(bindings.NETWORK_KEYS)
        self.sitekey = bindings.TURNSTILE_SITEKEY

        if not self.sitekey:
            raise ValueError('Voting requires its challenge site key.')

        self.bootstrap = configuration(self.profile.namespace)
