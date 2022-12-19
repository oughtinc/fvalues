import ast
import inspect
import warnings

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from types import CodeType
from types import FrameType
from typing import Any
from typing import Optional
from typing import Union

import executing


@dataclass
class FValue:
    """
    A non-constant part of an F string, corresponding to either
    an expression inside braces (`{}`) in an f-string, or
    a non-literal string in a concatenation (`+` or `+=`).
    """

    # Python source code of the expression.
    # Doesn't include the format spec or conversion specifier in f-strings,
    # e.g. in `{foo()!r:.2f}` it's just the `foo()`.
    # In rare cases this may
    source: str

    # Original (possibly non-str) value of the expression before formatting.
    value: Any

    # Final formatted string interpolated into the larger string.
    # If `value` is already a string and there isn't any formatting/conversion
    # then `value == formatted`.
    formatted: str


Part = Union[str, FValue]
Parts = tuple[Part, ...]  # type of F.parts


class NoSourceAvailableWarning(Warning):
    """
    Indicates that the source code corresponding to an F() call couldn't be found.
    This typically means that no source code file for any of the executed code
    was available, e.g. if the code was executed in the standard shell/REPL
    or with `exec()` or `eval()`.
    In rare cases it may be caused by a limitation with `executing`
    finding the correct expression within the available source code,
    e.g. inside an `assert` statement when using `pytest` which has its own magic.
    Either way, the resulting F object will simply fall back to a single string
    part, i.e. no FValues.
    """


class F(str):
    parts: Parts

    def __new__(cls, s: str, parts: Optional[Parts] = None):
        if parts is not None:
            # No magic when parts are provided.

            # Sanity check that the parts add up correctly.
            expected = "".join(
                part.formatted if isinstance(part, FValue) else part for part in parts
            )
            assert s == expected, f"{s!r} != {expected!r}"

            result = super().__new__(cls, s)
            result.parts = parts
            return result

        frame = get_frame()  # frame where F() was called
        ex = executing.Source.executing(frame)
        if ex.node is None:
            warnings.warn(
                "Couldn't get source node of F() call", NoSourceAvailableWarning
            )
            return F(s, (s,))

        assert isinstance(ex.node, ast.Call)
        [arg] = ex.node.args
        return F(s, F._parts_from_node(arg, ex, s))

    @staticmethod
    def _parts_from_node(
        node: ast.expr,
        ex: executing.Executing,
        value: Optional[str],
    ) -> Parts:
        """
        Extract one or more parts (strings or FValues) corresponding to the AST node.
        `node` should a descendant of `ex.node`.
        `value` should be the actual runtime value associated with the node if known.
        """
        if isinstance(node, ast.Constant):
            # Simple literal string part.
            # Could be a string literal in a concatenation,
            # or one of JoinedStr (f-string) values that isn't a FormattedValue.
            assert isinstance(node.value, str)
            return (node.value,)
        elif isinstance(node, ast.JoinedStr):  # f-string
            parts: list[Part] = []
            for node in node.values:  # ast.Constant or ast.FormattedValue
                # The values of these nodes are not known,
                # but don't need to be given here.
                parts.extend(F._parts_from_node(node, ex, None))
            return tuple(parts)
        elif isinstance(node, ast.FormattedValue):
            source, value_code, formatted_code = compile_formatted_value(
                node, ex.source
            )
            frame = ex.frame
            value = eval(value_code, frame.f_globals, frame.f_locals)
            formatted = eval(
                formatted_code, frame.f_globals, frame.f_locals | {"@fvalue": value}
            )
            f_value = FValue(source, value, formatted)
            return (f_value,)
        else:
            # Part of a concatenation.
            assert isinstance(node.parent, (ast.BinOp, ast.AugAssign))  # type: ignore
            assert isinstance(value, str)
            f_value = FValue(get_node_source_text(node, ex.source), value, value)
            return (f_value,)

    def __deepcopy__(self, memodict=None) -> "F":
        return F(str(self), deepcopy(self.parts, memodict))

    def flatten(self) -> "F":
        """
        Return an equivalent F string with any nested F strings
        (typically within an FValue part)
        expanded out into their constituent parts at the top level of `.parts`.
        Such nesting occurs when an F string is constructed over multiple
        formatting/concatenation steps.
        Flattening lets you work with the parts more simply when the exact origin
        of each part isn't that important.
        A downside is that you're more likely to end up with multiple FValues
        with the same `.source` but different values as they were evaluated
        at different times, even for pure expressions like variable names.
        """
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
        while parts:
            part = parts[index]
            if isinstance(part, FValue):
                s = part.formatted
            else:
                s = part
            s = getattr(s, method)(*args)
            if s:
                if isinstance(part, FValue):
                    value = part.value
                    if isinstance(part.value, str):
                        value = getattr(value, method)(*args)
                    part = FValue(part.source, value, s)
                else:
                    part = s
                parts[index] = part
                break
            else:
                del parts[index]
        s = getattr(super(), method)(*args)
        return F(s, tuple(parts))

    def _add(self, other: str, is_left: bool) -> "F":
        left, right = (self, other) if is_left else (other, self)
        value = str(left) + str(right)
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
            left_parts = F._parts_from_node(left_node, ex, left)
            right_parts = F._parts_from_node(right_node, ex, right)
            parts = left_parts + right_parts
        else:
            parts = left, right

        return F(value, parts)

    def __add__(self, other: str) -> "F":
        return self._add(other, True)

    def __radd__(self, other: str) -> "F":
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
    except Exception:
        source_segment_unparsed = ""
    return (
        source_segment
        if source_unparsed == source_segment_unparsed
        else source_unparsed
    )
