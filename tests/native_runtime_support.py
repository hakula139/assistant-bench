import asyncio
import hashlib
import json
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import tomllib
from collections.abc import AsyncGenerator, Generator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from textwrap import dedent
from typing import Self
from urllib.parse import parse_qs
from uuid import uuid4

import coverage
from pydantic import JsonValue, TypeAdapter

from assistant_bench.arena.runtime import (
    facade_config,
    load_target,
    management_config,
    renderer_config,
)
from assistant_bench_arena.ledger import Ledger
from ledger_support import D1Transport, Reply


ROOT = Path(__file__).resolve().parents[1]
APP_ORIGIN = 'https://app.example.com'
RENDER_ORIGIN = 'https://render.example.com'
NAMESPACE = 'native-runtime-test'
SITEKEY = 'synthetic-</script><script>alert("key")</script>'

_CONTROL = """
    from test_coverage import finish as coverage_finish
    from test_coverage import report as coverage_report
    from test_coverage import start as coverage_start

    coverage_start()

    import json
    import math
    import sys

    import app
    import render
    from app import Default as AppDefault
    from app import Management as AppManagement
    from app import RenderLookup as AppRenderLookup
    from assistant_bench_arena.admission import NetworkIdentity
    from assistant_bench_arena.api_models import StatusView
    from assistant_bench_arena.ledger_models import PointSnapshot
    from assistant_bench_arena.scheduler import pair_log_weights
    from assistant_bench_arena.worker import api, production
    from assistant_bench_arena.worker.authentication import Authentication
    from assistant_bench_arena.worker.bindings import LimitResult, NativeLimiter
    from pyodide.ffi import JsException
    from workers import WorkerEntrypoint
    from workers.rpc import JsDict

    coverage_finish()


    ingress_events = []
    original_lookup = AppRenderLookup.lookup


    class ObservedLimiter:
        def __init__(self, scope: str, limiter: NativeLimiter) -> None:
            self.scope = scope
            self.limiter = limiter

        async def limit(self, options: dict[str, str]) -> object:
            result = await self.limiter.limit(options)
            ingress_events.append({
                'kind': 'limit', 'scope': self.scope, 'key': options['key'],
                'success': LimitResult.model_validate(result).success,
            })
            return result


    class ObservedAuthentication(Authentication):
        async def preflight(self, address: str, now: int) -> NetworkIdentity:
            ingress_events.append({
                'kind': 'preflight', 'address': address, 'now': now,
            })
            return await super().preflight(address, now)


    def authentication(
        auth: Authentication, kind: type[Authentication],
    ) -> Authentication:
        return kind(
            auth.settings, auth.ledger, auth.ingress, auth.prefixes,
            auth.deployment, auth.actors, auth.challenge_secret,
            challenge_endpoint=auth.challenge_endpoint,
        )


    def observe_ingress(instance: AppDefault) -> None:
        auth = authentication(instance.owner.auth, ObservedAuthentication)
        auth.ingress = ObservedLimiter('exact', auth.ingress)
        auth.prefixes = ObservedLimiter('prefix', auth.prefixes)
        auth.deployment = ObservedLimiter('deployment', auth.deployment)
        instance.owner.auth = auth
        validate_request = production.validate_request
        request_body = api.request_body

        def observed_validate(request, profile, method):
            ingress_events.append({'kind': 'validate'})
            return validate_request(request, profile, method)

        async def observed_body(request):
            ingress_events.append({'kind': 'body'})
            return await request_body(request)

        production.validate_request = observed_validate
        api.request_body = observed_body


    class Default(AppDefault):
        async def fetch(self, request):
            coverage_start()
            try:
                return await super().fetch(request)
            finally:
                coverage_finish()

        async def scheduled(self, controller, env, ctx):
            coverage_start()
            try:
                return await super().scheduled(controller, env, ctx)
            finally:
                coverage_finish()


    class Management(AppManagement):
        async def command(self, serialized):
            coverage_start()
            try:
                return await super().command(serialized)
            finally:
                coverage_finish()


    class RenderLookup(AppRenderLookup):
        async def lookup(self, capability):
            coverage_start()
            try:
                return await super().lookup(capability)
            finally:
                coverage_finish()


    class TestControl(WorkerEntrypoint):
        async def execute(self, payload_json):
            payload = json.loads(payload_json)
            if payload.get('operation') == 'inspect':
                return json.dumps({'result': {
                    'heavy_import': any(name in sys.modules for name in (
                        'numpy', 'scipy', 'harbor', 'assistant_bench',
                    )),
                    'actual_app': Default is app.Default
                        or Default.__bases__ == (app.Default,),
                    'direct_entrypoint': Default is app.Default,
                    'actual_renderer': render.Default.__module__,
                }})
            if payload.get('operation') == 'configure_provider':
                instance = Default(self.ctx, self.env)
                instance.owner.auth.challenge_endpoint = self.env.TEST_PROVIDER_ENDPOINT
                return json.dumps({'result': {'configured': True}})
            if payload.get('operation') == 'observe_ingress':
                observe_ingress(Default(self.ctx, self.env))
                return json.dumps({'result': {'configured': True}})
            if payload.get('operation') == 'ingress_events':
                events = list(ingress_events)
                ingress_events.clear()
                return json.dumps({'result': {'events': events}})
            if payload.get('operation') == 'ingress_result':
                result = JsDict(payload['result'])

                class ConfiguredLimiter:
                    async def limit(self, options: dict[str, str]) -> object:
                        return result

                instance = Default(self.ctx, self.env)
                auth = authentication(instance.owner.auth, Authentication)
                auth.ingress = ConfiguredLimiter()
                instance.owner.auth = auth
                return json.dumps({'result': {'configured': True}})
            if payload.get('operation') == 'request_failure':
                stage = payload['stage']
                message = payload['message']

                def fail() -> None:
                    if payload['exception'] == 'validation':
                        LimitResult.model_validate({'success': message})
                    raise RuntimeError(message)

                class UnexpectedAuthentication(Authentication):
                    async def preflight(
                        self, address: str, now: int,
                    ) -> NetworkIdentity:
                        if stage == 'preflight':
                            fail()
                        return await super().preflight(address, now)

                    async def status(self, cookies: str, now: int) -> StatusView:
                        if stage == 'http':
                            fail()
                        return await super().status(cookies, now)

                    async def ensure_ready(self) -> None:
                        if stage == 'management':
                            fail()
                        await super().ensure_ready()

                instance = Default(self.ctx, self.env)
                instance.owner.auth = authentication(
                    instance.owner.auth,
                    Authentication if stage is None else UnexpectedAuthentication,
                )
                instance.owner.http = None
                return json.dumps({'result': {'configured': True}})
            if payload.get('operation') == 'lookup_result':
                async def lookup(self, capability: str) -> str:
                    return payload['result']

                AppRenderLookup.lookup = (
                    original_lookup if payload['result'] is None else lookup
                )
                return json.dumps({'result': {'configured': True}})
            if payload.get('operation') == 'coverage_probe':
                try:
                    import sqlite3
                except ImportError:
                    available = False
                else:
                    available = True
                return json.dumps({'result': {'sqlite3': available}})
            if payload.get('operation') == 'coverage':
                return coverage_report()
            if payload.get('operation') == 'pair_weights':
                snapshot = PointSnapshot.model_validate(payload['snapshot'])
                counts = {
                    (row['a_system'], row['b_system']): row['count']
                    for row in payload['coverage']
                }
                values = pair_log_weights(
                    snapshot, payload['pairs'], counts, payload['scale'],
                )
                return json.dumps({'result': {
                    'weights': [math.exp(value) for value in values],
                }})
            if payload.get('operation') == 'scheduled':
                try:
                    await Default(self.ctx, self.env).scheduled(None, None, None)
                except RuntimeError as error:
                    return json.dumps({'result': {'error': str(error)}})
                return json.dumps({'result': {'completed': True}})
            statements = [
                self.env.DB.prepare(item['sql']).bind(*item.get('params', []))
                for item in payload['statements']
            ]
            try:
                result = (await self.env.DB.batch(statements)
                    if payload['mode'] == 'batch' else await statements[0].all())
            except JsException as error:
                return json.dumps({'error': str(error)})
            return json.dumps({'result': result})
"""


