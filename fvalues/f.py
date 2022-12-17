import ast
import inspect
import warnings

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from types import CodeType
from types import FrameType
from typing import Any

import executing


@dataclass
class FValue:
    source: str
    value: Any
    formatted: str


Part = str | FValue
Parts = tuple[Part, ...]


class NoSourceAvailableWarning(Warning):
    pass


class F(str):
    parts: Parts

    def __new__(cls, s: str, parts: Parts | None = None):
        if parts is not None:
            expected = "".join(
                part.formatted if isinstance(part, FValue) else part for part in parts
            )
            assert s == expected, f"{s!r} != {expected!r}"
            result = super().__new__(cls, s)
            result.parts = parts
            return result

        frame = get_frame()
        ex = executing.Source.executing(frame)
        if ex.node is None:
            warnings.warn(
                "Couldn't get source node of F() call", NoSourceAvailableWarning
            )
            return F(s, (s,))

        assert isinstance(ex.node, ast.Call)
        [arg] = ex.node.args
        return F(s, F._parts_from_node(arg, frame, s, ex.source))

    @staticmethod
    def _parts_from_node(
        node: ast.expr,
        frame: FrameType,
        value: Part | None,
        ex_source: executing.Source,
    ) -> Parts:
        if isinstance(node, ast.Constant):
            assert isinstance(node.value, str)
            return (node.value,)
        elif isinstance(node, ast.JoinedStr):
            parts: list[Part] = []
            for node in node.values:
                parts.extend(F._parts_from_node(node, frame, None, ex_source))
            return tuple(parts)
        elif isinstance(node, ast.FormattedValue):
            source, value_code, formatted_code = compile_formatted_value(
                node, ex_source
            )
            value = eval(value_code, frame.f_globals, frame.f_locals)
            formatted = eval(
                formatted_code, frame.f_globals, frame.f_locals | {"@fvalue": value}
            )
            f_value = FValue(source, value, formatted)
            return (f_value,)
        else:
            assert isinstance(value, str)
            f_value = FValue(get_node_source_text(node, ex_source), value, value)
            return (f_value,)

    def __deepcopy__(self, memodict=None):
        return F(str(self), deepcopy(self.parts, memodict))

    def flatten(self) -> "F":
        parts: list[Part] = []
        for part in self.parts:
            if isinstance(part, FValue) and isinstance(part.value, F):
                parts.extend(part.value.flatten().parts)
            elif isinstance(part, F):
                parts.extend(part.flatten().parts)
            else:
                parts.append(part)
        return F(str(self), tuple(parts))

    def strip(self, *args) -> "F":
        return self.lstrip(*args).rstrip(*args)

    def lstrip(self, *args) -> "F":
        return self._strip(0, "lstrip", *args)

    def rstrip(self, *args) -> "F":
        return self._strip(-1, "rstrip", *args)

    def _strip(self, index: int, method: str, *args) -> "F":
        parts = list(self.parts)
        while True:
            part = parts[index]
            if isinstance(part, FValue):
                s = part.formatted
            else:
                s = part
            s = getattr(s, method)(*args)
            if s:
                if isinstance(part, FValue):
                    part = FValue(part.source, part.value, s)
                else:
                    part = s
                parts[index] = part
                break
            else:
                del parts[index]
        s = getattr(super(), method)(*args)
        return F(s, tuple(parts))

    def _add(self, other, is_left: bool):
        parts: Parts = (self, other) if is_left else (other, self)
        value = str(parts[0]) + str(parts[1])
        frame = get_frame().f_back
        assert frame is not None
        ex = executing.Source.executing(frame)
        if (
            ex.node is None
            and len(ex.statements) == 1
            and isinstance(stmt := list(ex.statements)[0], ast.AugAssign)
        ):
            node = stmt
        else:
            node = ex.node
        if isinstance(node, (ast.BinOp, ast.AugAssign)) and isinstance(
            node.op, ast.Add
        ):
            if isinstance(node, ast.AugAssign):
                left_node = node.target
                right_node = node.value
            else:
                left_node = node.left
                right_node = node.right
            left_parts = F._parts_from_node(left_node, frame, parts[0], ex.source)
            right_parts = F._parts_from_node(right_node, frame, parts[1], ex.source)
            parts = left_parts + right_parts

        return F(value, parts)

    def __add__(self, other):
        return self._add(other, True)

    def __radd__(self, other):
        return self._add(other, False)


def get_frame() -> FrameType:
    return inspect.currentframe().f_back.f_back  # type: ignore


# noinspection PyTypeChecker
# (PyCharm being weird with AST)
@lru_cache
def compile_formatted_value(
    node: ast.FormattedValue, ex_source: executing.Source
) -> tuple[str, CodeType, CodeType]:
    source = get_node_source_text(node.value, ex_source)
    value_code = compile(source, "<fvalue1>", "eval")
    expr = ast.Expression(
        ast.JoinedStr(
            values=[
                ast.FormattedValue(
                    value=ast.Name(id="@fvalue", ctx=ast.Load()),
                    conversion=node.conversion,
                    format_spec=node.format_spec,
                )
            ]
        )
    )
    ast.fix_missing_locations(expr)
    formatted_code = compile(expr, "<fvalue2>", "eval")
    return source, value_code, formatted_code


@lru_cache
def get_node_source_text(node: ast.AST, ex_source: executing.Source):
    source_unparsed = ast.unparse(node)
    source_segment = ast.get_source_segment(ex_source.text, node) or ""
    try:
        source_segment_unparsed = ast.unparse(ast.parse(source_segment, mode="eval"))
    except Exception:  # pragma: no cover  # TODO test a wonky f-string case
        source_segment_unparsed = ""
    return (
        source_segment
        if source_unparsed == source_segment_unparsed
        else source_unparsed
    )
