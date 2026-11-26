"""YouTube Data API v3 の薄いラッパー（クォータ計測付き）。"""
from __future__ import annotations

import os
import re
import time

SCOPES = ["https://www.googleapis.com/auth/youtube"]
COST = {"list": 1, "insert": 50, "delete": 50, "search": 100}
CREATED_MARK = "yt-playlist-sync により自動作成"


class QuotaExceeded(Exception):
    """設定した1日の予算、または YouTube 側のクォータを使い切った。"""


class ApiError(Exception):
    def __init__(self, status: int, reason: str, message: str = ""):
        super().__init__(f"{status} {reason} {message}")
        self.status = status
        self.reason = reason


def parse_duration(iso: str | None) -> int | None:
    """ISO 8601 (PT1H2M3S) を秒に変換。"""
    if not iso:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


OIDC_AUDIENCE = "yt-music-playlist"


def github_oidc_token(audience: str) -> str:
    """GitHub Actions の OIDC トークン（その実行専用・数分で失効）を受け取る。

    ワークフローに permissions: id-token: write が必要。GitHub Actions の外では使えない。
    """
    import json
    import urllib.parse
    import urllib.request

    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    bearer = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer:
        raise RuntimeError("GitHub の OIDC トークンを取得できません。Apps Script の方式は GitHub Actions の中でだけ使えます"
                           "（ワークフローに permissions: id-token: write が必要です。手元では動かせません）")
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}audience={urllib.parse.quote(audience)}",
                                 headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))["value"]


