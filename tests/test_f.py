from fvalues import F
from fvalues import FValue


def test_f():
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
            source="F('hello ')",
            value="hello ",
            formatted="hello ",
        ),
        "world",
    )
    assert s.parts[0].value.parts == ("hello ",)
    s = "hello " + F("world")
    assert s == "hello world"
    parts = (
        "hello ",
        FValue(source="F('world')", value="world", formatted="world"),
    )
    assert s.parts == parts
    s += "!"
    assert s == "hello world!"
    assert s.parts == ("hello world", "!")
    assert s.flatten().parts == ("hello ", "world", "!")
    assert s.parts[0].parts == parts
