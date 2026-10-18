'Semantic intent contracts; no robot or map identifiers are generated here.'

from dataclasses import dataclass
from enum import Enum
from typing import Literal, TypeAlias
class IntentType(str, Enum):
    DELIVER_DOCUMENT = "DELIVER_DOCUMENT"
    UNKNOWN = "UNKNOWN"

class Priority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"

class Action(str, Enum):
    'Shared vocabulary for policies and user decisions.'

    WAIT = "WAIT"
    RETRY = "RETRY"
    CANCEL = "CANCEL"
    ASK_USER = "ASK_USER"

BusyPolicy: TypeAlias = Literal[Action.WAIT, Action.ASK_USER]
ClosedPolicy: TypeAlias = Literal[Action.WAIT, Action.RETRY, Action.ASK_USER]
UserDecision: TypeAlias = Literal[Action.WAIT, Action.RETRY, Action.CANCEL]
MissingField: TypeAlias = Literal["destination", "document", "priority", "busy_policy"]

@dataclass
class Constraints:
    ifBusy: BusyPolicy | None
    ifClosed: ClosedPolicy | None
@dataclass(init=False)
class ParsedIntent:
    intent: IntentType
    destinationText: str | None
    documentText: str | None
    priority: Priority
    constraints: Constraints
    missingFields: list[MissingField]

    def __init__(
        self,
        intent: IntentType,
        destinationText: str | None,
        documentText: str | None,
        priority: Priority,
        constraints: Constraints,
        missingFields: list[MissingField],
        recipientText: str | None = None,
    ):
        self.intent = intent
        self.destinationText = destinationText
        self.documentText = documentText
        self.priority = priority
        self.constraints = constraints
        self.missingFields = missingFields
        self._recipientText = recipientText

    @property
    def recipientText(self) -> str | None:
        return self._recipientText

@dataclass
class ResolvedDestination:
    locationId: str
    originalText: str
@dataclass
class ResolvedDocument:
    documentId: str | None
    originalText: str | None
@dataclass(init=False)
class ResolvedIntent:
    intent: Literal[IntentType.DELIVER_DOCUMENT]
    destination: ResolvedDestination
    document: ResolvedDocument
    priority: Priority
    constraints: Constraints

    def __init__(
        self,
        intent: Literal[IntentType.DELIVER_DOCUMENT],
        destination: ResolvedDestination,
        document: ResolvedDocument,
        priority: Priority,
        constraints: Constraints,
        recipientText: str | None = None,
    ):
        self.intent = intent
        self.destination = destination
        self.document = document
        self.priority = priority
        self.constraints = constraints
        self._recipientText = recipientText

    @property
    def recipientText(self) -> str | None:
        return self._recipientText
