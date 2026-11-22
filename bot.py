import os
import threading
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

from webapp import create_app
from storage import get_authorized_user, get_stats
from discord_api import ensure_member_and_role, refresh_access_token

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
PREFIX = os.getenv("BOT_PREFIX", "!")
PORT = int(os.getenv("PORT", "5000"))

if not TOKEN or not GUILD_ID:
    raise RuntimeError("Set BOT_TOKEN and GUILD_ID in your .env file.")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        base_url = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        if not base_url:
            raise RuntimeError("Set PUBLIC_BASE_URL in .env before using /setup.")
        self.add_item(discord.ui.Button(
            label="Verify / Join",
            style=discord.ButtonStyle.link,
            url=f"{base_url}/verify?guild_id={GUILD_ID}"
        ))

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user}; synced {len(synced)} slash command(s).")
    except Exception as exc:
        print(f"Slash-command sync failed: {exc}")
    print(f"Bot ready. Prefix: {PREFIX}")

@bot.tree.command(name="setup", description="Post the server verification panel.")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    if not interaction.guild or interaction.guild.id != GUILD_ID:
        await interaction.response.send_message("This command is configured for a different server.", ephemeral=True)
        return
    embed = discord.Embed(
        title="Server Verification",
        description="Click **Verify / Join** to authorize Discord and join this server. You will be asked to approve access.",
        color=discord.Color.blurple()
    )
    await interaction.channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message("Verification panel posted.", ephemeral=True)

@bot.command(name="member")
@commands.has_permissions(administrator=True)
async def member(ctx, user: discord.User):
    """Add/verify a user only if they previously authorized this configured server."""
    record = get_authorized_user(str(user.id), str(GUILD_ID))
    if not record:
        await ctx.reply("That user has not authorized this server through the verification page.")
        return
    try:
        access_token = await asyncio.to_thread(refresh_access_token, str(user.id), str(GUILD_ID), record)
        result = await asyncio.to_thread(ensure_member_and_role, str(GUILD_ID), str(user.id), access_token)
        await ctx.reply(result)
    except Exception as exc:
        await ctx.reply(f"Could not add/verify that member: {exc}")

@bot.command(name="stats")
@commands.has_permissions(administrator=True)
async def stats(ctx):
    data = get_stats(str(GUILD_ID))
    await ctx.reply(
        f"**Verification stats for this server**\n"
        f"Authorized users: **{data['authorized']}**\n"
        f"Joined/role applied: **{data['joined']}**"
    )

@member.error
async def member_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("You need Administrator permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"Usage: `{PREFIX}member @user`")
    elif isinstance(error, commands.BadArgument):
        await ctx.reply(f"Usage: `{PREFIX}member @user`")

@stats.error
async def stats_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("You need Administrator permission to use this command.")

def run_web():
    app = create_app()
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot.run(TOKEN)