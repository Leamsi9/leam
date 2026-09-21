"""Exact logical submission identity, preserving pre-steering request hashes."""

import hashlib
import json


def fingerprint(thread_id, text, attachments, expected_turn=None):
    identity = [thread_id, text] + ([attachments] if attachments else [])
    if expected_turn is not None:
        identity.append({"expectedTurnId": expected_turn})
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()
