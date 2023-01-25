import time
from copy import deepcopy
from typing import Any
from typing import Dict

import pytest

from fvalues import F
from fvalues import FValue
from fvalues import NoSourceAvailableWarning


def test_formatting():
    numbers = [1.23456789, 2, 3]
    ndigits = 2
    s = F(
        f"number is approximately equal to {numbers[0]:.{ndigits}f}, "
        f"rounded to {ndigits = } places."
    )
    assert s == "number is approximately equal to 1.23, rounded to ndigits = 2 places."
    assert s.parts == (
        "number is approximately equal to ",
        FValue(source="numbers[0]", value=1.23456789, formatted="1.23"),
        ", rounded to ndigits = ",
        FValue(source="ndigits", value=2, formatted="2"),
        " places.",
    )


def test_add():
    s = F("hello ") + "world"
    assert s == "hello world"
    assert s.parts == (
        FValue(
            source='F("hello ")',
            value="hello ",
            formatted="hello ",
        ),
        "world",
    )
    assert s.parts[0].value.parts == ("hello ",)  # type: ignore

    s = "hello " + F("world")
    assert s == "hello world"
    parts = (
        "hello ",
        FValue(source='F("world")', value="world", formatted="world"),
    )
    assert s.parts == parts

    s += "!"
    assert s == "hello world!"
    assert s.parts == (
        FValue(source="s", value="hello world", formatted="hello world"),
        "!",
    )
    assert s.flatten().parts == ("hello ", "world", "!")
    assert s.parts[0].value.parts == parts  # type: ignore


def test_add_f():
    f1 = F(f"hello {1 + 2}")
    f2 = F(f"world {3 + 4}")
    f3 = f1 + " " + f2
    assert f3 == "hello 3 world 7"
    assert f3.parts == (
        FValue(source='f1 + " "', value="hello 3 ", formatted="hello 3 "),
        FValue(source="f2", value="world 7", formatted="world 7"),
    )
    assert f3.flatten().parts == (
        "hello ",
        FValue(source="1 + 2", value=3, formatted="3"),
        " ",
        "world ",
        FValue(source="3 + 4", value=7, formatted="7"),
    )


def test_no_node():
    with pytest.warns(
        NoSourceAvailableWarning, match=r"Couldn't get source node of F\(\) call"
    ):
        # exec/eval don't make source code accessible at all in general.
        s = eval('F(f"hello {1 + 2}")')
    assert s == "hello 3"
    assert s.parts == ("hello 3",)

    s2 = F(f"{s}!")
    s3 = eval("s + s2")
    assert s3 == "hello 3hello 3!"
    assert s3.parts == (s, s2)
    # Assert that these are the same actual F strings, not plain strings
    assert s3.parts[0] is s
    assert s3.parts[1] is s2
    assert s3.flatten().parts == ("hello 3", "hello 3", "!")


def test_strip():
    space = " "
    s = F(f" {space} hello {space} ")
    assert s == "   hello   "
    assert s.parts == (
        " ",
        FValue(source="space", value=" ", formatted=" "),
        " hello ",
        FValue(source="space", value=" ", formatted=" "),
        " ",
    )
    assert s.strip() == "hello"
    assert s.strip(space) == "hello"
    assert s.lstrip() == "hello   "
    assert s.lstrip(space) == "hello   "
    assert s.rstrip() == "   hello"
    assert s.rstrip(space) == "   hello"
    assert s.strip().parts == ("hello",)
    assert s.lstrip().parts == (
        "hello ",
        FValue(source="space", value=" ", formatted=" "),
        " ",
    )
    assert s.rstrip().parts == (
        " ",
        FValue(source="space", value=" ", formatted=" "),
        " hello",
    )
    assert s.strip().strip("ho").strip() == "ell"

    s = F(f"{' a'}b ")
    assert s == " ab "
    assert s.strip() == "ab"
    assert s.strip().parts == (FValue(source="' a'", value="a", formatted="a"), "b")


def test_strip_empty():
    for s in [
        F(""),
        F(" "),
        F(f""),  # noqa
        F(f" "),  # noqa
        F(f"{''}"),
        F(f"{' '}"),
        F(f" {''} "),
        F(f" {' '} "),
    ]:
        assert s.strip() == s.strip().strip() == s.lstrip() == s.rstrip() == ""
        assert not s.strip()
        assert len(s.strip()) == 0


