from dataclasses import dataclass, field


class NoIdentifiers(Exception):
    """The lead has nothing the platform can match on."""


class EventTooOld(Exception):
    pass


@dataclass
class SendResult:
    ok: bool
    status_code: int | None = None
    body: str = ''
    retryable: bool = False
    token_invalid: bool = False
    error_code: str = ''
    error_message: str = ''
    trace_id: str = ''
    events_received: int | None = None
    duration_ms: int | None = None
    extra: dict = field(default_factory=dict)


def short(text, n=4000):
    text = text or ''
    return text if len(text) <= n else text[:n] + '…[truncated]'
