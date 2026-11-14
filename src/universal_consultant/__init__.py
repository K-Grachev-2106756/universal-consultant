"""Диалоговый консультант: вопросы по базе знаний и таблица доступности."""

from universal_consultant.availability import (
    AvailabilityRow,
    generate_availability,
)
from universal_consultant.client_questions import ClientQuestion, generate_client_questions

__all__ = [
    "AvailabilityRow",
    "ClientQuestion",
    "generate_availability",
    "generate_client_questions",
]
