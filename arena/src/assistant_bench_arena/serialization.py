"""Canonical identities for sealed inventory and semantic ledger commands."""

import hashlib
import json

from pydantic import BaseModel, JsonValue


def canonical(value: BaseModel | JsonValue) -> str:
    data = value.model_dump(mode='json') if isinstance(value, BaseModel) else value
    return json.dumps(
        data, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False
    )


def digest(value: BaseModel | JsonValue) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def source_identity(
    case_key: str, system: str, fingerprint: str, job: str, trial: str
) -> str:
    return digest(
        {
            'case_key': case_key,
            'system': system,
            'fingerprint': fingerprint,
            'job': job,
            'trial': trial,
        }
    )
