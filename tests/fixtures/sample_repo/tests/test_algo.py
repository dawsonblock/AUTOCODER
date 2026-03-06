from src.algo import binary_search


def test_finds_first_value() -> None:
    assert binary_search([1, 3, 5, 7, 9], 1) == 0


def test_finds_last_value() -> None:
    assert binary_search([1, 3, 5, 7, 9], 9) == 4


def test_missing_value_returns_negative_one() -> None:
    assert binary_search([1, 3, 5, 7, 9], 4) == -1

