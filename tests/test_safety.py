import pytest

from conftest import require_test_database


def test_the_tests_refuse_to_empty_a_database_not_named_for_tests():
    with pytest.raises(RuntimeError, match="_test"):
        require_test_database("postgresql://catalog:catalog@localhost:55433/catalog")
    assert require_test_database("postgresql://catalog:catalog@localhost:55433/catalog_test").endswith("catalog_test")
