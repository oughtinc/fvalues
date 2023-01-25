import ast
import inspect
import warnings
from collections.abc import Iterable
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
    # In rare cases this may not be the original source code,
    # but rather equivalent code produced by `ast.unparse()`.
    # This is needed due to Python bugs in slightly older versions
    # involving the locations of nodes within f-strings.
    source: str

    # Original (possibly non-str) value of the expression before formatting.
    value: Any

    # Final formatted string interpolated into the larger string.
    # If `value` is already a string and there isn't any formatting/conversion
    # then `value == formatted`.
    formatted: str

    def __str__(self) -> str:
        """
        This means that `str(part)` is what you'd expect for both part types.
        """
        return self.formatted


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
            expected = "".join(map(str, parts))
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

        if len(ex.node.args) > 1:
            return F(s, (s,))  # possible deserialization call

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
                # Happens with concatenation when the source node can't be found.
                parts.extend(part.flatten().parts)
            else:
                parts.append(part)
        return F(str(self), tuple(parts))

    def strip(self, *args) -> "F":
        """
        Similar to the usual `str.strip()`, but also correspondingly strips
        the contents of `.parts`.
        In FValues, `.formatted` is stripped, as well as `.value` if and only
        if it's a string.
        Empty parts are removed.
        """
        return self.lstrip(*args).rstrip(*args)

    def lstrip(self, *args) -> "F":
        """
        Like strip() but only on the left.
        """
        return self._strip(0, "lstrip", *args)

    def rstrip(self, *args) -> "F":
        """
        Like strip() but only on the right.
        """
        return self._strip(-1, "rstrip", *args)

    def _strip(self, index: int, method: str, *args) -> "F":
        """
        Apply `method` to the whole string and to the part at `index`.
        Also apply to `.formatted` and maybe `.value` in FValues.
        If the remaining part is empty, remove it and repeat.
        """
        parts = list(self.parts)
        while parts:
            part = parts[index]
            s = getattr(str(part), method)(*args)

            if not s:
                del parts[index]
                continue

            if isinstance(part, str):
                part = s
            else:
                value = part.value
                if isinstance(value, str):
                    value = getattr(value, method)(*args)
                part = FValue(part.source, value, s)

            parts[index] = part
            break

        # Strip the string itself.
        s = getattr(super(), method)(*args)
        return F(s, tuple(parts))

    def _add(self, other: str, is_left: bool) -> "F":
        """
        Concatenate with the other string.
        is_left is True for self+other, False for other+self.
        The result has two parts, one for each side.
        If the source node can be detected and corresponds to `+` or `+=`
        (as opposed to an implicit addition from something like `str.join`)
        then sides that aren't string literals will produce an FValue.
        """
        left, right = (self, other) if is_left else (other, self)
        value = str(left) + str(right)
        frame = get_frame().f_back  # get_frame() corresponds to __[r]add__
        assert frame is not None
        ex = executing.Source.executing(frame)

        if (
            ex.node is None
            and len(ex.statements) == 1
            and isinstance(stmt := list(ex.statements)[0], ast.AugAssign)
        ):
            # Before Python 3.11, `executing` doesn't currently set `.node`
            # for `+=`. This is easy to workaround because we can just get the
            # statement as long as there's only one, which is usually the case
            # i.e. when there's no semicolons.
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
            # Node couldn't be found or was unexpected type.
            parts = left, right

        return F(value, parts)

    def __add__(self, other: str) -> "F":
        return self._add(other, True)

    def __radd__(self, other: str) -> "F":
        return self._add(other, False)

    def join(self, iterable: Iterable[str]) -> "F":
        parts: list[Part] = []
        to_list = not isinstance(iterable, (list, tuple))
        if to_list:
            iterable = list(iterable)
        ex = executing.Source.executing(get_frame())
        iterable_source = None
        separator_source = None
        if (
            ex.node
            and isinstance(ex.node, ast.Call)
            and isinstance(ex.node.func, ast.Attribute)
            and ex.node.func.attr == "join"
            and len(ex.node.args) == 1
        ):
            [iterable_node] = ex.node.args
            iterable_source = get_node_source_text(iterable_node, ex.source)
            iterable_source = f"({iterable_source})"
            if to_list:
                iterable_source = f"list{iterable_source}"

            separator_node = ex.node.func.value
            separator_source = get_node_source_text(separator_node, ex.source)

        for i, item in enumerate(iterable):
            assert isinstance(item, str)
            if i:
                if separator_source:
                    parts.append(FValue(separator_source, self, str(self)))
                else:
                    parts.append(self)

            if iterable_source:
                parts.append(FValue(f"{iterable_source}[{i}]", item, str(item)))
            else:
                parts.append(item)
        return F(str(self).join(map(str, iterable)), tuple(parts))


def get_frame() -> FrameType:
    """
    Return the frame which is calling the function which is calling this.
    """
    return inspect.currentframe().f_back.f_back  # type: ignore


# noinspection PyTypeChecker
# (PyCharm being weird with AST)
@lru_cache
def compile_formatted_value(
    node: ast.FormattedValue, ex_source: executing.Source
) -> tuple[str, CodeType, CodeType]:
    """
    Returns three things that can be expensive to compute:
    1. Source code corresponding to the node.
    2. A compiled code object which can be evaluated to calculate the value.
    3. Another code object which formats the value.
    """
    source = get_node_source_text(node.value, ex_source)
    value_code = compile(source, "<fvalue1>", "eval")
    expr = ast.Expression(
        ast.JoinedStr(
            values=[
                # Similar to the original FormattedValue node,
                # but replace the actual expression with a simple variable lookup.
                # Use @ in the variable name so that it can't possibly conflict
                # with a normal variable.
                # The value of this variable will be provided in the eval() call
                # and will come from evaluating value_code above.
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
    """
    Returns some Python source code representing `node`:
    preferably the actual original code given by `ast.get_source_segment`,
    but falling back to `ast.unparse(node)` if the former is incorrect.
    """
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
