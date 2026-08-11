import base64

from cryptography.hazmat.primitives.asymmetric import ed25519

from atlas.streams.polymarket_us import PolymarketUSMarketStream


def test_polymarket_auth_headers_have_expected_shape():
    private_key = ed25519.Ed25519PrivateKey.generate()
    raw = private_key.private_bytes_raw()
    headers = PolymarketUSMarketStream.auth_headers("key-id", base64.b64encode(raw).decode())
    assert set(headers) == {"X-PM-Access-Key", "X-PM-Timestamp", "X-PM-Signature"}
    assert headers["X-PM-Access-Key"] == "key-id"
    assert len(base64.b64decode(headers["X-PM-Signature"])) == 64
