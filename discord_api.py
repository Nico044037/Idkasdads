import os
import time
import requests
from dotenv import load_dotenv
from storage import refresh_user_token, set_joined, get_guild_role

load_dotenv()

API = "https://discord.com/api/v10"
BOT_TOKEN = os.getenv("BOT_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")


def _headers():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured.")
    return {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}


def refresh_access_token(user_id, guild_id, record):
    if int(record["expires_at"]) > int(time.time()) + 60:
        return record["access_token"]
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET must be configured.")
    response = requests.post(
        f"{API}/oauth2/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": record["refresh_token"]
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20
    )
    if not response.ok:
        raise RuntimeError(f"Discord token refresh failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    new_access = data["access_token"]
    new_refresh = data.get("refresh_token") or record["refresh_token"]
    expires_at = int(time.time()) + int(data.get("expires_in", 604800))
    refresh_user_token(user_id, guild_id, new_access, new_refresh, expires_at)
    return new_access


def ensure_member_and_role(guild_id, user_id, access_token):
    role_id = get_guild_role(guild_id)
    if not role_id:
        raise RuntimeError("This server has not been configured. Run /setup first.")

    response = requests.put(
        f"{API}/guilds/{guild_id}/members/{user_id}",
        headers=_headers(),
        json={"access_token": access_token},
        timeout=20
    )
    if response.status_code not in (201, 204):
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(f"Discord could not add the member ({response.status_code}): {detail}")

    role_response = requests.put(
        f"{API}/guilds/{guild_id}/members/{user_id}/roles/{role_id}",
        headers=_headers(),
        timeout=20
    )
    if role_response.status_code != 204:
        try:
            detail = role_response.json()
        except Exception:
            detail = role_response.text
        raise RuntimeError(
            f"Member was added, but role assignment failed ({role_response.status_code}): {detail}"
        )

    set_joined(user_id, guild_id, True)
    return "Member is in the server and the verified role has been applied."