_COVERAGE = """
    import hashlib
    import json
    from pathlib import Path

    import coverage

    measured = coverage.Coverage(
        data_file='/tmp/.coverage-native', config_file=False,
        include=['*/assistant_bench_arena/**/*.py', '*/app.py', '*/render.py'],
        branch=BRANCH,
    )
    measured.set_option('run:core', 'sysmon')
    observed = {}


    def start():
        measured.start()


    def finish():
        measured.stop()
        data = measured.get_data()
        for name in data.measured_files():
            observed[name] = {
                'sha256': hashlib.sha256(Path(name).read_bytes()).hexdigest(),
                'lines': data.lines(name),
                'arcs': data.arcs(name),
            }


    def report():
        return json.dumps({'result': {
            'version': coverage.__version__,
            'core': measured.get_option('run:core'),
            'branches': measured.get_option('run:branch'),
            'files': observed,
        }})
"""


class NativeProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.results: dict[str, bytes] = {}

    @contextmanager
    def serve(self) -> Generator[str]:
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                fields = parse_qs(
                    self.rfile.read(int(self.headers['Content-Length'])).decode()
                )
                assert set(fields) == {
                    'secret',
                    'response',
                    'remoteip',
                    'idempotency_key',
                }
                assert fields['secret'] == ['synthetic-secret']
                assert fields['remoteip'] == ['192.0.2.10']
                identity = fields['idempotency_key'][0]
                provider.calls.append(identity)
                if identity not in provider.results:
                    provider.results[identity] = json.dumps(
                        {
                            'success': True,
                            'challenge_ts': (
                                datetime.now(UTC) - timedelta(seconds=5)
                            ).isoformat(),
                            'hostname': 'app.example.com',
                            'action': 'bench_renew'
                            if fields['response'] == ['renew-proof']
                            else 'bench_admit',
                            'cdata': identity,
                        }
                    ).encode()
                content = provider.results[identity]
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}/siteverify'
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class NativeClient(D1Transport):
    async def call(self, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        assert self.process.stdin is not None and self.process.stdout is not None

        async with self.lock:
            self.process.stdin.write((json.dumps(payload) + '\n').encode())
            await self.process.stdin.drain()
            line = await asyncio.wait_for(self.process.stdout.readline(), 60)
            assert line, 'The native runtime client closed its protocol stream.'
            reply = Reply.model_validate_json(line)

        assert reply.error is None, reply.error
        assert isinstance(reply.result, dict)
        return reply.result

    async def http(
        self,
        path: str,
        *,
        body: dict[str, JsonValue] | str | None = None,
        cookie: str = '',
        renderer: bool = False,
        address: str = '192.0.2.10',
        forwarded_worker: bool = False,
        origin: str = APP_ORIGIN,
        sec_fetch_site: str = '',
    ) -> dict[str, JsonValue]:
        return await self.call(
            {
                'operation': 'http',
                'path': path,
                'body': body,
                'cookie': cookie,
                'renderer': renderer,
                'address': address,
                'forwarded_worker': forwarded_worker,
                'origin': origin,
                'sec_fetch_site': sec_fetch_site,
            }
        )


class NativeRuntime:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.provider = NativeProvider()
        self.coverage = coverage.Coverage.current()
        allowed = {
            'HOME',
            'PATH',
            'TMPDIR',
            'LANG',
            'SSL_CERT_FILE',
            'NIX_SSL_CERT_FILE',
            'NODE_EXTRA_CA_CERTS',
        }
        self.environment = {
            key: os.environ[key] for key in allowed if key in os.environ
        }
        self.environment.update(
            PATH=os.pathsep.join(
                [str(Path(sys.executable).parent), self.environment['PATH']]
            ),
            WRANGLER_SEND_METRICS='false',
            XDG_CONFIG_HOME=str(directory / 'config'),
        )

    @contextmanager
    def owner(self) -> Generator[Self]:
        with self.provider.serve() as endpoint, self._owner(endpoint):
            yield self

    @contextmanager
    def _owner(self, endpoint: str) -> Generator[None]:
        shutil.copytree(ROOT / 'src', self.directory / 'src')
        for name in ('pyproject.toml', 'pylock.toml'):
            shutil.copyfile(ROOT / name, self.directory / name)
        for name in ('rpc.ts', 'rpc-payload.ts'):
            shutil.copyfile(ROOT / 'arena' / name, self.directory / name)
        (self.directory / 'node_modules').symlink_to(ROOT / 'node_modules')
        if self.coverage is not None:
            assert coverage.__file__ is not None
            shutil.copytree(
                Path(coverage.__file__).parent,
                self.directory / 'src' / 'coverage',
                ignore=shutil.ignore_patterns('*.so', '*.pyd', '__pycache__'),
            )
            instrumentation = _COVERAGE.replace(
                'BRANCH', repr(bool(self.coverage.get_option('run:branch')))
            )
        else:
            instrumentation = """
                def start():
                    pass

                def finish():
                    pass

                def report():
                    return '{"result": {"enabled": false}}'
            """
        (self.directory / 'src' / 'test_coverage.py').write_text(
            dedent(instrumentation)
        )
        control = dedent(_CONTROL)
        renderer_source = dedent("""
                from test_coverage import finish, report, start

                start()

                from render import Default as RenderDefault
                from workers import WorkerEntrypoint

                finish()


                class Default(RenderDefault):
                    async def fetch(self, request):
                        start()
                        try:
                            return await super().fetch(request)
                        finally:
                            finish()


                class TestCoverage(WorkerEntrypoint):
                    async def dump(self):
                        return report()
            """)
        if self.coverage is None:
            control += dedent("""

                Default = AppDefault
                Management = AppManagement
                RenderLookup = AppRenderLookup
            """)
            renderer_source += dedent("""

                Default = RenderDefault
            """)
        (self.directory / 'src' / 'test_control.py').write_text(control)
        (self.directory / 'src' / 'test_renderer.py').write_text(renderer_source)
        assets = self.directory / 'assets'
        assets.mkdir()
        shutil.copyfile(ROOT / 'web' / 'public' / '_headers', assets / '_headers')
        (assets / 'arena-families.json').write_text(
            json.dumps(
                [
                    {
                        'name': 'writing',
                        'display_name': 'Synthetic writing',
                        'aspect': None,
                        'defects': ['tone'],
                    }
                ]
            )
        )
        (assets / 'index.html').write_text(
            '<html><head></head><body>Vote</body></html>'
        )
        vote = assets / 'vote'
        vote.mkdir()
        (vote / 'index.html').write_text((assets / 'index.html').read_text())
        name = f'runtime-test-{uuid4().hex}'
        manifest = tomllib.loads((ROOT / 'wrangler.toml').read_text())
        configuration = manifest['env']['staging']
        configuration.update(
            name=name,
            main='src/test_control.py',
            compatibility_date=manifest['compatibility_date'],
            compatibility_flags=manifest['compatibility_flags'],
            routes=[],
            observability={'enabled': False},
            assets={**configuration['assets'], 'directory': str(assets)},
        )
        configuration.pop('triggers', None)
        configuration.pop('secrets', None)
        configuration['d1_databases'] = [
            {
                'binding': 'DB',
                'database_name': name,
                'database_id': str(uuid4()),
            }
        ]
        key = json.dumps(
            {'versions': [{'version': 'test', 'starts_at': 0, 'key': 'k' * 32}]}
        )
        configuration['vars'] = {
            'ARENA_NAMESPACE': NAMESPACE,
            'APP_ORIGIN': APP_ORIGIN,
            'RENDER_ORIGIN': RENDER_ORIGIN,
            'SESSION_KEYS': key,
            'RENDER_KEYS': key,
            'NETWORK_KEYS': key,
            'TURNSTILE_SITEKEY': SITEKEY,
            'TURNSTILE_SECRET': 'synthetic-secret',
            'TEST_PROVIDER_ENDPOINT': endpoint,
        }
        target = load_target('staging').model_copy(
            update={
                'worker_name': name,
                'manifest_path': self.directory / 'wrangler.toml',
                'profile': load_target('staging').profile.model_copy(
                    update={
                        'namespace': NAMESPACE,
                        'app_origin': APP_ORIGIN,
                        'render_origin': RENDER_ORIGIN,
                    }
                ),
            }
        )
        with facade_config(target) as path:
            facade = tomllib.loads(path.read_text())
        with renderer_config(target) as path:
            renderer = tomllib.loads(path.read_text())
        with management_config(target) as path:
            management = tomllib.loads(path.read_text())
        for item in (
            *facade['services'],
            *renderer['services'],
            *management['services'],
        ):
            item['remote'] = False
        facade['main'] = str(self.directory / 'test_driver.ts')
        facade['services'].extend(
            [
                {'binding': 'APP', 'service': name},
                {'binding': 'RENDERER', 'service': name + '-render'},
            ]
        )
        renderer.update(
            main=str(self.directory / 'src' / 'test_renderer.py'),
            routes=[],
            vars={**renderer['vars'], 'RENDER_KEYS': key},
        )
        renderer.pop('secrets')
        client = {
            'name': name + '-client',
            'compatibility_date': manifest['compatibility_date'],
            'services': [
                {'binding': 'APP', 'service': name},
                {'binding': 'RENDERER', 'service': name + '-render'},
                {
                    'binding': 'MANAGEMENT',
                    'service': name + '-rpc',
                    'entrypoint': 'Management',
                },
                {'binding': 'CONTROL', 'service': name, 'entrypoint': 'TestControl'},
                {
                    'binding': 'BROWSER',
                    'service': name + '-rpc',
                    'entrypoint': 'TestBrowser',
                },
                {
                    'binding': 'RENDER_COVERAGE',
                    'service': name + '-render',
                    'entrypoint': 'TestCoverage',
                },
            ],
        }
        for label, value in (
            ('owner', configuration),
            ('facade', facade),
            ('renderer', renderer),
            ('client', client),
            ('management', management),
        ):
            (self.directory / f'{label}.json').write_text(json.dumps(value))
        (self.directory / 'wrangler.jsonc').write_text(json.dumps(configuration))
        (self.directory / 'test_driver.ts').write_text(
            dedent(f"""
                import {{WorkerEntrypoint}} from 'cloudflare:workers';
                export {{Management, RenderLookup}} from './rpc';
                export {{default}} from './rpc';

                export class TestBrowser extends WorkerEntrypoint {{
                    async request(serialized) {{
                        const payload = JSON.parse(serialized);
                        const renderer = payload.renderer;
                        const headers = new Headers({{
                            'CF-Connecting-IP': payload.address,
                        }});
                        if (payload.forwarded_worker) {{
                            headers.set('CF-Worker', 'synthetic.example');
                        }}
                        if (payload.cookie) headers.set('Cookie', payload.cookie);
                        if (payload.sec_fetch_site) {{
                            headers.set('Sec-Fetch-Site', payload.sec_fetch_site);
                        }}
                        const init = {{headers}};
                        if (payload.body !== null) {{
                            init.method = 'POST';
                            headers.set('Origin', payload.origin);
                            headers.set('Content-Type', 'application/json');
                            init.body = typeof payload.body === 'string'
                                ? payload.body : JSON.stringify(payload.body);
                        }}
                        const origin = renderer
                            ? {json.dumps(RENDER_ORIGIN)}
                            : {json.dumps(APP_ORIGIN)};
                        const target = renderer ? 'RENDERER' : 'APP';
                        const response = await this.env[target].fetch(
                            new Request(origin + payload.path, init));
                        return JSON.stringify({{result: {{
                            status: response.status, body: await response.text(),
                            headers: Object.fromEntries(response.headers),
                        }}}});
                    }}
                }}
            """)
        )
        (self.directory / 'bridge.mjs').write_text(
            dedent(f"""
                import {{Console}} from 'node:console';
                import {{createRequire}} from 'node:module';
                import {{createInterface}} from 'node:readline';
                globalThis.console = new Console({{
                    stdout: process.stderr, stderr: process.stderr,
                }});
                const {{getPlatformProxy}} = createRequire(
                    {json.dumps(str(ROOT / 'package.json'))}
                )('wrangler');
                const platform = await getPlatformProxy({{
                    configPath: {json.dumps(str(self.directory / 'client.json'))},
                    envFiles: ['/dev/null'], remoteBindings: false, persist: false,
                }});
                try {{
                    const input = createInterface({{input: process.stdin}});
                    for await (const line of input) {{
                        const payload = JSON.parse(line);
                        let result;
                        if (payload.operation === 'http') {{
                            result = await platform.env.BROWSER.request(line);
                        }} else if (payload.operation === 'renderer_coverage') {{
                            result = await platform.env.RENDER_COVERAGE.dump();
                        }} else if (payload.operation === 'management') {{
                            const reply = await platform.env.MANAGEMENT.command(
                                JSON.stringify(payload.request));
                            result = JSON.stringify({{result: JSON.parse(reply)}});
                        }} else {{
                            result = await platform.env.CONTROL.execute(line);
                        }}
                        process.stdout.write(result + '\\n');
                    }}
                }} finally {{ await platform.dispose(); }}
            """)
        )
        with ExitStack() as stack:
            for label in ('owner', 'facade', 'renderer'):
                stack.enter_context(self._worker(label))
            yield

    @contextmanager
    def _worker(self, label: str) -> Generator[None]:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        executable = (
            [str(Path(sys.executable).with_name('pywrangler'))]
            if label != 'facade'
            else [str(ROOT / 'node_modules' / '.bin' / 'wrangler')]
        )
        process = subprocess.Popen(
            [
                *executable,
                'dev',
                '--config',
                str(self.directory / f'{label}.json'),
                '--local',
                '--ip',
                '127.0.0.1',
                '--port',
                str(port),
                '--inspector-port',
                '0',
                '--env-file',
                '/dev/null',
                '--persist-to',
                str(self.directory / 'state'),
            ],
            cwd=self.directory,
            env=self.environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        lines: queue.Queue[str | None] = queue.Queue()

        def record() -> None:
            assert process.stdout is not None
            with (self.directory / f'{label}.log').open('w') as output:
                for line in process.stdout:
                    output.write(line)
                    output.flush()
                    lines.put(line)
            lines.put(None)

        reader = threading.Thread(target=record, daemon=True)
        reader.start()
        try:
            while True:
                line = lines.get(timeout=120)
                assert line is not None, (
                    f'The native {label} Worker exited before readiness.'
                )
                if 'Ready on' in line:
                    break
            yield
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=30)
            reader.join(timeout=5)

    @asynccontextmanager
    async def client(self) -> AsyncGenerator[tuple[Ledger, NativeClient]]:
        with (self.directory / 'client.log').open('wb') as errors:
            process = await asyncio.create_subprocess_exec(
                'node',
                str(self.directory / 'bridge.mjs'),
                cwd=ROOT,
                env=self.environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=errors,
                limit=4_194_304,
            )
            transport = NativeClient(process)
            try:
                capability = await transport.call({'operation': 'coverage_probe'})
                (self.directory / 'coverage-capability.json').write_text(
                    json.dumps(capability)
                )
                yield Ledger(transport), transport
            finally:
                try:
                    if self.coverage is not None:
                        for operation in ('coverage', 'renderer_coverage'):
                            exported = await transport.call({'operation': operation})
                            (self.directory / f'{operation}.json').write_text(
                                json.dumps(exported)
                            )
                            self._merge_coverage(exported)
                finally:
                    assert process.stdin is not None
                    process.stdin.close()
                    await process.stdin.wait_closed()
                    output, _ = await asyncio.wait_for(process.communicate(), 30)
                    assert process.returncode == 0 and not output

    async def management_status(self) -> dict[str, JsonValue]:
        source = dedent("""
            import asyncio
            import sys
            from pathlib import Path

            from pydantic import TypeAdapter

            from assistant_bench.arena.management import connect
            from assistant_bench_arena.management_models import ManagementStatus, Status


            async def main():
                async with connect(Path(sys.argv[1])) as management:
                    status = await management.domain(
                        Status(), TypeAdapter(ManagementStatus | None),
                    )
                    assert status is not None
                    print(status.model_dump_json())


            asyncio.run(main())
        """)
        assert (
            not {'CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_ACCOUNT_ID'}
            & self.environment.keys()
        )
        with (self.directory / 'management.log').open('wb') as errors:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                '-c',
                source,
                str(self.directory / 'management.json'),
                cwd=ROOT,
                env=self.environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=errors,
            )
            output, _ = await asyncio.wait_for(process.communicate(), 60)

        assert process.returncode == 0, (self.directory / 'management.log').read_text()
        return TypeAdapter(dict[str, JsonValue]).validate_json(output)

    def _merge_coverage(self, exported: dict[str, JsonValue]) -> None:
        assert self.coverage is not None
        assert exported['version'] == coverage.__version__
        assert exported['core'] == 'sysmon'
        files = exported['files']
        assert isinstance(files, dict) and files, (
            'The native Worker recorded no source coverage.'
        )
        lines: dict[str, list[int]] = {}
        arcs: dict[str, list[tuple[int, int]]] = {}

        for name, entry in files.items():
            assert isinstance(entry, dict)
            relative = Path(name).relative_to('/session/metadata')
            assert relative.parts[0] == 'assistant_bench_arena' or relative in {
                Path('app.py'),
                Path('render.py'),
            }
            source = ROOT / 'src' / relative
            assert hashlib.sha256(source.read_bytes()).hexdigest() == entry['sha256']
            lines[str(source)] = TypeAdapter(list[int]).validate_python(entry['lines'])
            arcs[str(source)] = TypeAdapter(list[tuple[int, int]]).validate_python(
                entry['arcs'] or []
            )

        if exported['branches']:
            self.coverage.get_data().add_arcs(arcs)
        else:
            self.coverage.get_data().add_lines(lines)
