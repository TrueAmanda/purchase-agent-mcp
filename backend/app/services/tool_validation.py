"""Local JSON Schema validation with strict JSON integers and no remote refs."""

from jsonschema import Draft202012Validator, FormatChecker, validators
from referencing import Registry

StrictValidator = validators.extend(Draft202012Validator, type_checker=
    Draft202012Validator.TYPE_CHECKER.redefine("integer", lambda checker, value: type(value) is int))


def tool_validator(schema: dict):
    StrictValidator.check_schema(schema)
    return StrictValidator(schema, registry=Registry(), format_checker=FormatChecker())
