"""Error codes from the engine spec."""


class EngineError(Exception):
    """A rule, permission, or usage failure that writes no event."""

    def __init__(self, code, message, details=None, cmd=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.cmd = cmd


# Every code in spec §3.1. Deferred codes are not raised until the phase
# that has the rule (combat, spells, conditions).
ALL_CODES = (
    'BAD_ARGS',
    'UNKNOWN_ID',
    'NOT_AUTHORIZED',
    'NOT_YOUR_TURN',
    'NO_ACTION_LEFT',
    'NO_SLOT',
    'OUT_OF_RANGE',
    'INVALID_TARGET',
    'INCAPACITATED',
    'RULE_VIOLATION',
    'NO_CAMPAIGN',
    'LOG_TAMPERED',
)

DEFERRED_CODES = (
    'NOT_YOUR_TURN',
    'NO_ACTION_LEFT',
    'NO_SLOT',
    'OUT_OF_RANGE',
    'INVALID_TARGET',
    'INCAPACITATED',
)


def exit_code(code):
    """Usage errors are exit 2. Rule and permission errors are exit 1."""
    if code == 'BAD_ARGS':
        return 2
    return 1
