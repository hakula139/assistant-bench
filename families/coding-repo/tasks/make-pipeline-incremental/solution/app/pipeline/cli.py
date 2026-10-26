"""Command-line entry point."""

import argparse
import sys
from pathlib import Path

from .build import build, clean
from .config import ConfigError, load
from .graph import order
from .steps import StepFailed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='pipeline')
    parser.add_argument('--project', type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest='command', required=True)
    build_command = commands.add_parser('build', help='build the steps')
    build_command.add_argument(
        '--target', help='build only this step and its dependencies'
    )
    commands.add_parser('clean', help='delete every declared output')
    commands.add_parser('order', help='print the build order')
    args = parser.parse_args(argv)

    try:
        pipeline = load(args.project.resolve())
        match args.command:
            case 'build':
                build(pipeline, args.target)
            case 'clean':
                for output in clean(pipeline):
                    print(f'removed {output}')
            case 'order':
                print('\n'.join(order(pipeline)))
            case _:
                raise AssertionError(args.command)
    except (ConfigError, StepFailed) as error:
        print(f'pipeline: {error}', file=sys.stderr)
        return 1
    return 0
