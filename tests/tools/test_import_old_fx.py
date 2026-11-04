"""`read_fx_rates` keeps only live rows and refuses an ambiguous day (the old `read_observations` semantics)."""
from datetime import date
from decimal import Decimal

import pytest

from moneyflow.core.fx import rate_for
from tools.import_old.read import read_fx_rates

_HEADER = "row_id,supersedes,date,rate,kind,method,source_ids,observed_at,note\n"


def _csv(tmp_path, *rows):
    path = tmp_path / "usd_rate.csv"
    path.write_text(_HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return path


def test_superseded_row_is_dropped_and_rate_for_gives_the_live_rate(tmp_path):
    path = _csv(
        tmp_path,
        "bbbbbbbbbbbb,,2026-10-01,40.0000,sell,test,,,",
        "aaaaaaaaaaaa,bbbbbbbbbbbb,2026-10-01,41.0000,sell,test,,,",
    )
    rates = read_fx_rates(path)
    assert [r.id for r in rates] == ["aaaaaaaaaaaa"]
    assert rate_for(date(2026, 10, 1), rates) == (Decimal("41.0000"), "sell")
    assert rate_for(date(2026, 10, 1), tuple(sorted(rates, key=lambda r: r.id))) == (Decimal("41.0000"), "sell")


def test_two_live_sell_rows_on_one_day_refuse(tmp_path):
    path = _csv(
        tmp_path,
        "bbbbbbbbbbbb,,2026-10-01,40.0000,sell,test,,,",
        "aaaaaaaaaaaa,,2026-10-01,41.0000,sell,test,,,",
    )
    with pytest.raises(ValueError, match="more than one live sell rate for 2026-10-01"):
        read_fx_rates(path)


def test_two_live_effective_rows_on_one_day_are_kept(tmp_path):
    path = _csv(
        tmp_path,
        "bbbbbbbbbbbb,,2026-10-01,40.0000,effective,test,,,",
        "aaaaaaaaaaaa,,2026-10-01,41.0000,effective,test,,,",
    )
    assert len(read_fx_rates(path)) == 2
