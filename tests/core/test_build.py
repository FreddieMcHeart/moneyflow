import pytest

from moneyflow.core.build import STAGES, build
from moneyflow.core.model import BuildRefused
from moneyflow.core.stages.arithmetic import arithmetic
from moneyflow.core.stages.categorize import categorize
from moneyflow.core.stages.exclude import exclude
from moneyflow.core.stages.ingest import ingest
from tests.core._builders import inputs, ruling, tx


def test_ruling_on_unknown_transaction_refuses():
    with pytest.raises(BuildRefused) as e:
        build(inputs(transactions=(tx(),), rulings=(ruling("g1", "ghost", "food"),)))
    assert e.value.transaction_id == "ghost"


def test_build_is_deterministic():
    inp = inputs(transactions=(tx("b", day=2), tx("a", day=2), tx("c", day=1)))
    first = build(inp)
    assert first == build(inp)
    assert isinstance(first, tuple)
    assert [e.transaction_id for e in first] == ["c", "a", "b"]


def test_stage_order():
    assert STAGES == [ingest, exclude, categorize, arithmetic]
