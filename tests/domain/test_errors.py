from lore.domain import AuthenticationError


def test_authentication_error_has_message() -> None:
    err = AuthenticationError("missing sub")
    assert str(err) == "missing sub"
