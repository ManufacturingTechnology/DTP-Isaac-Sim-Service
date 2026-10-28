from enum import Enum
from pathlib import Path
from typing import Annotated, List, Literal, Optional, Tuple

import pytest
from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo

from amt.dtp.ise.lib.model_editing import (
    default_value, drop_target, is_list_annotation, item_title, length_limits, list_item_annotation,
    model_defaults, move, to_plain, unwrap_optional,
)


class Color(str, Enum):
    RED = "red"
    BLUE = "blue"


class Inner(BaseModel):
    name: str = Field(title="Name")
    count: int = 3


class Outer(BaseModel):
    inner: Inner
    required_optional: Optional[Inner]
    tags: List[str] = Field(default_factory=lambda: ["a"])
    maybe: Optional[Inner] = None
    people: List[Inner]


def test_list_annotations():
    assert list_item_annotation(List[int]) is int
    assert list_item_annotation(list[str]) is str
    assert list_item_annotation(Tuple[float, ...]) is float
    assert list_item_annotation(Tuple[float, float]) is None
    assert list_item_annotation(int) is None
    assert is_list_annotation(Optional[List[int]])
    assert not is_list_annotation(Optional[int])
    assert unwrap_optional(Optional[List[int]]) == (List[int], True)


def test_length_limits():
    class M(BaseModel):
        xs: List[int] = Field(min_length=1, max_length=5)
        ys: List[int] = []
    assert length_limits(M.model_fields["xs"]) == (1, 5)
    assert length_limits(M.model_fields["ys"]) == (None, None)


def test_default_values():
    assert default_value(str) == ""
    assert default_value(int) == 0
    assert default_value(float) == 0.0
    assert default_value(bool) is False
    assert default_value(Color) is Color.RED
    assert default_value(Literal["A", "B"]) == "A"
    assert default_value(Path) == Path("")
    assert default_value(List[int]) == []
    assert default_value(Optional[int]) is None
    # constraints from Annotated items
    fi = FieldInfo.from_annotation(Annotated[int, Field(ge=2, le=10)])
    assert default_value(fi.annotation, fi) == 2


def test_model_defaults_omit_required_primitives():
    assert default_value(Inner) == {"count": 3}
    d = model_defaults(Outer)
    assert d == {"inner": {"count": 3}, "required_optional": None, "tags": ["a"], "maybe": None, "people": []}
    # the defaults validate once the required fields are filled in
    d["inner"]["name"] = "x"
    Outer(**d)


def test_to_plain_keeps_raw_types():
    o = Outer(inner=Inner(name="a"), required_optional=None, people=[Inner(name="b")])
    plain = to_plain(o)
    assert plain["people"] == [{"name": "b", "count": 3}]
    assert isinstance(plain["inner"], dict)


def test_item_title():
    assert item_title({"name": "home"}, 0, Inner, "name") == 'Inner "home"'
    assert item_title({"name": ""}, 2, Inner, "name") == "Inner 2"
    assert item_title(Inner(name="x"), 1, Inner) == "Inner 1"
    assert item_title(5, 4) == "Item 4"


@pytest.mark.parametrize("source,target,expected", [
    (0, 2, ["b", "c", "a", "d"]),
    (3, 0, ["d", "a", "b", "c"]),
    (1, 1, ["a", "b", "c", "d"]),
])
def test_move(source, target, expected):
    assert move(["a", "b", "c", "d"], source, target) == expected


def test_move_out_of_range():
    with pytest.raises(IndexError):
        move(["a"], 0, 1)


@pytest.mark.parametrize("source,gap,expected", [
    (0, 0, 0), (0, 1, 0), (0, 2, 1), (0, 4, 3),   # moving down: the gap below shifts by one
    (3, 0, 0), (3, 3, 3), (3, 4, 3),              # moving up / dropping next to itself
])
def test_drop_target(source, gap, expected):
    assert drop_target(source, gap) == expected
    assert move(list("abcd"), source, drop_target(source, gap))  # always a valid move
