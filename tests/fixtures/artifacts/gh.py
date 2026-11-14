import json
import os
import sys
from pathlib import Path
from typing import Any


arguments = sys.argv[1:]
if not arguments or arguments[0] != 'api':
    raise SystemExit(2)
if code := int(os.environ.get('TEST_GH_STATUS', '0')):
    raise SystemExit(code)
metadata = Path(os.environ['TEST_GH_METADATA'])
if arguments[1:] == ['repos/example/private-data']:
    sys.stdout.write(metadata.read_text())
else:
    if 'repos/example/private-data/pulls' not in arguments:
        raise SystemExit(2)
    records = metadata.with_name('pulls.json')
    requests: list[dict[str, Any]] = (
        json.loads(records.read_text()) if records.exists() else []
    )
    fields = dict(
        item.split('=', 1)
        for index, item in enumerate(arguments)
        if index and arguments[index - 1] == '-f'
    )
    if arguments[arguments.index('--method') + 1] == 'POST':
        if os.environ.get('TEST_GH_PR_FAILURE'):
            raise SystemExit(1)
        request = {
            'number': len(requests) + 1,
            'html_url': f'https://example.com/pull/{len(requests) + 1}',
            'state': 'open',
            'merged_at': None,
            'merge_commit_sha': None,
            'head': {
                'ref': fields['head'],
                'repo': {'full_name': 'example/private-data'},
            },
            'base': {
                'ref': fields['base'],
                'repo': {'full_name': 'example/private-data'},
            },
        }
        requests.append(request)
        records.write_text(json.dumps(requests))
        sys.stdout.write(json.dumps(request))
    else:
        sys.stdout.write(
            json.dumps(
                [
                    request
                    for request in requests
                    if os.environ.get('TEST_GH_MISDIRECTED')
                    or request['head']['ref'] == fields['head'].split(':', 1)[1]
                ]
            )
        )
