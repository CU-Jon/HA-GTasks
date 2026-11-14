"""Check the Android identity, failure classification and local secret handling."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from custom_components.gtasks.exceptions import NativeAuthError, TasksError
from custom_components.gtasks.native import exchange_token, obtain_master_token
from tools import get_master_token


def test_scoped_exchange_uses_verified_identity():
    session = MagicMock()
    response = session.post.return_value
    response.status_code = 200
    response.text = "Auth=scoped-access\nExpiry=9999999999\n"
    with patch("requests.Session") as factory:
        factory.return_value.__enter__.return_value = session
        token, lifetime = exchange_token("person@example.com", "fake-master", "0123456789abcdef")
    assert token == "scoped-access"
    assert lifetime == 1800
    options = session.post.call_args.kwargs
    assert options["allow_redirects"] is False
    assert options["timeout"] == 20
    assert options["data"]["app"] == "com.google.android.apps.tasks"
    assert options["data"]["client_sig"] == "5af12649728293f2d9f5e8c61e455a8fea16e7d8"
    assert options["data"]["sdk_version"] == 24
    assert options["data"]["service"] == "oauth2:https://www.googleapis.com/auth/tasks"


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (200, "Token=generated-test-master\n", None),
        (200, "Error=BadAuthentication\n", NativeAuthError),
        (400, "sensitive-provider-body", NativeAuthError),
        (503, "sensitive-provider-body", TasksError),
    ],
)
def test_browser_token_exchange_transport(status, body, expected):
    session = MagicMock()
    session.post.return_value.status_code = status
    session.post.return_value.text = body
    with patch("requests.Session") as factory:
        factory.return_value.__enter__.return_value = session
        if expected:
            with pytest.raises(expected) as error:
                obtain_master_token(
                    "person@example.com", "temporary-test-cookie", "0123456789abcdef"
                )
            assert body not in str(error.value)
        else:
            assert (
                obtain_master_token(
                    "person@example.com", "temporary-test-cookie", "0123456789abcdef"
                )
                == "generated-test-master"
            )
    session.post.assert_called_once()
    options = session.post.call_args.kwargs
    assert options["allow_redirects"] is False
    assert options["timeout"] == 20
    assert "temporary-test-cookie" in options["data"].values()


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, NativeAuthError),
        (401, NativeAuthError),
        (403, NativeAuthError),
        (429, TasksError),
        (500, TasksError),
    ],
)
def test_exchange_errors_safe(status, expected):
    session = MagicMock()
    session.post.return_value.status_code = status
    session.post.return_value.text = "sensitive-response-fake-master"
    with patch("requests.Session") as factory:
        factory.return_value.__enter__.return_value = session
        with pytest.raises(expected) as error:
            exchange_token("person@example.com", "fake-master", "0123456789abcdef")
    assert "sensitive" not in str(error.value)
    assert "fake-master" not in str(error.value)
    if expected is TasksError:
        assert not isinstance(error.value, NativeAuthError)


def test_exchange_network_failure_is_recoverable():
    with patch("requests.Session") as factory:
        factory.return_value.__enter__.return_value.post.side_effect = requests.ConnectionError(
            "sensitive"
        )
        with pytest.raises(TasksError) as error:
            exchange_token("person@example.com", "fake-master", "0123456789abcdef")
    assert not isinstance(error.value, NativeAuthError)
    assert "sensitive" not in str(error.value)


def test_browser_exchange_network_failure_is_recoverable():
    with patch("requests.Session") as factory:
        factory.return_value.__enter__.return_value.post.side_effect = requests.ConnectionError(
            "sensitive-cookie"
        )
        with pytest.raises(TasksError) as error:
            obtain_master_token("person@example.com", "temporary-test-cookie", "0123456789abcdef")
    assert not isinstance(error.value, NativeAuthError)
    assert "sensitive" not in str(error.value)


def test_helper_writes_private_file_without_printing_token(tmp_path, monkeypatch, capsys):
    output = tmp_path / "private/credentials.json"
    monkeypatch.setattr("sys.argv", ["get_master_token.py", "--output", str(output)])
    monkeypatch.setattr("builtins.input", lambda _: "person@example.com")
    monkeypatch.setattr("getpass.getpass", lambda _: "fake-cookie")
    session = MagicMock()
    session.post.return_value.text = "Token=fake-master-secret\n"
    with patch("requests.Session") as factory:
        factory.return_value.__enter__.return_value = session
        assert get_master_token.main() == 0
    assert json.loads(output.read_text())["master_token"] == "fake-master-secret"
    assert output.stat().st_mode & 0o777 == 0o600
    stdout, stderr = capsys.readouterr()
    assert "fake-master-secret" not in stdout + stderr
    assert "fake-cookie" not in stdout + stderr


def test_helper_never_overwrites(tmp_path, monkeypatch):
    output = tmp_path / "credentials.json"
    output.write_text("existing")
    monkeypatch.setattr("sys.argv", ["get_master_token.py", "--output", str(output)])
    with pytest.raises(SystemExit):
        get_master_token.main()
    assert output.read_text() == "existing"
