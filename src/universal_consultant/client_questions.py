"""Вопросы клиента и ответы на них по документу базы знаний через локальную Ollama.

На вход подаётся текст документа. Модель формулирует реплики от лица клиента
и сама отвечает на них фактами из этого документа.
"""

import re
from typing import Any

from ollama import Client
from pydantic import BaseModel, Field, ValidationError

DEFAULT_MODEL = "qwen3.8:27b"

_SYSTEM_PROMPT = """\
Ты готовишь проверочные пары «вопрос клиента — ответ консультанта».

По документу базы знаний составь вопросы так, будто их задаёт клиент в чате.
На каждый вопрос сразу дай ответ только по фактам этого документа: цена, условие, \
срок, адрес, услуга, ограничение.
Не спрашивай и не отвечай тем, чего в документе нет.
Если задано название объекта, каждый вопрос содержит его отличительную часть.
По вопросу должно быть понятно, о какой позиции каталога речь, даже среди похожих документов.
Вопрос звучит как живая речь, ответ короткий и по делу.
Один вопрос — один факт.\
"""


class ClientQuestion(BaseModel):
    """Вопрос клиента и ответ по документу."""

    question: str = Field(description="Вопрос клиента на русском языке.")
    answer: str = Field(description="Короткий ответ по фактам документа.")


class ClientQuestions(BaseModel):
    """Структурированный ответ модели."""

    questions: list[ClientQuestion]


def generate_client_questions(
    document: str,
    *,
    subject: str | None = None,
    model: str = DEFAULT_MODEL,
    count: int | None = None,
    host: str | None = None,
) -> list[ClientQuestion]:
    """Сгенерировать вопросы клиента и ответы на них по документу.

    Запрос уходит в локальную Ollama. Схема ответа — объект с полем ``questions``,
    у каждого элемента есть ``question`` и ``answer``.
    ``subject`` — название объекта, о котором документ. Каждый вопрос должен его
    содержать, чтобы поиск отличал документ от похожих.

    Args:
        document: Текст документа из базы знаний.
        subject: Название объекта, например номер или услуга.
        model: Имя модели в Ollama.
        count: Сколько пар вернуть. Если не задано, модель сама выбирает число
            по разным фактам документа.
        host: Адрес Ollama. Если не передан, клиент берёт свой адрес по умолчанию.

    Returns:
        Вопросы клиента и ответы на них.
    """
    text = document.strip()
    if not text:
        raise ValueError("Документ пустой.")
    if count is not None and count < 1:
        raise ValueError("count должен быть не меньше 1.")
    anchor = _anchor(subject) if subject and subject.strip() else None

    questions = _request(text, count=count, anchor=anchor, model=model, host=host)
    if anchor is not None and not _all_mention(questions, anchor):
        questions = _request(text, count=count, anchor=anchor, model=model, host=host)
    if anchor is not None:
        missed = [item.question for item in questions if not _mentions(item.question, anchor)]
        if missed:
            raise RuntimeError(f"Вопрос не называет объект «{anchor}»: {missed[0]}")
    return questions


def _request(
    document: str,
    *,
    count: int | None,
    anchor: str | None,
    model: str,
    host: str | None,
) -> list[ClientQuestion]:
    client = Client(host=host) if host else Client()
    response = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(document, count, anchor)},
        ],
        format=_schema(count, anchor),
        think=False,
        options={"temperature": 0.4},
    )
    content = response.message.content
    if not content or not content.strip():
        raise RuntimeError("Модель не вернула вопросы и ответы.")
    questions = _questions_from_content(content)
    if count is not None and len(questions) != count:
        raise RuntimeError(f"Ожидалось пар: {count}, получено: {len(questions)}.")
    return questions


def _anchor(subject: str) -> str:
    quoted = re.findall(r"«([^»]+)»", subject)
    anchor = quoted[-1] if quoted else subject.strip()
    if not anchor:
        raise ValueError("Название объекта пустое.")
    return anchor


def _mentions(question: str, anchor: str) -> bool:
    return anchor.casefold() in question.casefold()


def _all_mention(questions: list[ClientQuestion], anchor: str) -> bool:
    return all(_mentions(item.question, anchor) for item in questions)


def _schema(count: int | None, anchor: str | None) -> dict[str, Any]:
    questions: dict[str, Any] = {
        "type": "array",
        "description": "Вопросы клиента и ответы на них.",
        "items": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "Вопрос клиента на русском языке."
                        if anchor is None
                        else f"Вопрос клиента на русском языке. Обязательно содержит «{anchor}»."
                    ),
                },
                "answer": {
                    "type": "string",
                    "description": "Короткий ответ только по фактам документа.",
                },
            },
            "required": ["question", "answer"],
            "additionalProperties": False,
        },
    }
    if count is not None:
        questions["minItems"] = count
        questions["maxItems"] = count
    return {
        "type": "object",
        "properties": {"questions": questions},
        "required": ["questions"],
        "additionalProperties": False,
    }


def _user_prompt(document: str, count: int | None, anchor: str | None) -> str:
    if count is None:
        task = "Сформулируй несколько вопросов по разным фактам документа и ответь на них."
    else:
        task = f"Сформулируй ровно {count} вопросов по разным фактам документа и ответь на них."
    if anchor is None:
        return f"{task}\n\nДокумент:\n{document}"
    naming = (
        f"В каждом вопросе напиши «{anchor}» так, как сказал бы клиент. "
        "Без этого названия нельзя понять, о каком объекте вопрос."
    )
    return f"{task}\n{naming}\n\nДокумент:\n{document}"


def _questions_from_content(content: str) -> list[ClientQuestion]:
    parsed = _parse_model(content)
    raw = parsed.questions
    if not raw:
        raise RuntimeError("Модель не вернула вопросы и ответы.")
    questions: list[ClientQuestion] = []
    for item in raw:
        question, answer = item.question.strip(), item.answer.strip()
        if not question or not answer:
            raise RuntimeError("В паре есть пустой вопрос или ответ.")
        questions.append(ClientQuestion(question=question, answer=answer))
    return questions


def _parse_model(content: str) -> ClientQuestions:
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```").removesuffix("```").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return ClientQuestions.model_validate_json(text)
    except ValidationError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise RuntimeError("Модель не вернула вопросы и ответы.") from None
        try:
            return ClientQuestions.model_validate_json(text[start : end + 1])
        except ValidationError:
            raise RuntimeError("Модель не вернула вопросы и ответы.") from None
