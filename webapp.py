import os
import time
import secrets
from urllib.parse import urlencode

import requests
from flask import Flask, redirect, request, session, render_template_string
from dotenv import load_dotenv

from storage import init_db, save_authorization, get_guild_role
from discord_api import ensure_member_and_role

load_dotenv()

API = "https://discord.com/api/v10"
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI")
SESSION_SECRET = os.getenv("FLASK_SECRET_KEY")

PAGE = """
<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Discord Verification</title>
<style>
body{font-family:Arial,sans-serif;background:#202225;color:#fff;display:grid;place-items:center;min-height:100vh;margin:0}
main{max-width:520px;margin:20px;padding:28px;background:#2f3136;border-radius:14px;text-align:center}
p{color:#d7d7d7;line-height:1.5}
</style></head><body><main><h2>{{ title }}</h2><p>{{ message }}</p></main></body></html>
"""


def _page(title, message, status=200):
    return render_template_string(PAGE, title=title, message=message), status


def create_app():
    app = Flask(__name__)
    app.secret_key = SESSION_SECRET or secrets.token_urlsafe(48)

    @app.get("/")
    def home():
        return _page(
            "Discord verification",
            "Open the verification link from the Discord server you want to join."
        )

    @app.get("/verify")
    def verify():
        guild_id = request.args.get("guild_id", "")
        if not guild_id.isdigit():
            return _page("Invalid server", "The verification link has an invalid server ID.", 400)
        if not get_guild_role(guild_id):
            return _page("Not configured", "This server has not configured verification yet.", 404)
        if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI]):
            return _page("Setup incomplete", "The bot owner has not configured OAuth2.", 500)

        state = secrets.token_urlsafe(32)
        session["oauth_state"] = state
        session["guild_id"] = guild_id
        params = {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "identify guilds.join",
            "state": state,
            "prompt": "consent"
        }
        return redirect(f"https://discord.com/oauth2/authorize?{urlencode(params)}")

    @app.get("/callback")
    def callback():
        if request.args.get("error"):
            return _page(
                "Authorization cancelled",
                "You did not approve Discord authorization. You can close this page."
            )

        code = request.args.get("code")
        state = request.args.get("state")
        expected = session.pop("oauth_state", None)
        guild_id = session.pop("guild_id", None)

        if (
            not code or not state or not expected
            or not secrets.compare_digest(state, expected)
            or not guild_id or not guild_id.isdigit()
        ):
            return _page(
                "Verification failed",
                "The verification session expired or was invalid. Return to Discord and try again.",
                400
            )

        if not get_guild_role(guild_id):
            return _page("Server not configured", "The server's verification setup is no longer available.", 404)

        token_response = requests.post(
            f"{API}/oauth2/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20
        )
        if not token_response.ok:
            return _page(
                "Authorization failed",
                f"Discord token exchange failed ({token_response.status_code}). Please try again.",
                400
            )

        token_data = token_response.json()
        access_token = token_data["access_token"]
        me_response = requests.get(
            f"{API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20
        )
        if not me_response.ok:
            return _page("Could not read profile", "Discord did not return your profile. Please try again.", 400)

        user = me_response.json()
        expires_at = int(time.time()) + int(token_data.get("expires_in", 604800))
        save_authorization(
            str(user["id"]),
            user.get("global_name") or user["username"],
            guild_id,
            access_token,
            token_data.get("refresh_token", ""),
            expires_at
        )

        try:
            ensure_member_and_role(guild_id, str(user["id"]), access_token)
            message = "Authorization succeeded. You have been added to the server and verified."
        except Exception as exc:
            message = (
                "Your authorization was saved, but automatic joining or role assignment did not "
                "complete. Please contact a server administrator; they can retry with !member."
            )
            print(f"Join/role error for user {user['id']} in guild {guild_id}: {exc}")

        return _page("Verification complete", message)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    init_db()
    return app