def test_strip_flatten():
    s = F(f" a {1}")
    assert s == " a 1"
    one_fval = FValue(source="1", value=1, formatted="1")
    assert s.parts == (" a ", one_fval)

    assert s.strip() == "a 1"
    assert s.strip().parts == ("a ", one_fval)

    s += "!"
    assert s == " a 1!"
    s_fval = FValue(source="s", value=" a 1", formatted=" a 1")
    assert s.parts == (s_fval, "!")
    assert s.flatten().parts == (" a ", one_fval, "!")

    s = s.strip()
    assert s == "a 1!"
    assert s.parts == (FValue(source="s", value="a 1", formatted="a 1"), "!")
    assert s.flatten().parts == ("a ", one_fval, "!")


def test_deepcopy():
    name = "world"
    s = F(f"hello {name}")
    check_deepcopy(s)
    s = F(f"{s}!")
    assert s == "hello world!"
    check_deepcopy(s)


def check_deepcopy(s: F):
    s2 = deepcopy(s)
    assert s == s2
    assert s is not s2
    assert s.parts == s2.parts
    for p1, p2 in zip(s.parts, s2.parts):
        assert p1 == p2
        if not isinstance(p1, str):
            assert p1 is not p2


def test_caching():
    # Check that many calls are fast thanks to cached compiling.
    start = time.time()
    for _ in range(30000):
        s = F(f"hello {1 + 2}")
        assert s == "hello 3"
    end = time.time()
    assert end - start < 1


def test_get_source_segment():
    # Check that original source code is typically used.
    s1 = F(f"hello {(1) + 2}")
    s2 = F(f"hello {1 + (2)}")
    assert s1.parts == (
        "hello ",
        FValue(source="(1) + 2", value=3, formatted="3"),
    )
    assert s2.parts == (
        "hello ",
        FValue(source="1 + (2)", value=3, formatted="3"),
    )


def test_bad_source_segment():
    s = F(
        f"""
        {1 + (2)}
        """
    ).strip()
    [part] = s.parts
    assert isinstance(part, FValue)
    # Depending on Python version, ast.get_source_segment may be wrong,
    # fallback to ast.unparse instead.
    assert part.source in ("1 + (2)", "1 + 2")
    assert part.value == 3
    assert part.formatted == "3"


def test_other_node_type_call_arg():
    s = "foo"
    s = F(F(s))
    assert s == "foo"
    (part,) = s.parts
    assert part == FValue(source="F(s)", value="foo", formatted="foo")
    assert isinstance(part, FValue)  # for mypy
    assert (
        part.value.parts
        == s.flatten().parts
        == (FValue(source="s", value="foo", formatted="foo"),)
    )


def test_join_non_list():
    strings = (x for x in ["a", "b", "c"])
    s = F(" ").join(strings)
    assert s == "a b c"
    assert s.parts == (
        FValue(source="list(strings)[0]", value="a", formatted="a"),
        FValue(source='F(" ")', value=" ", formatted=" "),
        FValue(source="list(strings)[1]", value="b", formatted="b"),
        FValue(source='F(" ")', value=" ", formatted=" "),
        FValue(source="list(strings)[2]", value="c", formatted="c"),
    )
    assert s.flatten().parts == (
        FValue(source="list(strings)[0]", value="a", formatted="a"),
        " ",
        FValue(source="list(strings)[1]", value="b", formatted="b"),
        " ",
        FValue(source="list(strings)[2]", value="c", formatted="c"),
    )


def test_join_list():
    strings = ["a", "b", "c"]
    s = F("").join(strings)
    assert s == "abc"
    assert s.parts == (
        FValue(source="(strings)[0]", value="a", formatted="a"),
        FValue(source='F("")', value="", formatted=""),
        FValue(source="(strings)[1]", value="b", formatted="b"),
        FValue(source='F("")', value="", formatted=""),
        FValue(source="(strings)[2]", value="c", formatted="c"),
    )
    assert s.flatten().parts == (
        FValue(source="(strings)[0]", value="a", formatted="a"),
        "",
        FValue(source="(strings)[1]", value="b", formatted="b"),
        "",
        FValue(source="(strings)[2]", value="c", formatted="c"),
    )


def test_join_bad_source():
    strings = ["a", "b", "c"]
    s = F.join(F(","), strings)
    assert s == "a,b,c"
    assert s.parts == s.flatten().parts == ("a", ",", "b", ",", "c")


def test_deserialization():
    # pyyaml deserialization reconstructs F with multiple arguments:
    # https://github.com/yaml/pyyaml/blob/957ae4d/lib/yaml/constructor.py#L591
    # make sure that there is no error resulting from this
    cls = F
    args = ["test_str"]
    kwds: Dict[str, Any] = {}
    # copied straight from pyyaml, so ignore mypy complaints
    new_f = cls.__new__(cls, *args, **kwds)  # type: ignore
    # during deserialization, the state of the object will be updated after
    # construction anyways, so there's no need to check for anything other than
    # successful object creation
    assert new_f == "test_str"
