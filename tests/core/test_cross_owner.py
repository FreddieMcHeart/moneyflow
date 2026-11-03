"""A6 cross-owner detection, A7 blessing by ruling, A8 white-card legs. Synthetic data only."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from moneyflow.core.build import build
from moneyflow.core.cross_owner import coverage, cross_owner
from moneyflow.core.model import Account, BuildRefused, Owner, Transaction
from moneyflow.core.stages.exclude import _exclude_rulings
from moneyflow.core.stages.ingest import ingest
from tests.core._builders import exclude_ruling, inputs, ruling, white_card_ruling

OWNERS = (
    Owner(id="owner_a", display="n", counterparty_strings=("Alpha Test", "Owner A Test"), budget=True),
    Owner(id="owner_b", display="k", counterparty_strings=("Owner B Test", "From: Owner B Test"), budget=False),
)


def acct(id: str, owner: str, currency: str = "UAH", include: bool = True) -> Account:
    return Account(id=id, name=id, currency=currency, owner=owner, role="main", include=include, cash_from=None)


ACCOUNTS = (acct("his", "owner_a"), acct("hers", "owner_b"))
OUT = "Alpha Test"  # an expense on her card naming him
BACK = "From: Owner B Test"  # the income on his card naming her


def row(id: str, account: str, description: str, amount: str = "100.00", direction: str = "expense", day: int = 15, currency: str = "UAH") -> Transaction:
    return Transaction(
        id=id,
        account_id=account,
        date=date(2026, 1, day),
        time=datetime(2026, 1, day, 12, 0),
        amount=Decimal(amount),
        currency=currency,
        direction=direction,  # type: ignore[arg-type]
        mcc=None,
        description=description,
        comment="",
    )


def filler(account: str, first: int = 1, last: int = 28) -> tuple[Transaction, ...]:
    return (row(f"f1-{account}", account, "filler", day=first), row(f"f2-{account}", account, "filler", day=last))


def world(*txs: Transaction, rulings=(), accounts=ACCOUNTS, owners=OWNERS, windows=None):
    spans = windows or {"his": (1, 28), "hers": (1, 28)}
    fill = tuple(t for a, (first, last) in spans.items() for t in filler(a, first, last))
    return inputs(transactions=fill + txs, rulings=rulings, accounts=accounts, owners=owners)


def run(inp):
    return cross_owner(_exclude_rulings(ingest([], inp), inp), inp, coverage(inp.transactions))


def white(id: str):
    return (white_card_ruling(f"white_card:{id}", id), ruling(f"override:{id}", id, "food"))


PAIR = {"out": "cross-owner-transfer", "back": "cross-owner-transfer"}


def pair(amount: str = "500.00", day: int = 15):
    return (row("out", "hers", OUT, amount, day=day), row("back", "his", BACK, amount, "income", day=day))


def test_coverage_is_first_and_last_observed_date_per_account():
    txs = (row("a", "his", "x", day=5), row("b", "his", "x", day=20), row("c", "hers", "x", day=9))
    assert coverage(txs) == {"his": (date(2026, 1, 5), date(2026, 1, 20)), "hers": (date(2026, 1, 9), date(2026, 1, 9))}


def test_an_expense_naming_the_other_owner_inside_coverage_is_excluded_with_its_partner():
    assert run(world(*pair())) == PAIR


def test_detection_is_case_insensitive():
    out = row("out", "his", "owner b test")
    back = row("back", "hers", "from: owner a test", direction="income")
    assert run(world(out, back)) == PAIR


def test_the_income_leg_is_never_marked():
    assert run(world(row("back", "his", BACK, direction="income"))) == {}


def test_a_leg_naming_its_own_owner_is_not_a_cross_owner_transfer():
    assert run(world(row("x", "hers", "Owner B Test"))) == {}


def test_a_leg_outside_the_counterpartys_coverage_is_left_alone():
    inp = world(row("out", "hers", OUT, day=15), windows={"his": (20, 28), "hers": (1, 28)})
    assert run(inp) == {}


def test_coverage_is_a_union_so_one_empty_account_does_not_disable_detection():
    accounts = ACCOUNTS + (acct("his-empty", "owner_a"),)
    assert run(world(*pair(), accounts=accounts)) == PAIR


def test_a_description_naming_nobody_is_not_detected():
    assert run(world(row("x", "hers", "Card transfer"))) == {}


def test_a_row_matching_two_owners_is_counted_once():
    third = Owner(id="third", display="t", counterparty_strings=("Alpha Test",), budget=False)
    inp = world(*pair(), accounts=ACCOUNTS + (acct("third-acc", "third"),), owners=OWNERS + (third,), windows={"his": (1, 28), "hers": (1, 28), "third-acc": (1, 28)})
    assert run(inp) == PAIR


def test_two_legs_on_one_key_match_two_partners_and_all_four_are_excluded():
    txs = (
        row("o1", "hers", OUT, "250.00"),
        row("o2", "hers", "Owner A Test", "250.00"),
        row("b1", "his", BACK, "250.00", "income"),
        row("b2", "his", "Owner B Test", "250.00", "income"),
    )
    assert set(run(world(*txs))) == {"o1", "o2", "b1", "b2"}


def test_a_detected_leg_with_no_partner_refuses_and_names_the_row():
    with pytest.raises(BuildRefused) as refused:
        run(world(row("out", "hers", OUT, "1200.00")))
    assert refused.value.transaction_id == "out"
    assert "1200.00" in str(refused.value) and "out" in str(refused.value)


def test_a_blessed_unpaired_id_is_excluded_by_its_ruling_and_not_counted_as_unpaired():
    inp = world(row("out", "hers", OUT, "1200.00"), rulings=(exclude_ruling("exclude:cross-owner-unpaired:out", "out", "cross-owner-unpaired"),))
    assert run(inp) == {}
    (e,) = [e for e in build(inp) if e.transaction_id == "out"]
    assert (e.status, e.reason, e.decided_by) == ("excluded", "cross-owner-unpaired", "ruling:exclude:cross-owner-unpaired:out")


def test_more_candidates_than_legs_refuses():
    txs = (row("out", "hers", OUT, "200.00"), row("b1", "his", BACK, "200.00", "income"), row("b2", "his", "Owner B Test", "200.00", "income"))
    with pytest.raises(BuildRefused):
        run(world(*txs))


def test_an_income_row_that_does_not_name_the_payer_is_not_a_candidate():
    out, back = pair("200.00")
    noise = row("noise", "his", "Salary", "200.00", "income")
    assert run(world(out, back, noise)) == PAIR


def test_an_unrelated_same_amount_pair_on_the_same_day_is_left_alone():
    out, back = pair("250.00", 29 - 1)
    move = row("move", "his", "Card transfer", "250.00", day=28)
    own_in = row("own", "his", "From white card", "250.00", "income", day=28)
    assert run(world(out, move, back, own_in)) == PAIR


def test_two_genuinely_separate_payments_on_one_key_both_pair():
    txs = (
        row("o1", "hers", OUT, "500.00"),
        row("o2", "hers", "Alpha Test again", "500.00"),
        row("b1", "his", BACK, "500.00", "income"),
        row("b2", "his", "Owner B Test pay", "500.00", "income"),
    )
    assert len(run(world(*txs))) == 4


def test_a_currency_difference_is_not_a_match():
    accounts = ACCOUNTS + (acct("his-usd", "owner_a", currency="USD"),)
    txs = (row("out", "hers", OUT, "500.00"), row("back", "his-usd", BACK, "500.00", "income", currency="USD"))
    with pytest.raises(BuildRefused):
        run(world(*txs, accounts=accounts))


def test_a_candidate_on_a_non_included_account_is_not_counted():
    accounts = ACCOUNTS + (acct("his-other", "owner_a", include=False),)
    txs = (row("out", "hers", OUT, "500.00"), row("back", "his-other", BACK, "500.00", "income"))
    with pytest.raises(BuildRefused):
        run(world(*txs, accounts=accounts))


def test_the_stage_excludes_both_legs_and_names_the_mechanism():
    ledger = {e.transaction_id: e for e in build(world(*pair()))}
    for id in ("out", "back"):
        assert (ledger[id].status, ledger[id].reason, ledger[id].decided_by) == ("excluded", "cross-owner-transfer", "core:cross-owner-transfer")
    assert ledger["f1-his"].status == "unresolved"


def white_leg(id: str, note: str, amount: str = "4500.00") -> Transaction:
    return row(id, "his", f"Owner B Test {note}", amount)


def her_income(amount: str = "4500.00") -> Transaction:
    return row("partner", "hers", "From: Owner A Test", amount, "income")


def test_a_listed_leg_with_no_partner_is_kept_not_refused():
    inp = world(white_leg("w", "coffee"), rulings=white("w"))
    assert run(inp) == {}
    (e,) = [e for e in build(inp) if e.transaction_id == "w"]
    assert (e.status, e.category, e.decided_by) == ("kept", "food", "ruling:override:w")


def test_the_same_leg_unlisted_still_refuses():
    with pytest.raises(BuildRefused):
        run(world(white_leg("w", "coffee")))


def test_a_listed_leg_whose_key_has_an_income_partner_refuses():
    with pytest.raises(BuildRefused) as refused:
        run(world(white_leg("w", "coffee"), her_income(), rulings=white("w")))
    message = str(refused.value)
    assert "w" in message and "partner" in message and "4500.00" in message and "white-card" in message


def test_on_a_mixed_key_the_unlisted_leg_pairs_and_the_listed_one_is_kept():
    inp = world(white_leg("w", "coffee"), white_leg("u", "food"), her_income(), rulings=white("w"))
    assert run(inp) == {"u": "cross-owner-transfer", "partner": "cross-owner-transfer"}


def test_two_listed_legs_one_unlisted_and_one_partner():
    inp = world(white_leg("a", "x"), white_leg("b", "y"), white_leg("u", "z"), her_income(), rulings=white("a") + white("b"))
    assert run(inp) == {"u": "cross-owner-transfer", "partner": "cross-owner-transfer"}


def test_a_listed_id_that_is_not_a_detected_leg_refuses():
    with pytest.raises(BuildRefused, match="not a detected cross-owner expense leg") as refused:
        run(world(row("plain", "his", "shop"), rulings=white("plain")))
    assert refused.value.transaction_id == "plain"


def test_a_listed_leg_outside_her_coverage_window_refuses_and_points_to_a_category_ruling():
    inp = world(white_leg("w", "coffee"), rulings=white("w"), windows={"his": (1, 28), "hers": (1, 10)})
    with pytest.raises(BuildRefused, match="category ruling") as refused:
        run(inp)
    assert refused.value.transaction_id == "w"


def test_an_id_in_both_listings_refuses():
    rulings = white("w") + (exclude_ruling("exclude:cross-owner-unpaired:w", "w", "cross-owner-unpaired"),)
    with pytest.raises(BuildRefused, match="both a white-card leg and excluded by a ruling") as refused:
        run(world(white_leg("w", "coffee"), rulings=rulings))
    assert refused.value.transaction_id == "w"

