"""PromQL tokenizer — Baysöz Labs <baysozlabs@proton.me>."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ParseError

_NUMBER = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d|w|y)")
_IDENT = re.compile(r"[a-zA-Z_][a-zA-Z0-9_:]*")
_UNIT_SECONDS = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
    "y": 31536000.0,
}
_OPERATORS = ("==", "!=", ">=", "<=", "=~", "!~", "+", "-", "*", "/", "%", "^", ">", "<", "=")
_PUNCTUATION = frozenset("{}[](),@:")


@dataclass(frozen=True, slots=True)
class Token:
    """A positioned token."""

    kind: str
    value: str
    position: int
    line: int
    column: int


def duration_to_seconds(text: str, position: int = 0) -> float:
    """Convert a PromQL duration such as ``1h30m`` to seconds."""
    if not text:
        raise ParseError("empty duration", position)
    total = 0.0
    offset = 0
    while offset < len(text):
        match = _DURATION_PART.match(text, offset)
        if not match:
            raise ParseError(f"invalid duration component {text[offset:]!r}", position + offset)
        total += float(match.group(1)) * _UNIT_SECONDS[match.group(2)]
        offset = match.end()
    if total <= 0:
        raise ParseError("duration must be positive", position)
    return total


def tokenize(source: str) -> tuple[Token, ...]:
    """Tokenize a PromQL expression with exact source positions."""
    tokens: list[Token] = []
    index = 0
    line = 1
    column = 1
    length = len(source)
    while index < length:
        char = source[index]
        if char.isspace():
            if char == "\n":
                line += 1
                column = 1
            else:
                column += 1
            index += 1
            continue
        if char == "#":
            while index < length and source[index] != "\n":
                index += 1
                column += 1
            continue
        start, start_column = index, column
        if char in "\"'`":
            quote = char
            index += 1
            buffer: list[str] = []
            closed = False
            while index < length:
                current = source[index]
                if current == "\\" and quote != "`":
                    if index + 1 >= length:
                        break
                    escapes = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}
                    escaped = source[index + 1]
                    if escaped not in escapes:
                        raise ParseError(f"invalid escape \\{escaped}", index)
                    buffer.append(escapes[escaped])
                    index += 2
                    column += 2
                    continue
                if current == quote:
                    index += 1
                    column += 1
                    closed = True
                    break
                if current == "\n" and quote != "`":
                    break
                buffer.append(current)
                index += 1
                column += 1
            if not closed:
                raise ParseError("unterminated string literal", start)
            tokens.append(Token("string", "".join(buffer), start, line, start_column))
            continue
        duration = _DURATION_PART.match(source, index)
        if duration and (
            duration.end() == length
            or not (source[duration.end()].isalnum() or source[duration.end()] == "_")
        ):
            value = duration.group(0)
            tokens.append(Token("duration", value, start, line, start_column))
            index = duration.end()
            column += len(value)
            continue
        number = _NUMBER.match(source, index)
        if number:
            value = number.group(0)
            tokens.append(Token("number", value, start, line, start_column))
            index = number.end()
            column += len(value)
            continue
        identifier = _IDENT.match(source, index)
        if identifier:
            value = identifier.group(0)
            tokens.append(Token("ident", value, start, line, start_column))
            index = identifier.end()
            column += len(value)
            continue
        operator = next((op for op in _OPERATORS if source.startswith(op, index)), None)
        if operator:
            tokens.append(Token("op", operator, start, line, start_column))
            index += len(operator)
            column += len(operator)
            continue
        if char in _PUNCTUATION:
            tokens.append(Token("punct", char, start, line, start_column))
            index += 1
            column += 1
            continue
        raise ParseError(f"unexpected character {char!r}", start)
    tokens.append(Token("eof", "", length, line, column))
    return tuple(tokens)
