import os
import time
import secrets
import requests
from flask import Flask, redirect, request, session, render_template_string
from dotenv import load_dotenv
from storage import init_db, save_authorization
from discord_api import ensure_member_and_role

load_dotenv()
API = "https://discord.com/api/v10"
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI")
SESSION_SECRET = os.getenv("FLASK_SECRET_KEY")
DEFAULT_GUILD = os.getenv("GUILD_ID")

PAGE = """
<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Discord Verification</title>
<style>
body{font-family:Arial,sans-serif;background:#202225;color:#fff;display:grid;place-items:center;min-height:100vh;margin:0}
main{max-width:520px;margin:20px;padding:28px;background:#2f3136;border-radius:14px;text-align:center}
a{display:inline-block;background:#5865f2;color:white;padding:12px 20px;border-radius:8px;text-decoration:none}
p{color:#d7d7d7;line-height:1.5}
</style></head><body><main><h2>{{ title }}</h2><p>{{ message }}</p>{{ button|safe }}</main></body></html>
"""

def create_app():
    app = Flask(__name__)
    app.secret_key = SESSION_SECRET or secrets.token_urlsafe(48)

    @app.get("/")
    def home():
        return render_template_string(PAGE, title="Discord verification", message="Open the verification link from the Discord server.", button="")

    @app.get("/verify")
    def verify():
        guild_id = request.args.get("guild_id", DEFAULT_GUILD)
        if not guild_id or guild_id != DEFAULT_GUILD:
            return render_template_string(PAGE, title="Invalid server", message="This verification link is not configured for this server.", button=""), 400
        if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI]):
            return render_template_string(PAGE, title="Setup incomplete", message="The bot owner has not configured OAuth2 yet.", button=""), 500

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
        from urllib.parse import urlencode
        return redirect(f"https://discord.com/oauth2/authorize?{urlencode(params)}")

    @app.get("/callback")
    def callback():
        if request.args.get("error"):
            return render_template_string(PAGE, title="Authorization cancelled", message="You did not approve Discord authorization. You can close this page.", button="")
        code = request.args.get("code")
        state = request.args.get("state")
        expected = session.pop("oauth_state", None)
        guild_id = session.pop("guild_id", None)
        if not code or not state or not expected or not secrets.compare_digest(state, expected) or guild_id != DEFAULT_GUILD:
            return render_template_string(PAGE, title="Verification failed", message="The verification session expired or was invalid. Return to Discord and try again.", button=""), 400

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
            return render_template_string(PAGE, title="Authorization failed", message=f"Discord token exchange failed ({token_response.status_code}). Try again.", button=""), 400
        token_data = token_response.json()
        access_token = token_data["access_token"]
        me_response = requests.get(
            f"{API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20
        )
        if not me_response.ok:
            return render_template_string(PAGE, title="Could not read profile", message="Discord did not return your profile. Please try again.", button=""), 400
        user = me_response.json()
        expires_at = int(time.time()) + int(token_data.get("expires_in", 604800))
        save_authorization(
            str(user["id"]),
            user.get("global_name") or user["username"],
            str(guild_id),
            access_token,
            token_data.get("refresh_token", ""),
            expires_at
        )

        try:
            ensure_member_and_role(str(guild_id), str(user["id"]), access_token)
            message = "Your Discord authorization succeeded. You have been added to the server."
        except Exception as exc:
            # Authorization is saved; an admin can retry with !member @user.
            message = "Your authorization was saved, but automatic joining/role assignment did not complete. Please contact a server administrator."
            print(f"Join/role error for {user['id']}: {exc}")

        return render_template_string(PAGE, title="Verification complete", message=message, button="")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    init_db()
    return app