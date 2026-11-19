"""YouTube Data API v3 の薄いラッパー（クォータ計測付き）。"""
from __future__ import annotations

import os
import re
import time

SCOPES = ["https://www.googleapis.com/auth/youtube"]
COST = {"list": 1, "insert": 50, "delete": 50, "search": 100}


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


def uploads_playlist_id(channel_id: str) -> str:
    return "UU" + channel_id[2:] if channel_id.startswith("UC") else channel_id


class YouTube:
    def __init__(self, service, budget: int):
        self.s = service
        self.budget = budget
        self.used = 0

    @classmethod
    def from_env(cls, budget: int) -> "YouTube":
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

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
                part="snippet,contentDetails", id=",".join(chunk)), "list")
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
                })
        return out

    def search_video_ids(self, query: str, published_after: str | None) -> list[str]:
        kw = dict(part="snippet", q=query, type="video", maxResults=50, order="date")
        if published_after:
            kw["publishedAfter"] = published_after
        r = self._exec(self.s.search().list(**kw), "search")
        return [it["id"]["videoId"] for it in r.get("items", [])]

    # ---- write -----------------------------------------------------------
    def create_playlist(self, title: str, privacy: str) -> str:
        r = self._exec(self.s.playlists().insert(part="snippet,status", body={
            "snippet": {"title": title, "description": "yt-playlist-sync により自動作成"},
            "status": {"privacyStatus": privacy},
        }), "insert")
        return r["id"]

    def add_item(self, playlist_id: str, video_id: str) -> str:
        r = self._exec(self.s.playlistItems().insert(part="snippet", body={
            "snippet": {"playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id}},
        }), "insert")
        return r["id"]

    def delete_item(self, item_id: str) -> None:
        self._exec(self.s.playlistItems().delete(id=item_id), "delete")
