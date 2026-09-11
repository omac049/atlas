"""Loop 1: Search Console pulls. No network: Google is an httpx.MockTransport."""

import base64
import json
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from atlas import gsc

EMAIL = "reader@atlas-gsc.iam.gserviceaccount.com"


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _key(rsa_key) -> dict:
    return {"client_email": EMAIL, "private_key": rsa_key, "token_uri": gsc.GOOGLE_OAUTH_URL}


def test_signed_assertion_verifies_and_asks_only_for_read_access(rsa_key):
    token = gsc.signed_assertion(EMAIL, rsa_key, gsc.GOOGLE_OAUTH_URL, now=1_800_000_000)
    header, body, signature = token.split(".")
    rsa_key.public_key().verify(
        _unb64(signature), f"{header}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    assert json.loads(_unb64(header)) == {"alg": "RS256", "typ": "JWT"}
    assert json.loads(_unb64(body)) == {
        "iss": EMAIL, "scope": "https://www.googleapis.com/auth/webmasters.readonly",
        "aud": gsc.GOOGLE_OAUTH_URL, "iat": 1_800_000_000, "exp": 1_800_003_600,
    }


def test_pull_pages_through_rows_and_writes_one_file_per_day(tmp_path, rsa_key, monkeypatch):
    monkeypatch.setattr(gsc, "ROW_LIMIT", 2)
    totals_rows = [
        {"keys": ["2026-09-10"], "clicks": 1, "impressions": 20, "ctr": 0.05, "position": 14.0},
        {"keys": ["2026-09-11"], "clicks": 0, "impressions": 5, "ctr": 0.0, "position": 9.0},
    ]
    detail_rows = [
        {"keys": ["2026-09-10", "https://verifiedfees.com/ebay", "ebay fee calculator"],
         "clicks": 1, "impressions": 16, "ctr": 0.06, "position": 12.0},
        {"keys": ["2026-09-10", "https://verifiedfees.com/paypal", "paypal fees"],
         "clicks": 0, "impressions": 4, "ctr": 0.0, "position": 25.0},
        {"keys": ["2026-09-11", "https://verifiedfees.com/ebay", "ebay fees"],
         "clicks": 0, "impressions": 5, "ctr": 0.0, "position": 9.0},
    ]
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == [gsc.JWT_BEARER] and form["assertion"]
            return httpx.Response(200, json={"access_token": "t-123", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer t-123"
        assert request.url.path.endswith("/sites/sc-domain:verifiedfees.com/searchAnalytics/query")
        body = json.loads(request.content)
        assert body["startDate"] == "2026-09-07" and body["endDate"] == "2026-09-11"
        assert body["dataState"] == "all"
        seen.append((tuple(body["dimensions"]), body["startRow"]))
        rows = totals_rows if body["dimensions"] == ["date"] else detail_rows
        return httpx.Response(200, json={"rows": rows[body["startRow"]:body["startRow"] + 2]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        summary = gsc.pull(client, _key(rsa_key), ["sc-domain:verifiedfees.com"], days=5,
                           today=date(2026, 9, 12), out_dir=tmp_path)
    assert seen == [(("date",), 0), (("date",), 2), (("date", "page", "query"), 0),
                    (("date", "page", "query"), 2)]
    assert summary == {"sc-domain:verifiedfees.com": {
        "days_with_data": 2, "rows": 3, "window": "2026-09-07..2026-09-11"}}
    first = json.loads((tmp_path / "verifiedfees.com" / "2026-09-10.json").read_text())
    assert first["totals"]["impressions"] == 20 and len(first["rows"]) == 2
    assert first["rows"][0] == {"page": "https://verifiedfees.com/ebay", "query": "ebay fee calculator",
                                "clicks": 1, "impressions": 16, "ctr": 0.06, "position": 12.0}
    second = json.loads((tmp_path / "verifiedfees.com" / "2026-09-11.json").read_text())
    assert second["totals"]["impressions"] == 5 and len(second["rows"]) == 1


def test_a_property_without_the_service_account_names_the_fix(tmp_path, rsa_key):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(403, json={"error": {"message": "User does not have permission"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(
        RuntimeError, match="Add the service account"
    ):
        gsc.pull(client, _key(rsa_key), ["sc-domain:samebetornot.com"], days=3,
                 today=date(2026, 9, 12), out_dir=tmp_path)


def test_missing_key_is_a_clean_skip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_GSC_KEY_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setenv("ATLAS_GSC_DATA_DIR", str(tmp_path / "data"))
    assert gsc.main(["pull"]) == 0
    assert capsys.readouterr().out.startswith("skipped: not configured")


def test_key_errors_name_the_path_never_the_contents(tmp_path):
    bad = tmp_path / "key.json"
    bad.write_text(json.dumps({
        "type": "service_account", "client_email": EMAIL,
        "private_key": "-----BEGIN PRIVATE KEY-----\nSENTINEL-NOT-A-KEY\n-----END PRIVATE KEY-----\n",
    }))
    with pytest.raises(gsc.NotConfigured) as err:
        gsc.load_key(bad)
    assert str(bad) in str(err.value)
    assert "SENTINEL" not in str(err.value) and err.value.__suppress_context__


def test_status_shows_presence_and_mode_but_never_contents(tmp_path, monkeypatch, capsys, rsa_key):
    pem = rsa_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"type": "service_account", "client_email": EMAIL, "private_key": pem}))
    key.chmod(0o644)
    monkeypatch.setenv("ATLAS_GSC_KEY_FILE", str(key))
    monkeypatch.setenv("ATLAS_GSC_DATA_DIR", str(tmp_path / "data"))
    assert gsc.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "present" in out and "chmod 600" in out and "no data yet" in out
    assert "PRIVATE KEY" not in out and EMAIL not in out
    assert gsc.load_key(key)["client_email"] == EMAIL


def _day(site: str, day: str, totals: dict, rows: list[dict]) -> dict:
    return {"site": site, "date": day, "totals": totals, "rows": rows}


def test_report_weights_positions_and_computes_the_pass_line(tmp_path):
    site = "sc-domain:verifiedfees.com"
    folder = tmp_path / "verifiedfees.com"
    folder.mkdir()
    days = [
        _day(site, "2026-09-10", {"clicks": 1, "impressions": 100, "ctr": 0.01, "position": 20.0}, [
            {"page": "https://verifiedfees.com/ebay", "query": "ebay fee calculator",
             "clicks": 1, "impressions": 60, "ctr": 0.02, "position": 15.0},
            {"page": "https://verifiedfees.com/paypal", "query": "how to send money",
             "clicks": 0, "impressions": 40, "ctr": 0.0, "position": 28.0},
        ]),
        _day(site, "2026-09-11", {"clicks": 3, "impressions": 300, "ctr": 0.01, "position": 10.0}, [
            {"page": "https://verifiedfees.com/ebay", "query": "ebay fee calculator",
             "clicks": 3, "impressions": 140, "ctr": 0.02, "position": 9.0},
        ]),
    ]
    for d in days:
        (folder / f"{d['date']}.json").write_text(json.dumps(d))
    r = gsc.site_report(site, gsc.load_days(folder), date(2026, 9, 14), ["ebay", "paypal"])
    assert r["data_through"] == "2026-09-11"
    assert r["last_7_days"] == {"clicks": 4, "impressions": 400, "ctr": 0.01, "position": 12.5}
    ebay = r["top_queries"][0]
    assert ebay == {"query": "ebay fee calculator", "clicks": 4, "impressions": 200,
                    "position": 10.8, "page": "https://verifiedfees.com/ebay"}
    assert [q["query"] for q in r["striking_distance"]] == ["ebay fee calculator", "how to send money"]
    line = r["pass_line"]
    assert line["impressions_30d"] == 400 and line["impressions_target"] == 2000
    assert line["best_term"]["query"] == "ebay fee calculator" and line["in_top_n"] is True
    assert line["week"] == 1
    assert line["scoreboard_row"].startswith("| 1 | 2026-09-14 | 400 | 4 | 12.5 | /ebay (10.8) |")
    md = gsc.render_markdown([r], date(2026, 9, 14))
    assert "400 of 2,000 impressions" in md and "Top 20: yes" in md


def test_samebetornot_terms_are_kalshi_or_polymarket_queries():
    match = gsc.term_matcher("sc-domain:samebetornot.com", [])
    assert match("kalshi senate control 2026") and match("polymarket house 2026")
    assert not match("september 2026 fomc meeting date")
    assert gsc.term_matcher("sc-domain:unknown.example", []) is None


def test_fee_terms_come_from_the_published_schedules():
    names = gsc.fee_platform_names()
    assert {"ebay", "tiktok shop", "tiktokshop", "quickbooks", "cash app"} <= set(names)
    match = gsc.term_matcher("sc-domain:verifiedfees.com", names)
    assert match("tiktok shop fee calculator") and match("quickbooks payments fees")
    assert not match("ebay coupons")
