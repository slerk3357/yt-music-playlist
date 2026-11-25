"""追加の提案に対する決定（Worker の画面で選んだ「追加」「却下」）の取得と、反映結果の送信。

Worker（worker/）の Durable Object に保存された決定を、毎日の同期の最初に読み、
「追加」は再生リストに入れ、「却下」はその提案先に二度と提案しない。結果は Worker に返す（ack）。

Worker が使えないとき（設定していない・つながらない）は、手元の記録 data/decisions.yaml だけで動く。
手元の記録は git に残るので、Worker 側のデータが消えても却下の記録は失われない。

環境変数（GitHub の Secrets）
  MIX_WORKER_URL          Worker の URL（例 https://yt-music-mix.<サブドメイン>.workers.dev）
  CF_ACCESS_CLIENT_ID     Cloudflare Access のサービストークン（Cloudflare が発行・失効・更新を管理する）
  CF_ACCESS_CLIENT_SECRET
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .store import _plain, dump_yaml, load_yaml

APPLIED_FIELDS = ("applied_at", "result", "error")


class RemoteError(Exception):
    pass


class Remote:
    """Worker の同期用 API（/api/sync/…）。"""

    def __init__(self, url: str, client_id: str, client_secret: str, timeout: float = 20):
        self.url = url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "Remote | None":
        url = os.environ.get("MIX_WORKER_URL", "").strip()
        cid = os.environ.get("CF_ACCESS_CLIENT_ID", "").strip()
        secret = os.environ.get("CF_ACCESS_CLIENT_SECRET", "").strip()
        if not (url and cid and secret):
            return None
        return cls(url, cid, secret)

    def _request(self, path: str, body: dict | None = None) -> dict:
        headers = {"Accept": "application/json", "User-Agent": "yt-music-playlist-sync",
                   "CF-Access-Client-Id": self.client_id, "CF-Access-Client-Secret": self.client_secret}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, headers=headers,
                                     method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                text = res.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise RemoteError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RemoteError(str(e)) from e
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            # Access のログイン画面（HTML）が返ってきた場合など
            raise RemoteError("JSON ではない応答です（サービストークンと Access のポリシーを確認してください）") from e

    def fetch(self) -> list[dict]:
        return list(self._request("/api/sync/decisions").get("decisions") or [])

    def ack(self, results: list[dict]) -> int:
        if not results:
            return 0
        return int(self._request("/api/sync/ack", {"results": results}).get("acknowledged") or 0)


def key_of(rec: dict) -> tuple[str, str]:
    return rec["target"], rec["video"]


class Decisions:
    """決定の記録（data/decisions.yaml）。キーは（提案先の再生リストID, 動画ID）。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        data = _plain(load_yaml(self.path, {}) or {})
        self.records: dict[tuple[str, str], dict] = {}
        for r in data.get("decisions") or []:
            if r.get("target") and r.get("video"):
                self.records[key_of(r)] = dict(r)
        self.unacked: list[tuple[str, str]] = []   # Worker にまだ結果を返していないもの

    def merge(self, remote: list[dict]) -> int:
        """Worker の記録を取り込む。新しく決め直したものは、手元の反映結果を捨てる。取り込んだ件数を返す。"""
        n = 0
        for r in remote:
            if not (r.get("target") and r.get("video") and r.get("action")):
                continue
            k = key_of(r)
            mine = self.records.get(k)
            if mine and str(mine.get("decided_at") or "") > str(r.get("decided_at") or ""):
                continue   # 手元のほうが新しい（Worker のデータが巻き戻った場合など）
            if mine and mine.get("decided_at") == r.get("decided_at"):
                if mine.get("applied_at") and not r.get("applied_at"):
                    self.unacked.append(k)   # 反映したが、前回は Worker に伝えられなかった
                    continue
                if mine == {**mine, **r}:
                    continue
            new = {x: r.get(x) for x in ("target", "target_name", "video", "title", "channel", "action",
                                           "decided_at", "by") if r.get(x) is not None}
            for f in APPLIED_FIELDS:
                if r.get(f):
                    new[f] = r[f]
            self.records[k] = new
            n += 1
        return n

    def pending_adds(self) -> list[dict]:
        return [r for r in self.records.values() if r.get("action") == "add" and not r.get("applied_at")]

    def pending_rejects(self) -> list[dict]:
        return [r for r in self.records.values() if r.get("action") == "reject" and not r.get("applied_at")]

    def mark(self, rec: dict, result: str, at: str, error: str | None = None) -> None:
        rec["applied_at"], rec["result"] = at, result
        if error:
            rec["error"] = error
        else:
            rec.pop("error", None)
        self.unacked.append(key_of(rec))

    def blocks_suggestion(self, target: str | None, video: str) -> bool:
        """この提案先にはもう出さない（却下した・追加にした）。追加に失敗したものは、もう一度出す。"""
        r = self.records.get((target, video)) if target else None
        if not r:
            return False
        if r.get("action") == "reject":
            return True
        return r.get("action") == "add" and r.get("result") != "failed"

    def ack_payload(self) -> list[dict]:
        out = []
        for k in dict.fromkeys(self.unacked):
            r = self.records.get(k)
            if r and r.get("applied_at"):
                out.append({"target": r["target"], "video": r["video"], "decided_at": r.get("decided_at"),
                            "applied_at": r["applied_at"], "result": r.get("result"),
                            **({"error": r["error"]} if r.get("error") else {})})
        return out

    def save(self) -> None:
        recs = sorted(self.records.values(), key=lambda r: (str(r.get("decided_at") or ""), r["target"], r["video"]))
        dump_yaml(self.path, {"decisions": recs})
