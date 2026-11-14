"""Create a local credential file using Google's temporary EmbeddedSetup token."""

import argparse
import getpass
import json
import os
import secrets
import sys
from pathlib import Path

import gpsoauth
import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    target = args.output.expanduser().resolve()
    if target.exists():
        parser.error("Output already exists. Choose a new filename.")
    print("Open https://accounts.google.com/EmbeddedSetup in your own browser.")
    print("Sign in and accept the prompt. If the page keeps loading, continue below.")
    print("In browser Developer Tools, open Application/Storage > Cookies.")
    print("Copy the oauth_token cookie for accounts.google.com privately.")
    email = input("Google email: ").strip()
    temporary = getpass.getpass("Temporary oauth_token cookie (hidden): ").strip()
    if not email or not temporary:
        parser.error("Email and temporary token are required.")
    device_id = secrets.token_hex(8)
    try:
        with requests.Session() as session:
            session.mount(gpsoauth.AUTH_URL, gpsoauth.AuthHTTPAdapter())
            session.headers.update(gpsoauth.build_auth_headers())
            response = session.post(
                gpsoauth.AUTH_URL,
                data=gpsoauth.build_exchange_token_data(email, temporary, device_id),
                timeout=20,
                allow_redirects=False,
            )
            response.raise_for_status()
            token = gpsoauth.google.parse_auth_response(response.text).get("Token")
        if not isinstance(token, str) or not token:
            raise ValueError
    except requests.RequestException, ValueError, KeyError:
        print(
            "Google rejected the exchange or could not be reached. No credentials saved.",
            file=sys.stderr,
        )
        return 1
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive creation avoids overwriting an existing credential or symlink.
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump({"email": email, "master_token": token, "device_id": device_id}, output, indent=2)
        output.write("\n")
    print(f"Credentials saved to {target}. Open this file privately for setup.")
    print("On Windows, the file inherits the directory's access permissions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
