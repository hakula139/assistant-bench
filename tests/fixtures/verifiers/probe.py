import argparse
import importlib
import importlib.machinery
import json
import pkgutil
import sys
import types
from collections.abc import Callable, Sequence
from importlib.resources import files


verify_lib = importlib.import_module('verify_lib')


class Multiply(argparse.Action):
    calls = 0

    def __init__(
        self,
        option_strings: Sequence[str],
        dest: str,
        *,
        multiplier: int,
        default: object = None,
    ) -> None:
        super().__init__(option_strings, dest, default=default)
        self.multiplier = multiplier
        Multiply.calls += 1

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        if not isinstance(values, str):
            raise TypeError('Expected one argument')
        setattr(namespace, self.dest, int(values) * self.multiplier)


def argument_adder(
    parser: argparse.ArgumentParser, scope: str
) -> Callable[..., argparse.Action]:
    if scope == 'group':
        return parser.add_argument_group('words').add_argument
    if scope == 'exclusive':
        return parser.add_mutually_exclusive_group().add_argument
    if scope == 'group-exclusive':
        return (
            parser.add_argument_group('words')
            .add_mutually_exclusive_group()
            .add_argument
        )
    if scope == 'child':
        return (
            parser.add_subparsers(dest='command', required=True)
            .add_parser('run')
            .add_argument
        )
    if scope == 'child-group':
        return (
            parser.add_subparsers(dest='command', required=True)
            .add_parser('run')
            .add_argument_group('words')
            .add_argument
        )
    return parser.add_argument


def option_probe(target: str, scope: str) -> list[dict[str, object]]:
    verify_lib.ignore_option(target)
    parser = argparse.ArgumentParser(allow_abbrev=False)
    add = argument_adder(parser, scope)
    add('--top', '-t', type=int, default=9)
    add('--stopwords', '-s', default='keep.txt')
    add('--other', action=Multiply, multiplier=3)
    parser.add_argument('tail', nargs='*')
    prefix = ['run'] if scope.startswith('child') else []
    vectors = [
        [],
        ['--top=4'],
        ['-t', '5'],
        ['--top', '2', '--top', '7'],
        ['--stopwords=skip.txt'],
        ['-s', 'skip.txt'],
        ['--stopwords', 'first.txt', '--stopwords', 'last.txt'],
        ['--other', '2'],
    ]
    if not prefix:
        vectors.append(['--', '--top=4'])

    return [vars(parser.parse_args(prefix + args)) for args in vectors]


def default_probe(case: str) -> dict[str, object]:
    verify_lib.ignore_option('--target')
    parser = argparse.ArgumentParser()
    if case == 'typed-unused':
        parser.add_argument('--target', default='7', type=int)
        argv: list[str] = []
    elif case in ('flag-unused', 'flag-used'):
        parser.add_argument('--target', action='store_true')
        argv = ['--target'] if case == 'flag-used' else []
    else:
        default = argparse.SUPPRESS if case == 'suppressed' else 'default'
        parser.add_argument('--target', default=default)
        argv = ['--target', 'ignored']
    namespace = argparse.Namespace(target='initial') if case == 'initial' else None

    return vars(parser.parse_args(argv, namespace=namespace))


def shared_action_probe() -> dict[str, object]:
    verify_lib.ignore_option('--target')
    first = argparse.ArgumentParser()
    first.add_argument('--target', action=Multiply, multiplier=7, default=11)
    first.add_argument('--other', action=Multiply, multiplier=3)
    second = argparse.ArgumentParser()
    second.add_argument('--target', action=Multiply, multiplier=5, default=13)
    second.add_argument('--other', action=Multiply, multiplier=4)

    a = first.parse_args(['--target', '2', '--other', '2'])
    b = second.parse_args(['--target', '2', '--other', '2'])

    return {'first': vars(a), 'second': vars(b), 'constructors': Multiply.calls}


def loader_probe(name: str) -> dict[str, object]:
    callbacks = 0

    def changed() -> str:
        return 'changed'

    replacement = types.FunctionType(changed.__code__, changed.__globals__)

    def patch(module: types.ModuleType) -> None:
        nonlocal callbacks
        callbacks += 1
        verify_lib.replace_function(module, 'value', replacement)

    verify_lib.after_import(name, patch)
    module = importlib.import_module(name)
    data = pkgutil.get_data(name, 'payload.txt')

    return {
        'value': module.value(),
        'alias': module.alias(),
        'callbacks': callbacks,
        'executions': module.executions(),
        'during': module.RESOURCE.decode(),
        'after': None if data is None else data.decode(),
        'modern': files('resource_fixture').joinpath('payload.txt').read_text(),
        'native_loader': isinstance(
            module.__loader__, importlib.machinery.SourceFileLoader
        ),
    }


def main() -> None:
    match sys.argv[1]:
        case 'option':
            result: object = option_probe(sys.argv[2], sys.argv[3])
        case 'default':
            result = default_probe(sys.argv[2])
        case 'shared-action':
            result = shared_action_probe()
        case 'loader':
            result = loader_probe(sys.argv[2])
        case _:
            raise ValueError('Unknown probe')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
