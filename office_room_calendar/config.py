import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


def flag(name):
    value = os.environ.get(name, "false").lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


@dataclass(frozen=True)
class CalendarSettings:
    database_url: str = field(repr=False)
    session_secret: str = field(repr=False)
    public_origin: str = "http://127.0.0.1:8000"
    timezone: str = "Asia/Ho_Chi_Minh"
    travel_seconds: int = 420
    handoff_seconds: int = 180
    evidence_seconds: int = 30
    nlp_enabled: bool = False

    def __post_init__(self):
        origin = urlsplit(self.public_origin)
        origin.port  # Validate malformed/out-of-range ports before accepting an origin.
        if (origin.scheme not in {"http", "https"} or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment
                or origin.scheme == "http" and origin.hostname not in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("CALENDAR_PUBLIC_ORIGIN must be an HTTPS origin (loopback HTTP allowed for development)")
        if not self.database_url or len(self.session_secret) < 32:
            raise ValueError("Calendar requires its dedicated database URL and a separate session secret of at least 32 characters")
        ZoneInfo(self.timezone)
        if not (0 <= self.travel_seconds <= 86400 and 1 <= self.handoff_seconds <= 28800 and 1 <= self.evidence_seconds <= 60):
            raise ValueError("Calendar timing configuration is out of range")

    @property
    def secure_cookie(self):
        return self.public_origin.startswith("https://")

    @classmethod
    def from_environment(cls):
        try:
            if os.environ.get("RENDER", "").lower() == "true" and not os.environ.get("CALENDAR_PUBLIC_ORIGIN", "").startswith("https://"):
                raise ValueError("Render calendar requires its explicit HTTPS origin")
            if os.environ.get("CALENDAR_SESSION_SECRET") == os.environ.get("API_ACCESS_TOKEN"):
                raise ValueError("Calendar session secret must be distinct")
            return cls(os.environ.get("CALENDAR_DATABASE_URL", ""), os.environ.get("CALENDAR_SESSION_SECRET", ""),
                       os.environ.get("CALENDAR_PUBLIC_ORIGIN", "http://127.0.0.1:8000"),
                       os.environ.get("CALENDAR_TIMEZONE", "Asia/Ho_Chi_Minh"),
                       int(os.environ.get("CALENDAR_TRAVEL_SECONDS", "420")), int(os.environ.get("CALENDAR_HANDOFF_SECONDS", "180")),
                       int(os.environ.get("CALENDAR_EVIDENCE_SECONDS", "30")), flag("CALENDAR_NLP_ENABLED"))
        except (ValueError, KeyError):
            raise ValueError("Invalid calendar configuration; check dedicated CALENDAR_* environment variables privately") from None
