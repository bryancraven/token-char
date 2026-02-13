"""Field definitions and validation for token-char output schema."""

TURN_FIELDS = [
    "source",
    "machine",
    "project",
    "session_id",
    "turn_number",
    "timestamp",
    "model",
    "model_family",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_create_tokens",
    "total_tokens",
]

SESSION_FIELDS = [
    "source",
    "machine",
    "project",
    "session_id",
    "title",
    "model",
    "created_at",
    "duration_min",
    "turns_user",
    "turns_assistant",
    "total_input_tokens",
    "total_output_tokens",
    "total_cache_read_tokens",
    "total_cache_create_tokens",
    "total_tokens",
]

_TURN_TYPES = {
    "source": str,
    "machine": str,
    "project": str,
    "session_id": str,
    "turn_number": int,
    "timestamp": (str, type(None)),
    "model": str,
    "model_family": str,
    "input_tokens": int,
    "output_tokens": int,
    "cache_read_tokens": int,
    "cache_create_tokens": int,
    "total_tokens": int,
}

_SESSION_TYPES = {
    "source": str,
    "machine": str,
    "project": str,
    "session_id": str,
    "title": str,
    "model": str,
    "created_at": (str, type(None)),
    "duration_min": (int, float, type(None)),
    "turns_user": int,
    "turns_assistant": int,
    "total_input_tokens": int,
    "total_output_tokens": int,
    "total_cache_read_tokens": int,
    "total_cache_create_tokens": int,
    "total_tokens": int,
}


def validate_turn(d):
    """Validate a turn dict has all required fields with correct types.
    Returns list of error strings (empty if valid)."""
    errors = []
    for field in TURN_FIELDS:
        if field not in d:
            errors.append(f"missing field: {field}")
            continue
        expected = _TURN_TYPES[field]
        if isinstance(expected, tuple):
            if not isinstance(d[field], expected):
                errors.append(f"{field}: expected {expected}, got {type(d[field])}")
        else:
            if not isinstance(d[field], expected):
                errors.append(f"{field}: expected {expected}, got {type(d[field])}")
    return errors


def validate_session(d):
    """Validate a session dict has all required fields with correct types.
    Returns list of error strings (empty if valid)."""
    errors = []
    for field in SESSION_FIELDS:
        if field not in d:
            errors.append(f"missing field: {field}")
            continue
        expected = _SESSION_TYPES[field]
        if isinstance(expected, tuple):
            if not isinstance(d[field], expected):
                errors.append(f"{field}: expected {expected}, got {type(d[field])}")
        else:
            if not isinstance(d[field], expected):
                errors.append(f"{field}: expected {expected}, got {type(d[field])}")
    return errors
