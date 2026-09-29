"""Helpers for creating an authenticated Google API service."""

import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


API_SERVICE_NAME = "gmail"
API_VERSION = "v1"
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
CLIENT_SECRETS_FILE = os.getenv("GOOGLE_OAUTH_CLIENT_SECRETS", "client_secret.json")
TOKEN_FILE = os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "token.json")


def google_service(
    api_service_name=API_SERVICE_NAME,
    api_version=API_VERSION,
    scopes=None,
    client_secrets_file=CLIENT_SECRETS_FILE,
    token_file=TOKEN_FILE,
):
    """Build and return a Google API client authenticated with OAuth."""
    requested_scopes = scopes or SCOPES
    credentials = None

    if os.path.exists(token_file):
        credentials = Credentials.from_authorized_user_file(
            token_file, requested_scopes
        )

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except RefreshError:
            credentials = None

    if not credentials or not credentials.valid:
        if not os.path.exists(client_secrets_file):
            raise FileNotFoundError(
                f"OAuth client secrets file not found: {client_secrets_file}"
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            client_secrets_file, requested_scopes
        )
        credentials = flow.run_local_server(port=0)
        with open(token_file, "w", encoding="utf-8") as token:
            token.write(credentials.to_json())

    return build(api_service_name, api_version, credentials=credentials)