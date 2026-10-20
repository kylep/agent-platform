"""Explicit retrieval data for detached runs; never execution instructions."""
from typing import Annotated

from pydantic import BeforeValidator, Field, ValidationError


def validate_memory_query(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('memory_query must be a string or null')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeEncodeError:
        # FastAPI reflects validation inputs. A raw surrogate in that error
        # cannot itself be serialized to UTF-8; mask only this invalid input.
        raise ValidationError.from_exception_data('MemoryQuery', [{
            'type': 'value_error', 'loc': (), 'input': '<invalid Unicode>',
            'ctx': {'error': ValueError('memory_query must contain valid Unicode')},
        }]) from None
    if '\x00' in value or not value.strip():
        raise ValueError('memory_query must be nonblank and contain no NUL')
    if size > 1024:
        raise ValueError('memory_query must be at most 1024 UTF-8 bytes')
    return value.strip()


MemoryQuery = Annotated[str | None, BeforeValidator(validate_memory_query), Field(
    description='Optional detached retrieval query: nonblank, at most 1024 UTF-8 bytes. '
                'Null uses the existing execution-prompt query; PATCH omission preserves the stored value. '
                'Creation omission defaults to null. Does not change instructions or access.')]


def query_for_run(run):
    if run.memory_query is not None:
        return validate_memory_query(run.memory_query), 'explicit_memory_query'
    return (run.user_message, 'user_message') if run.user_message else (run.prompt, 'prompt')
