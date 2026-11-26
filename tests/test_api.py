import io
import json

import pytest

from ytpl import api


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_github_oidc_token_and_gas(monkeypatch):
    seen = []

    def urlopen(req, timeout=0):
        seen.append(req)
        if req.full_url.startswith("https://gh.example"):
            assert req.headers["Authorization"] == "Bearer req-token"
            return Resp(json.dumps({"value": "oidc.jwt.sig"}).encode())
        assert json.loads(req.data) == {"id_token": "oidc.jwt.sig"}     # 合言葉は送らない
        return Resp(json.dumps({"access_token": "ya29.x"}).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://gh.example/token?api-version=2.0")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "req-token")
    tok = api.github_oidc_token("yt-music-playlist")
    assert seen[0].full_url == "https://gh.example/token?api-version=2.0&audience=yt-music-playlist"
    assert api.fetch_gas_token("https://script.example/exec", tok) == "ya29.x"


def test_oidc_outside_actions(monkeypatch):
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GitHub Actions の中でだけ"):
        api.github_oidc_token("yt-music-playlist")


def test_gas_forbidden(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=0: Resp(json.dumps({"error": "forbidden: workflow"}).encode()))
    with pytest.raises(RuntimeError, match="forbidden: workflow"):
        api.fetch_gas_token("https://script.example/exec", "t")
