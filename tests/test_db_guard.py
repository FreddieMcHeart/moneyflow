import pytest

from tests.conftest import require_test_db


def test_refuses_a_database_without_test_in_its_name():
    with pytest.raises(pytest.fail.Exception, match="moneyflow_dev"):
        require_test_db("postgresql://postgres:mf@localhost:5433/moneyflow_dev")


def test_accepts_a_test_database():
    require_test_db("postgresql://postgres:mf@localhost:5433/moneyflow_test")
