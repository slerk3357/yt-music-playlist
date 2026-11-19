"""リフレッシュトークンを取得する（手元のPCで1回だけ実行）。

    pip install google-auth-oauthlib
    python scripts/get_token.py client_secret.json

ブラウザが開くので、再生リストを管理しているGoogleアカウントで許可してください。
表示された3つの値を GitHub の Secrets に登録します。
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube"]

path = sys.argv[1] if len(sys.argv) > 1 else "client_secret.json"
flow = InstalledAppFlow.from_client_secrets_file(path, SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
info = json.load(open(path, encoding="utf-8"))
info = info.get("installed") or info.get("web")
print("\n以下を GitHub リポジトリの Settings > Secrets and variables > Actions に登録してください:\n")
print(f"YT_CLIENT_ID      = {info['client_id']}")
print(f"YT_CLIENT_SECRET  = {info['client_secret']}")
print(f"YT_REFRESH_TOKEN  = {creds.refresh_token}")
