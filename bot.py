import os
import threading
import asyncio

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

from webapp import create_app
from storage import (
    init_db,
    get_authorized_users,
    get_stats,
    set_guild_role
)
from discord_api import ensure_member_and_role, refresh_access_token

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
PREFIX = os.getenv("BOT_PREFIX", "!")
PORT = int(os.getenv("PORT", "5000"))

if not TOKEN:
    raise RuntimeError("Set BOT_TOKEN in your .env file.")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)


class VerifyView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)

        base_url = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

        if not base_url:
            raise RuntimeError("Set PUBLIC_BASE_URL in .env.")

        self.add_item(
            discord.ui.Button(
                label="Verify / Join",
                style=discord.ButtonStyle.link,
                url=f"{base_url}/verify?guild_id={guild_id}"
            )
        )


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user}")
        print(f"Synced {len(synced)} slash commands.")
    except Exception as exc:
        print(f"Slash command sync failed: {exc}")


@bot.tree.command(
    name="setup",
    description="Configure verification for this server."
)
@app_commands.describe(
    verified_role="Role given to members after verification"
)
@app_commands.checks.has_permissions(administrator=True)
async def setup(
    interaction: discord.Interaction,
    verified_role: discord.Role
):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "Run this command inside a server.",
            ephemeral=True
        )
        return

    bot_member = guild.me

    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        await interaction.response.send_message(
            "I need the Manage Roles permission.",
            ephemeral=True
        )
        return

    if verified_role >= bot_member.top_role:
        await interaction.response.send_message(
            "Move my bot role above the verified role.",
            ephemeral=True
        )
        return

    try:
        await interaction.response.defer(ephemeral=True)

        set_guild_role(
            str(guild.id),
            str(verified_role.id)
        )

        view = VerifyView(guild.id)

        embed = discord.Embed(
            title="Server Verification",
            description=(
                "Click **Verify / Join** to authorize Discord "
                "and join this server."
            ),
            color=discord.Color.blurple()
        )

        await interaction.channel.send(
            embed=embed,
            view=view
        )

        await interaction.followup.send(
            f"Verification panel posted. Verified members receive "
            f"{verified_role.mention}.",
            ephemeral=True
        )

    except Exception as exc:
        await interaction.followup.send(
            f"Setup failed: {exc}",
            ephemeral=True
        )


@bot.command(name="member")
@commands.has_permissions(administrator=True)
async def member(ctx):
    if ctx.guild is None:
        await ctx.reply(
            "This command only works inside a server."
        )
        return

    records = await asyncio.to_thread(
        get_authorized_users,
        str(ctx.guild.id)
    )

    if not records:
        await ctx.reply(
            "No users have authorized this server yet."
        )
        return

    status = await ctx.reply(
        f"Processing {len(records)} authorized members..."
    )

    succeeded = 0
    failed = []

    for record in records:
        user_id = record["user_id"]

        try:
            access_token = await asyncio.to_thread(
                refresh_access_token,
                user_id,
                str(ctx.guild.id),
                record
            )

            await asyncio.to_thread(
                ensure_member_and_role,
                str(ctx.guild.id),
                user_id,
                access_token
            )

            succeeded += 1

        except Exception as exc:
            failed.append(
                (user_id, str(exc)[:180])
            )

    summary = (
        "**Finished processing authorized members**\n"
        f"Successful: **{succeeded}**\n"
        f"Failed: **{len(failed)}**"
    )

    if failed:
        summary += "\n\n**Failures (first 10):**\n"

        summary += "\n".join(
            f"• <@{user_id}> — {reason}"
            for user_id, reason in failed[:10]
        )

    await status.edit(
        content=summary[:1900]
    )


@bot.command(name="stats")
@commands.has_permissions(administrator=True)
async def stats(ctx):
    if ctx.guild is None:
        await ctx.reply(
            "This command only works inside a server."
        )
        return

    data = await asyncio.to_thread(
        get_stats,
        str(ctx.guild.id)
    )

    await ctx.reply(
        "**Verification stats for this server**\n"
        f"Authorized users: **{data['authorized']}**\n"
        f"Joined/role applied: **{data['joined']}**"
    )


@setup.error
async def setup_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need Administrator permission."
    else:
        message = f"Setup failed: {error}"

    if interaction.response.is_done():
        await interaction.followup.send(
            message,
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            message,
            ephemeral=True
        )


@member.error
async def member_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply(
            "You need Administrator permission."
        )
    else:
        await ctx.reply(
            f"Could not process members: {error}"
        )


@stats.error
async def stats_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply(
            "You need Administrator permission."
        )


def run_web():
    app = create_app()

    # Listen on all network interfaces so external connections can reach Flask.
    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
        use_reloader=False
    )


if __name__ == "__main__":
    init_db()

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    bot.run(TOKEN)