def fetch_gas_token(url: str, id_token: str) -> str:
    """Apps Script のWebアプリに GitHub の OIDC トークンを送り、YouTube のアクセストークン（約1時間有効）を受け取る。"""
    import json
    import urllib.request

    req = urllib.request.Request(url, data=json.dumps({"id_token": id_token}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:  # 302 リダイレクトは自動で追従
        text = r.read().decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        raise RuntimeError("トークン発行Webアプリの応答がJSONではありません。"
                           "デプロイのアクセス権が「全員」になっているか確認してください") from None
    if "access_token" not in data:
        raise RuntimeError(f"トークンを取得できません: {data.get('error')}"
                           "（Apps Script のスクリプト プロパティ ALLOWED_REPOSITORY と、Code.gs が最新かを確認）")
    return data["access_token"]


def uploads_playlist_id(channel_id: str) -> str:
    return "UU" + channel_id[2:] if channel_id.startswith("UC") else channel_id


class YouTube:
    def __init__(self, service, budget: int):
        self.s = service
        self.budget = budget
        self.used = 0

    @classmethod
    def from_env(cls, budget: int) -> "YouTube":
        """認証情報を環境変数から読む。

        - YT_TOKEN_URL: Apps Script のトークン発行Webアプリ（推奨）。GitHub Actions の OIDC トークンで認証する
        - YT_CLIENT_ID + YT_CLIENT_SECRET + YT_REFRESH_TOKEN: 自前の OAuth クライアント
        """
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        if os.environ.get("YT_TOKEN_URL"):
            aud = os.environ.get("YT_TOKEN_AUDIENCE") or OIDC_AUDIENCE
            creds = Credentials(fetch_gas_token(os.environ["YT_TOKEN_URL"], github_oidc_token(aud)))
        else:
            creds = Credentials(
                None,
                refresh_token=os.environ["YT_REFRESH_TOKEN"],
                token_uri="https://oauth2.googleapis.com/token",
                client_id=os.environ["YT_CLIENT_ID"],
                client_secret=os.environ["YT_CLIENT_SECRET"],
                scopes=SCOPES,
            )
        return cls(build("youtube", "v3", credentials=creds, cache_discovery=False), budget)

    # ---- low level -------------------------------------------------------
    @property
    def remaining(self) -> int:
        return self.budget - self.used

    def can_afford(self, kind: str) -> bool:
        return self.used + COST[kind] <= self.budget

    def _exec(self, request, kind: str):
        if not self.can_afford(kind):
            raise QuotaExceeded(f"budget {self.budget} reached (used {self.used})")
        self.used += COST[kind]
        from googleapiclient.errors import HttpError

        for attempt in range(4):
            try:
                return request.execute()
            except HttpError as e:
                status = e.resp.status
                body = e.content.decode("utf-8", "replace") if e.content else ""
                if "quotaExceeded" in body or "dailyLimitExceeded" in body:
                    raise QuotaExceeded("YouTube API quota exceeded") from e
                if status in (500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                m = re.search(r'"reason":\s*"([^"]+)"', body)
                raise ApiError(status, m.group(1) if m else "", body[:300]) from e

    # ---- read ------------------------------------------------------------
    def my_playlists(self) -> list[dict]:
        out, token = [], None
        while True:
            r = self._exec(self.s.playlists().list(
                part="snippet,contentDetails", mine=True, maxResults=50, pageToken=token), "list")
            for it in r.get("items", []):
                out.append({"id": it["id"], "title": it["snippet"]["title"],
                            "description": it["snippet"].get("description") or "",
                            "count": it["contentDetails"].get("itemCount")})
            token = r.get("nextPageToken")
            if not token:
                return out

    def playlist_pages(self, playlist_id: str):
        """1ページ（最大50件）ずつ yield する。"""
        token = None
        while True:
            r = self._exec(self.s.playlistItems().list(
                part="snippet,contentDetails,status", playlistId=playlist_id,
                maxResults=50, pageToken=token), "list")
            page = []
            for it in r.get("items", []):
                sn = it["snippet"]
                page.append({
                    "item_id": it["id"],
                    "video_id": it["contentDetails"]["videoId"],
                    "title": sn.get("title", ""),
                    "channel_id": sn.get("videoOwnerChannelId"),
                    "channel_title": sn.get("videoOwnerChannelTitle"),
                    "position": sn.get("position"),
                    "privacy": it.get("status", {}).get("privacyStatus"),
                })
            yield page
            token = r.get("nextPageToken")
            if not token:
                return

    def playlist_items(self, playlist_id: str) -> list[dict]:
        return [x for page in self.playlist_pages(playlist_id) for x in page]

    def videos(self, ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            r = self._exec(self.s.videos().list(
                part="snippet,contentDetails,status", id=",".join(chunk)), "list")
            for it in r.get("items", []):
                sn = it["snippet"]
                out.append({
                    "id": it["id"],
                    "title": sn.get("title", ""),
                    "channel_id": sn.get("channelId"),
                    "channel_title": sn.get("channelTitle"),
                    "published_at": sn.get("publishedAt"),
                    "description": (sn.get("description") or "")[:800],
                    "category": sn.get("categoryId"),
                    "duration": parse_duration(it.get("contentDetails", {}).get("duration")),
                    "live": sn.get("liveBroadcastContent", "none"),
                    "privacy": it.get("status", {}).get("privacyStatus"),
                })
        return out

    def uploads_id(self, channel_id: str) -> str | None:
        """チャンネル情報に書かれたアップロード一覧のID（UU… と違う場合や、ない場合がある）。"""
        r = self._exec(self.s.channels().list(part="contentDetails", id=channel_id), "list")
        items = r.get("items", [])
        return items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads") if items else None

    def uploads_ids(self, channel_ids: list[str]) -> dict[str, str | None]:
        """チャンネルID -> チャンネル情報に書かれたアップロード一覧のID（50チャンネルで1ユニット）。
        存在しないチャンネルは結果に含まれない。"""
        out = {}
        for i in range(0, len(channel_ids), 50):
            r = self._exec(self.s.channels().list(part="contentDetails", id=",".join(channel_ids[i:i + 50])), "list")
            for it in r.get("items", []):
                out[it["id"]] = it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        return out

    def channel_playlists(self, channel_id: str) -> list[dict]:
        """チャンネルが公開している再生リスト [{id, title, count}]（50件で1ユニット）。"""
        out, token = [], None
        while True:
            r = self._exec(self.s.playlists().list(part="snippet,contentDetails", channelId=channel_id,
                                                   maxResults=50, pageToken=token), "list")
            out += [{"id": it["id"], "title": it["snippet"].get("title"),
                     "count": it.get("contentDetails", {}).get("itemCount")} for it in r.get("items", [])]
            token = r.get("nextPageToken")
            if not token:
                return out

    def playlists_info(self, ids: list[str]) -> dict[str, dict]:
        """再生リストID -> {title, count, channel_id}（50件で1ユニット）。見つからないものは含まれない。"""
        out = {}
        for i in range(0, len(ids), 50):
            r = self._exec(self.s.playlists().list(part="snippet,contentDetails", id=",".join(ids[i:i + 50]),
                                                   maxResults=50), "list")
            for it in r.get("items", []):
                out[it["id"]] = {"title": it["snippet"].get("title"), "channel_id": it["snippet"].get("channelId"),
                                 "count": it.get("contentDetails", {}).get("itemCount")}
        return out

    def channel_sections(self, channel_id: str) -> list[dict]:
        """チャンネルページの棚 [{type, title, playlists}]（診断用。1ユニット）。"""
        r = self._exec(self.s.channelSections().list(part="snippet,contentDetails", channelId=channel_id), "list")
        return [{"type": it.get("snippet", {}).get("type"), "title": it.get("snippet", {}).get("title"),
                 "playlists": it.get("contentDetails", {}).get("playlists", [])} for it in r.get("items", [])]

    def search_channel_playlists(self, channel_id: str) -> list[dict]:
        """チャンネルの再生リストを検索で取得（診断用。100ユニット）。"""
        r = self._exec(self.s.search().list(part="snippet", channelId=channel_id, type="playlist", maxResults=50),
                       "search")
        return [{"id": it["id"]["playlistId"], "title": it["snippet"].get("title")} for it in r.get("items", [])]

    def section_playlists(self, channel_id: str) -> list[str]:
        """チャンネルページの棚（アルバム・シングルなど）に並ぶ再生リストのID。"""
        r = self._exec(self.s.channelSections().list(part="contentDetails", channelId=channel_id), "list")
        out = []
        for it in r.get("items", []):
            out += it.get("contentDetails", {}).get("playlists", [])
        return list(dict.fromkeys(out))

    def search_channel_video_ids(self, channel_id: str, published_after: str | None = None) -> list[str]:
        """チャンネルの動画を検索で取得（新しい順に最大50件、100ユニット）。"""
        kw = dict(part="id", channelId=channel_id, type="video", maxResults=50, order="date")
        if published_after:
            kw["publishedAfter"] = published_after
        r = self._exec(self.s.search().list(**kw), "search")
        return [it["id"]["videoId"] for it in r.get("items", [])]

    def channels_info(self, ids: list[str]) -> dict[str, dict]:
        """チャンネルID -> {"title", "handle"}。存在しないチャンネルは結果に含まれない。"""
        out = {}
        for i in range(0, len(ids), 50):
            r = self._exec(self.s.channels().list(part="snippet", id=",".join(ids[i:i + 50])), "list")
            for it in r.get("items", []):
                h = it["snippet"].get("customUrl")
                out[it["id"]] = {"title": it["snippet"].get("title"),
                                 "handle": (h if h.startswith("@") else "@" + h) if h else None}
        return out

    def channel_handles(self, ids: list[str]) -> dict[str, str]:
        """チャンネルID -> @ハンドル"""
        return {c: i["handle"] for c, i in self.channels_info(ids).items() if i["handle"]}

    def search_video_ids(self, query: str, published_after: str | None) -> list[str]:
        kw = dict(part="snippet", q=query, type="video", maxResults=50, order="date")
        if published_after:
            kw["publishedAfter"] = published_after
        r = self._exec(self.s.search().list(**kw), "search")
        return [it["id"]["videoId"] for it in r.get("items", [])]

    # ---- write -----------------------------------------------------------
    def create_playlist(self, title: str, privacy: str) -> str:
        r = self._exec(self.s.playlists().insert(part="snippet,status", body={
            "snippet": {"title": title, "description": CREATED_MARK},
            "status": {"privacyStatus": privacy},
        }), "insert")
        return r["id"]

    def add_item(self, playlist_id: str, video_id: str) -> str:
        r = self._exec(self.s.playlistItems().insert(part="snippet", body={
            "snippet": {"playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id}},
        }), "insert")
        return r["id"]

    # 削除系のAPI（playlistItems.delete 等）は意図的に実装していない。データ保護のため、
    # このプログラムは再生リストから動画を消すことができない。
