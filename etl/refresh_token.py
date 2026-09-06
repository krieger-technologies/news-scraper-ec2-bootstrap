import os
from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ENV_DIR = os.path.join(BASE_DIR, "env")

TOKEN_PATH = os.path.join(ENV_DIR, "token.json")
CREDS_PATH = os.path.join(ENV_DIR, "credentials.json")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

def main():
    if not os.path.exists(CREDS_PATH):
        raise SystemExit(f"Missing credentials file: {CREDS_PATH}")

    flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
    creds = flow.run_local_server(host="127.0.0.1", port=8765, open_browser=False)

    os.makedirs(ENV_DIR, exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"✅ Wrote new token to: {TOKEN_PATH}")

if __name__ == "__main__":
    main()
