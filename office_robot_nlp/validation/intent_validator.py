'''Standard-library decoding and deterministic validation of delivery intents.

A resolver will eventually supply a resolution result. No location matching, task
creation, LLM calls, or robot/world-state queries take place here.
'''

import json
from dataclasses import dataclass
from enum import Enum
from typing import Literal, TypeAlias, TypeVar
from office_robot_nlp.domain.intent import (
    Action, Constraints, IntentType, ParsedIntent, Priority,
)
from office_robot_nlp.domain.map import MapCatalog, MapLocation
class ValidationError(str, Enum):
    INVALID_LLM_OUTPUT = "INVALID_LLM_OUTPUT"
    UNSUPPORTED_INTENT = "UNSUPPORTED_INTENT"
    MISSING_DESTINATION = "MISSING_DESTINATION"
    UNRESOLVED_DESTINATION = "UNRESOLVED_DESTINATION"
    UNKNOWN_LOCATION = "UNKNOWN_LOCATION"
    AMBIGUOUS_LOCATION = "AMBIGUOUS_LOCATION"
    INVALID_PRIORITY = "INVALID_PRIORITY"
    INVALID_POLICY = "INVALID_POLICY"

@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[ValidationError, ...] = ()

@dataclass(frozen=True)
class IntentSchemaResult:
    intent: ParsedIntent | None
    validation: ValidationResult
@dataclass(frozen=True)
class ResolvedLocation:
    status: Literal["RESOLVED"]
    locationId: str
@dataclass(frozen=True)
class UnknownLocation:
    status: Literal["NOT_FOUND"]

@dataclass(frozen=True)
class AmbiguousLocation:
    status: Literal["AMBIGUOUS"]
    candidates: list[str]

EntityResolutionResult: TypeAlias = ResolvedLocation | UnknownLocation | AmbiguousLocation

def _failure(*errors: ValidationError) -> ValidationResult:
    return ValidationResult(False, tuple(dict.fromkeys(errors)))


EnumValue = TypeVar('EnumValue', bound=Enum)


def _enum_value(enum_type: type[EnumValue], value: object) -> EnumValue | None:
    if not isinstance(value, str):
        return None
    try:
        return enum_type(value)
    except ValueError:
        return None

def decode_parsed_intent(payload: object) -> IntentSchemaResult:
    '''Decode an untrusted JSON object/string to the Phase 1 dataclass.

    Reject unknown keys and wrong field types. Do not infer omitted fields or
    trust Python dataclass annotations to perform runtime validation.
    '''
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return IntentSchemaResult(None, _failure(ValidationError.INVALID_LLM_OUTPUT))

    required_keys = {
        "intent", "destinationText", "documentText", "priority", "constraints", "missingFields"
    }
    if (not isinstance(payload, dict)
            or not required_keys.issubset(set(payload))
            or set(payload) - required_keys - {"recipientText"}):
        return IntentSchemaResult(None, _failure(ValidationError.INVALID_LLM_OUTPUT))

    constraints = payload["constraints"]
    missing = payload["missingFields"]
    if (not isinstance(constraints, dict) or set(constraints) != {"ifBusy", "ifClosed"}
            or not isinstance(missing, list)
            or any(type(item) is not str or item not in {"destination", "document", "priority", "busy_policy"}
                   for item in missing)
                or any(value is not None and not isinstance(value, str)
                    for value in (payload["destinationText"], payload["documentText"], payload.get("recipientText")))):
        return IntentSchemaResult(None, _failure(ValidationError.INVALID_LLM_OUTPUT))

    errors: list[ValidationError] = []
    intent = _enum_value(IntentType, payload["intent"])
    if intent is None:
        errors.append(ValidationError.UNSUPPORTED_INTENT if isinstance(payload["intent"], str)
                      else ValidationError.INVALID_LLM_OUTPUT)
    priority = _enum_value(Priority, payload["priority"])
    if priority is None:
        errors.append(ValidationError.INVALID_PRIORITY)
    busy = constraints["ifBusy"]
    closed = constraints["ifClosed"]
    if busy is not None and _enum_value(Action, busy) not in (Action.WAIT, Action.ASK_USER):
        errors.append(ValidationError.INVALID_POLICY)
    if closed is not None and _enum_value(Action, closed) not in (Action.WAIT, Action.RETRY, Action.ASK_USER):
        errors.append(ValidationError.INVALID_POLICY)
    if errors:
        return IntentSchemaResult(None, _failure(*errors))

    parsed = ParsedIntent(
        intent=intent,
        destinationText=payload["destinationText"],
        documentText=payload["documentText"],
        priority=priority,
        constraints=Constraints(ifBusy=Action(busy) if busy is not None else None,
                                ifClosed=Action(closed) if closed is not None else None),
        missingFields=list(missing),
        recipientText=payload.get("recipientText"),
    )
    return IntentSchemaResult(parsed, ValidationResult(True))


def validate_intent(
    intent: object,
    resolution: EntityResolutionResult | None = None,
    catalog: MapCatalog | None = None,
) -> ValidationResult:
    '''Validate a ParsedIntent and, when supplied, its externally resolved target.

    Full success requires a RESOLVED location present in the supplied catalog.
    Until then the intent is not eligible for downstream task construction.
    '''
    if not isinstance(intent, ParsedIntent):
        return _failure(ValidationError.INVALID_LLM_OUTPUT)
    decoded = decode_parsed_intent({
        "intent": intent.intent,
        "destinationText": intent.destinationText,
        "documentText": intent.documentText,
        "recipientText": intent.recipientText,
        "priority": intent.priority,
        "constraints": {
            "ifBusy": intent.constraints.ifBusy, "ifClosed": intent.constraints.ifClosed
        } if isinstance(intent.constraints, Constraints) else None,
        "missingFields": intent.missingFields,
    })
    if not decoded.validation.valid:
        return decoded.validation
    errors: list[ValidationError] = []
    if intent.intent is IntentType.UNKNOWN:
        errors.append(ValidationError.UNSUPPORTED_INTENT)
    if intent.intent is IntentType.DELIVER_DOCUMENT:
        if not intent.destinationText or not intent.destinationText.strip():
            errors.append(ValidationError.MISSING_DESTINATION)
        elif resolution is None:
            errors.append(ValidationError.UNRESOLVED_DESTINATION)
        elif isinstance(resolution, UnknownLocation) and resolution.status == "NOT_FOUND":
            errors.append(ValidationError.UNKNOWN_LOCATION)
        elif isinstance(resolution, AmbiguousLocation) and resolution.status == "AMBIGUOUS":
            errors.append(ValidationError.AMBIGUOUS_LOCATION)
        elif not isinstance(resolution, ResolvedLocation) or resolution.status != "RESOLVED":
            errors.append(ValidationError.INVALID_LLM_OUTPUT)
        elif catalog is None:
            errors.append(ValidationError.UNRESOLVED_DESTINATION)
        elif (not isinstance(catalog, MapCatalog) or not isinstance(catalog.locations, list)
              or any(not isinstance(location, MapLocation) or not isinstance(location.id, str)
                     for location in catalog.locations)):
            errors.append(ValidationError.INVALID_LLM_OUTPUT)
        elif (not isinstance(resolution.locationId, str) or not resolution.locationId.strip()
              or not any(location.id == resolution.locationId for location in catalog.locations)):
            errors.append(ValidationError.UNKNOWN_LOCATION)
    return _failure(*errors) if errors else ValidationResult(True)
