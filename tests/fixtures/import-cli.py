"""Import the `bench` CLI while refusing dotenv loading and network I/O."""

import importlib
import socket
from unittest import mock


def refuse(*args: object, **kwargs: object) -> None:
    raise AssertionError('Import attempted dotenv loading or network I/O')


with (
    mock.patch('dotenv.load_dotenv', refuse),
    mock.patch('socket.getaddrinfo', refuse),
    mock.patch.object(socket.socket, 'connect', refuse),
    mock.patch.object(socket.socket, 'connect_ex', refuse),
):
    importlib.import_module('assistant_bench.cli')
