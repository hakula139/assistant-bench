import json
from pathlib import Path

import pytest
from harbor.environments.docker import ssh
from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.task.config import EnvironmentConfig, NetworkMode, NetworkPolicy
from harbor.models.trial.paths import TrialPaths

from assistant_bench.harnesses.docker import (
    CONTROLLED_HOST_OVERLAY,
    HOST_OVERLAY,
    InstalledDocker,
    check_local_context,
)


class TestInstalledDocker:
    @pytest.mark.parametrize(
        ('mode', 'owner'),
        [
            (NetworkMode.NO_NETWORK, 'harbor-docker-egress-control-sidecar'),
            (NetworkMode.PUBLIC, 'main'),
        ],
        ids=['controlled', 'public'],
    )
    def test_maps_the_namespace_owner_without_reordering_task_overlays(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mode: NetworkMode,
        owner: str,
    ) -> None:
        def supported(cls: type[DockerEnvironment]) -> bool:
            return True

        monkeypatch.setattr(
            InstalledDocker, '_egress_control_kernel_support', classmethod(supported)
        )
        existing = tmp_path / 'task.yaml'
        existing.write_text('services: {}\n')
        policy = NetworkPolicy(network_mode=mode)
        environment = InstalledDocker(
            environment_dir=tmp_path,
            environment_name='example',
            session_id='example',
            trial_paths=TrialPaths(tmp_path / 'trial'),
            task_env_config=EnvironmentConfig(docker_image='debian:trixie-slim'),
            network_policy=policy,
            extra_docker_compose=[existing, HOST_OVERLAY, existing],
        )

        overlays = environment.extra_docker_compose_paths

        assert overlays == [
            existing,
            CONTROLLED_HOST_OVERLAY if mode == NetworkMode.NO_NETWORK else HOST_OVERLAY,
            existing,
        ]
        assert json.loads(overlays[1].read_text()) == {
            'services': {
                owner: {'extra_hosts': {'host.docker.internal': 'host-gateway'}}
            }
        }
        assert environment.network_policy == policy

    def test_keeps_the_separate_verifiers_cleared_overlays_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def supported(cls: type[DockerEnvironment]) -> bool:
            return True

        monkeypatch.setattr(
            InstalledDocker, '_egress_control_kernel_support', classmethod(supported)
        )
        environment = InstalledDocker(
            environment_dir=tmp_path,
            environment_name='example',
            session_id='example',
            trial_paths=TrialPaths(tmp_path / 'trial'),
            task_env_config=EnvironmentConfig(docker_image='debian:trixie-slim'),
            network_policy=NetworkPolicy(network_mode=NetworkMode.NO_NETWORK),
            extra_docker_compose=[],
        )

        assert environment.extra_docker_compose_paths == []


class TestCheckLocalContext:
    @pytest.mark.parametrize(
        ('endpoint', 'host', 'context', 'accepted'),
        [
            ('unix:///run/docker.sock', None, None, True),
            ('unix:///run/docker.sock', 'ssh://example.com', 'local', True),
            ('ssh://example.com', None, None, False),
            ('tcp://example.com:2376', None, None, False),
            ('tcp://127.0.0.1:2375', None, None, False),
            ('unix:///run/docker.sock', 'unix:///run/docker.sock', None, False),
        ],
        ids=[
            'local-context',
            'explicit-context-overrides-host',
            'ssh-context',
            'tcp-context',
            'localhost-tcp-unverified',
            'standalone-unix-host-requires-context',
        ],
    )
    def test_requires_a_local_context_and_preserves_native_endpoint_precedence(
        self,
        monkeypatch: pytest.MonkeyPatch,
        endpoint: str,
        host: str | None,
        context: str | None,
        accepted: bool,
    ) -> None:
        for name, value in (('DOCKER_HOST', host), ('DOCKER_CONTEXT', context)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)

        async def run_local(*command: str) -> bytes:
            if command == ('docker', 'context', 'show'):
                return b'local\n'
            assert command == (
                'docker',
                'context',
                'inspect',
                'local',
                '--format',
                '{{.Endpoints.docker.Host}}',
            )
            return endpoint.encode()

        monkeypatch.setattr(ssh, 'run_local', run_local)

        if accepted:
            check_local_context()
        else:
            with pytest.raises(ValueError, match='named local Docker context'):
                check_local_context()
