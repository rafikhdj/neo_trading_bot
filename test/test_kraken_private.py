"""Tests hors réseau, sans lecture du .env du projet et sans credentials réels."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import traceback
from urllib.parse import parse_qs, quote_plus

import pytest
import requests

from data import kraken_private as kp

KEY = "unit-test-api-key"
SECRET = base64.b64encode(b"unit-test-secret-not-real").decode()


class Response:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Transport:
    def __init__(self):
        self.responses = []
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        assert self.responses, "Appel inattendu : aucun réseau réel n'est autorisé dans ces tests."
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    monkeypatch.setattr(kp, "_ENV_PATH", tmp_path / "absent.env")
    monkeypatch.setattr(kp, "_last_nonce", 0)


@pytest.fixture
def transport(monkeypatch):
    transport = Transport()
    monkeypatch.setattr(kp.requests, "Session", lambda: transport)
    return transport


def ok(result):
    return Response({"error": [], "result": result})


def test_official_signature_vector_without_any_network():
    # Exemple public de la documentation Kraken ; la fonction ne fait aucun appel HTTP.
    public_example = "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="
    data = {"nonce": "1616492376594", "ordertype": "limit", "pair": "XBTUSD",
            "price": 37500, "type": "buy", "volume": 1.25}
    expected = "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="
    assert kp.sign_request("/0/private/AddOrder", data, public_example) == expected


def test_nonces_survive_same_tick_and_clock_rollback(monkeypatch):
    times = iter([5000000, 5000000, 3000000])
    monkeypatch.setattr(kp.time, "time_ns", lambda: next(times))
    assert [kp.next_nonce() for _ in range(3)] == ["5", "6", "7"]


def test_nonces_are_unique_across_threads(monkeypatch):
    monkeypatch.setattr(kp.time, "time_ns", lambda: 1000000)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: int(kp.next_nonce()), range(200)))
    assert len(set(values)) == 200
    assert sorted(values) == list(range(1, 201))


def test_balance_signs_exact_form_body_and_disables_redirects(transport, monkeypatch):
    monkeypatch.setattr(kp, "next_nonce", lambda: "123456")
    transport.responses = [ok({"ZEUR": "50.0000", "XXBT": "0.001"})]
    with kp.KrakenPrivateClient(KEY, SECRET) as client:
        assert client.balance() == {"ZEUR": "50.0000", "XXBT": "0.001"}
    url, call = transport.calls[0]
    assert url == "https://api.kraken.com/0/private/Balance"
    assert call["data"] == "nonce=123456"
    assert call["headers"]["API-Key"] == KEY
    assert call["headers"]["API-Sign"] == kp.sign_request("/0/private/Balance", {"nonce": "123456"}, SECRET)
    assert call["timeout"] == 15 and call["allow_redirects"] is False
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert SECRET not in call["data"]
    assert transport.closed


def test_reads_open_orders(transport):
    orders = {"ORDER-1": {"status": "open", "vol": "0.1"}}
    transport.responses = [ok({"open": orders})]
    client = kp.KrakenPrivateClient(KEY, SECRET)
    assert client.open_orders() == orders
    assert parse_qs(transport.calls[0][1]["data"])["trades"] == ["false"]


def test_closed_orders_are_sorted_limited_and_keep_canceled_orders(transport):
    transport.responses = [ok({"closed": {"old": {"closetm": 10, "status": "closed"},
                                            "recent": {"closetm": 30, "status": "canceled"},
                                            "middle": {"closetm": 20, "status": "closed"}}, "count": 100})]
    client = kp.KrakenPrivateClient(KEY, SECRET)
    assert list(client.closed_orders(limit=2)) == ["recent", "middle"]
    assert parse_qs(transport.calls[0][1]["data"])["closetime"] == ["close"]


@pytest.mark.parametrize("limit", [0, 51, -1, True, 2.5, "5"])
def test_invalid_limits_never_call_network(transport, limit):
    with pytest.raises(kp.KrakenPrivateError):
        kp.KrakenPrivateClient(KEY, SECRET).closed_orders(limit)
    assert not transport.calls


@pytest.mark.parametrize("endpoint", ["AddOrder", "EditOrder", "CancelOrder", "Withdraw", "Transfer", "../Balance", "https://example.com"])
def test_no_endpoint_outside_read_allowlist(transport, endpoint):
    with pytest.raises(kp.KrakenPrivateError, match="Endpoint interdit"):
        kp.KrakenPrivateClient(KEY, SECRET)._request(endpoint)
    assert transport.calls == []


@pytest.mark.parametrize("key,secret", [("", SECRET), (KEY, ""), (None, SECRET), (KEY, None),
                                        ("key\nsecret", SECRET), (KEY, "not-base64!"), (KEY, "é")])
def test_missing_or_invalid_credentials_are_safe(transport, key, secret):
    with pytest.raises(kp.KrakenCredentialsError) as error:
        kp.KrakenPrivateClient(key, secret)
    assert KEY not in str(error.value) and SECRET not in str(error.value)
    assert transport.calls == []


def test_env_file_loading_quotes_equals_and_environment_precedence(tmp_path, monkeypatch, transport):
    path = tmp_path / ".env"
    path.write_text(f'# comment\nexport KRAKEN_API_KEY="file-key"\nKRAKEN_API_SECRET=\'{SECRET}\'\nUNUSED=x\n')
    monkeypatch.setenv("KRAKEN_API_KEY", KEY)
    transport.responses = [ok({})]
    client = kp.KrakenPrivateClient.from_env(path)
    client.balance()
    assert transport.calls[0][1]["headers"]["API-Key"] == KEY
    assert "KRAKEN_API_SECRET" not in __import__('os').environ


def test_missing_credentials_fail_before_transport_creation(monkeypatch):
    def forbidden():
        pytest.fail("Aucun transport sans credentials")
    monkeypatch.setattr(kp.requests, "Session", forbidden)
    with pytest.raises(kp.KrakenCredentialsError):
        kp.KrakenPrivateClient.from_env()


@pytest.mark.parametrize("status,kind", [(401, kp.KrakenAuthenticationError), (403, kp.KrakenAuthenticationError),
                                       (500, kp.KrakenHTTPError), (302, kp.KrakenHTTPError)])
def test_http_errors_do_not_echo_body(transport, status, kind):
    transport.responses = [Response({"echo": KEY + SECRET}, status)]
    with pytest.raises(kind) as error:
        kp.KrakenPrivateClient(KEY, SECRET).balance()
    assert KEY not in str(error.value) and SECRET not in str(error.value)
    assert len(transport.calls) == 1


def test_http_rate_limit_exposes_only_numeric_delay_without_retry(transport):
    transport.responses = [Response(status=429, headers={"Retry-After": "12"})]
    with pytest.raises(kp.KrakenRateLimitError) as error:
        kp.KrakenPrivateClient(KEY, SECRET).balance()
    assert error.value.retry_after == 12
    assert len(transport.calls) == 1


@pytest.mark.parametrize("code,kind", [
    ("EAPI:Invalid key", kp.KrakenAuthenticationError),
    ("EAPI:Invalid signature", kp.KrakenAuthenticationError),
    ("EAPI:Invalid nonce", kp.KrakenAuthenticationError),
    ("EGeneral:Permission denied", kp.KrakenAuthenticationError),
    ("EAPI:Rate limit exceeded", kp.KrakenRateLimitError),
    ("EService:Throttled:123", kp.KrakenRateLimitError),
    ("EGeneral:Temporary lockout", kp.KrakenAuthenticationError),
    ("EService:Unavailable", kp.KrakenPrivateError),
])
def test_kraken_errors_are_classified_and_redacted(transport, code, kind):
    transport.responses = [Response({"error": [code, KEY + SECRET]})]
    with pytest.raises(kind) as error:
        kp.KrakenPrivateClient(KEY, SECRET).balance()
    assert KEY not in str(error.value) and SECRET not in str(error.value)
    assert KEY not in "".join(traceback.format_exception(error.value))
    assert SECRET not in "".join(traceback.format_exception(error.value))
    assert len(transport.calls) == 1


@pytest.mark.parametrize("failure,kind", [
    (requests.Timeout(KEY + SECRET), kp.KrakenNetworkError),
    (requests.ConnectionError(KEY + SECRET), kp.KrakenNetworkError),
    (requests.exceptions.SSLError(KEY + SECRET), kp.KrakenNetworkError),
])
def test_network_errors_suppress_sensitive_exception_chains(transport, failure, kind):
    transport.responses = [failure]
    with pytest.raises(kind) as error:
        kp.KrakenPrivateClient(KEY, SECRET).balance()
    trace = "".join(traceback.format_exception(error.value))
    assert KEY not in trace and SECRET not in trace
    assert len(transport.calls) == 1


@pytest.mark.parametrize("payload", [ValueError(KEY + SECRET), [], {}, {"error": "bad"},
                                     {"error": [123]}, {"error": [], "result": []}, {"error": []}])
def test_malformed_responses_fail_safely(transport, payload):
    transport.responses = [Response(payload)]
    with pytest.raises(kp.KrakenPrivateError) as error:
        kp.KrakenPrivateClient(KEY, SECRET).balance()
    assert KEY not in str(error.value) and SECRET not in str(error.value)


@pytest.mark.parametrize("method,result", [("open_orders", {}), ("closed_orders", {"closed": []}),
                                          ("closed_orders", {"closed": {"id": {"closetm": SECRET}}})])
def test_order_response_shapes_are_checked(transport, method, result):
    transport.responses = [ok(result)]
    with pytest.raises(kp.KrakenPrivateError) as error:
        getattr(kp.KrakenPrivateClient(KEY, SECRET), method)()
    assert SECRET not in str(error.value)


def test_success_payloads_and_client_representation_do_not_expose_credentials(transport, caplog):
    transport.responses = [ok({KEY: [SECRET, quote_plus(SECRET)]})]
    client = kp.KrakenPrivateClient(KEY, SECRET)
    rendered = json.dumps(client.balance()) + repr(client) + caplog.text
    assert KEY not in rendered and SECRET not in rendered and quote_plus(SECRET) not in rendered
    assert "REDACTED" in rendered


def test_multiple_clients_serialize_requests_with_increasing_nonces(transport):
    transport.responses = [ok({}) for _ in range(20)]
    clients = [kp.KrakenPrivateClient(KEY, SECRET), kp.KrakenPrivateClient(KEY, SECRET)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: clients[i % 2].balance(), range(20)))
    sent = [int(parse_qs(call["data"])["nonce"][0]) for _, call in transport.calls]
    assert sent == sorted(set(sent))


def test_cli_only_calls_three_read_endpoints_and_redacts_output(monkeypatch, transport, capsys):
    monkeypatch.setenv("KRAKEN_API_KEY", KEY)
    monkeypatch.setenv("KRAKEN_API_SECRET", SECRET)
    transport.responses = [ok({"ZEUR": "50"}), ok({"open": {}}),
                           ok({"closed": {"id": {"closetm": 1, "descr": {"order": KEY + SECRET}}}})]
    assert kp.main(["--limit", "5"]) == 0
    output = capsys.readouterr()
    assert "Connexion authentifiée : OK" in output.out
    assert "Ordres ouverts (0)" in output.out and "Derniers ordres clôturés (1" in output.out
    assert KEY not in output.out + output.err and SECRET not in output.out + output.err
    assert [url.rsplit("/", 1)[-1] for url, _ in transport.calls] == ["Balance", "OpenOrders", "ClosedOrders"]
    assert transport.closed


def test_cli_reports_missing_credentials_without_network(transport, capsys):
    assert kp.main([]) == 1
    assert "KRAKEN_API_KEY" in capsys.readouterr().err
    assert not transport.calls


def test_cli_partial_permission_failure_is_not_reported_as_full_success(monkeypatch, transport, capsys):
    monkeypatch.setenv("KRAKEN_API_KEY", KEY)
    monkeypatch.setenv("KRAKEN_API_SECRET", SECRET)
    transport.responses = [ok({}), Response({"error": ["EGeneral:Permission denied", KEY + SECRET]})]
    assert kp.main([]) == 1
    output = capsys.readouterr()
    assert "Vérification incomplète" in output.err
    assert "Vérification des trois accès de lecture : OK" not in output.out
    assert KEY not in output.out + output.err and SECRET not in output.out + output.err
    assert len(transport.calls) == 2
