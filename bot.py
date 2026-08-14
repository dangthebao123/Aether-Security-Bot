"""
AETHER SECURITY BOT
Discord security / moderation bot.

Cài đặt:
    py -m pip install -r requirements.txt

Tạo file .env từ .env.example rồi điền:
    DISCORD_TOKEN=TOKEN_MOI_CUA_BAN

Chạy:
    py bot.py

LƯU Ý:
- KHÔNG đặt token trực tiếp trong code.
- Token đã từng bị lộ phải RESET/REGENERATE trong Discord Developer Portal.
"""

import os
import re
import time
import sqlite3
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("PREFIX", "!")
LOG_CHANNEL_NAME = os.getenv("LOG_CHANNEL_NAME", "aether-security-logs")

if not TOKEN:
    raise RuntimeError(
        "Chưa có DISCORD_TOKEN. Hãy tạo file .env và thêm DISCORD_TOKEN=..."
    )

# -----------------------------
# Cấu hình bảo mật mặc định
# -----------------------------
SPAM_MESSAGES = 6          # Số tin nhắn tối đa...
SPAM_WINDOW = 8            # ...trong số giây này
MUTE_MINUTES = 5           # timeout tự động khi spam
MAX_WARNINGS = 3           # đủ cảnh cáo thì timeout
DELETE_LINKS = True        # Xóa link đáng ngờ
ANTI_MENTION = True        # Chặn mass-mention

# Một số pattern link thường gặp.
URL_RE = re.compile(
    r"(https?://|www\.|discord\.gg/|discord\.com/invite/)",
    re.IGNORECASE,
)

# -----------------------------
# Database SQLite
# -----------------------------
db = sqlite3.connect("aether_security.db")
db.execute("""
CREATE TABLE IF NOT EXISTS warnings (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at INTEGER NOT NULL
)
""")
db.commit()


def add_warning(guild_id: int, user_id: int, reason: str) -> int:
    db.execute(
        "INSERT INTO warnings VALUES (?, ?, ?, ?)",
        (guild_id, user_id, reason, int(time.time())),
    )
    db.commit()
    row = db.execute(
        "SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    ).fetchone()
    return row[0]


def get_warning_count(guild_id: int, user_id: int) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    ).fetchone()
    return row[0]


# -----------------------------
# Bot + Intents
# -----------------------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None,
)

# Lưu lịch sử tin nhắn theo user để phát hiện spam.
message_history = defaultdict(lambda: deque(maxlen=20))

# Chống việc xử lý moderation lặp quá nhanh.
action_cooldown = {}


# -----------------------------
# Utility
# -----------------------------
async def security_log(guild: discord.Guild, title: str, description: str):
    """Gửi log vào channel bảo mật nếu channel tồn tại."""
    channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if not channel:
        return

    embed = discord.Embed(
        title=f"🛡️ AETHER SECURITY | {title}",
        description=description,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="Aether Security Bot")
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        pass


def is_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator


# -----------------------------
# Events
# -----------------------------
@bot.event
async def on_ready():
    print("=" * 60)
    print(f"AETHER SECURITY BOT ONLINE")
    print(f"Logged in as: {bot.user} | ID: {bot.user.id}")
    print(f"Servers: {len(bot.guilds)}")
    print("=" * 60)

    # Sync slash commands để /ping, /ban... xuất hiện.
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as exc:
        print(f"Slash command sync error: {exc}")

    if not security_status.is_running():
        security_status.start()


@bot.event
async def on_guild_join(guild: discord.Guild):
    await security_log(
        guild,
        "BOT JOINED SERVER",
        f"Aether Security vừa tham gia **{guild.name}**.\n"
        f"Members: `{guild.member_count}`",
    )


@bot.event
async def on_member_join(member: discord.Member):
    # Log thành viên mới.
    await security_log(
        member.guild,
        "NEW MEMBER",
        f"👤 {member.mention} (`{member.id}`) vừa tham gia.",
    )


@bot.event
async def on_member_remove(member: discord.Member):
    await security_log(
        member.guild,
        "MEMBER LEFT",
        f"👤 **{member}** (`{member.id}`) đã rời server.",
    )


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    member = message.author
    now = time.monotonic()

    # Admin/mod được bỏ qua anti-spam tự động.
    if not is_admin(member):
        history = message_history[(message.guild.id, member.id)]
        history.append(now)

        # Anti-spam.
        recent = [t for t in history if now - t <= SPAM_WINDOW]
        if len(recent) >= SPAM_MESSAGES:
            key = (message.guild.id, member.id)
            if now - action_cooldown.get(key, 0) > 30:
                action_cooldown[key] = now

                try:
                    await member.timeout(
                        timedelta(minutes=MUTE_MINUTES),
                        reason="Aether Security: Anti-spam",
                    )
                    await security_log(
                        message.guild,
                        "ANTI-SPAM",
                        f"🔇 {member.mention} bị timeout {MUTE_MINUTES} phút "
                        f"do gửi quá nhiều tin nhắn.",
                    )
                except discord.Forbidden:
                    await security_log(
                        message.guild,
                        "PERMISSION ERROR",
                        "Không đủ quyền để timeout một thành viên.",
                    )

        # Anti-link.
        if DELETE_LINKS and URL_RE.search(message.content):
            try:
                await message.delete()
                await security_log(
                    message.guild,
                    "LINK BLOCKED",
                    f"🔗 Đã xóa link từ {member.mention}:\n"
                    f"`{message.content[:500]}`",
                )
                return
            except discord.Forbidden:
                pass

        # Anti mass-mention.
        if ANTI_MENTION and (
            len(message.mentions) >= 5 or message.mention_everyone
        ):
            try:
                await message.delete()
                await security_log(
                    message.guild,
                    "MASS MENTION BLOCKED",
                    f"🚨 Tin nhắn mass-mention của {member.mention} đã bị xóa.",
                )
                return
            except discord.Forbidden:
                pass

    await bot.process_commands(message)


# -----------------------------
# Slash commands
# -----------------------------
@bot.tree.command(name="ping", description="Kiểm tra Aether Security có hoạt động không.")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(
        f"🛡️ **Aether Security Online**\nLatency: `{latency}ms`"
    )


@bot.tree.command(name="security", description="Xem trạng thái hệ thống bảo mật.")
async def security(interaction: discord.Interaction):
    if not interaction.guild:
        return await interaction.response.send_message(
            "Lệnh này chỉ dùng trong server.", ephemeral=True
        )

    embed = discord.Embed(
        title="🛡️ AETHER SECURITY STATUS",
        description="Hệ thống bảo vệ đang hoạt động.",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Anti-Spam", value="🟢 ACTIVE", inline=True)
    embed.add_field(name="Anti-Link", value="🟢 ACTIVE", inline=True)
    embed.add_field(name="Anti-Mention", value="🟢 ACTIVE", inline=True)
    embed.add_field(name="Database", value="🟢 SQLite", inline=True)
    embed.add_field(name="Protection", value="🛡️ ONLINE", inline=True)
    embed.set_footer(text="Aether Security Bot")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="warn", description="Cảnh cáo một thành viên.")
@app_commands.describe(member="Thành viên cần cảnh cáo", reason="Lý do")
@app_commands.checks.has_permissions(manage_messages=True)
async def warn(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "Không có lý do",
):
    if member == interaction.user:
        return await interaction.response.send_message(
            "Bạn không thể tự cảnh cáo mình.", ephemeral=True
        )

    count = add_warning(interaction.guild.id, member.id, reason)

    await interaction.response.send_message(
        f"⚠️ {member.mention} đã nhận cảnh cáo **#{count}**.\n"
        f"Lý do: `{reason}`"
    )

    await security_log(
        interaction.guild,
        "WARNING",
        f"{member.mention} bị cảnh cáo bởi {interaction.user.mention}\n"
        f"Warnings: `{count}`\nReason: `{reason}`",
    )

    if count >= MAX_WARNINGS:
        try:
            await member.timeout(
                timedelta(minutes=MUTE_MINUTES),
                reason="Aether Security: Maximum warnings reached",
            )
            await security_log(
                interaction.guild,
                "AUTO TIMEOUT",
                f"{member.mention} bị timeout vì đạt `{MAX_WARNINGS}` warnings.",
            )
        except discord.Forbidden:
            pass


@bot.tree.command(name="warnings", description="Xem số cảnh cáo của một thành viên.")
@app_commands.describe(member="Thành viên cần kiểm tra")
@app_commands.checks.has_permissions(manage_messages=True)
async def warnings(interaction: discord.Interaction, member: discord.Member):
    count = get_warning_count(interaction.guild.id, member.id)
    await interaction.response.send_message(
        f"📋 {member.mention} hiện có **{count}** warning(s)."
    )


@bot.tree.command(name="clear", description="Xóa tin nhắn.")
@app_commands.describe(amount="Số tin nhắn muốn xóa (1-100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(
        f"🧹 Đã xóa `{len(deleted)}` tin nhắn.", ephemeral=True
    )
    await security_log(
        interaction.guild,
        "MESSAGE PURGE",
        f"{interaction.user.mention} đã xóa `{len(deleted)}` tin nhắn "
        f"ở {interaction.channel.mention}.",
    )


@bot.tree.command(name="timeout", description="Timeout một thành viên.")
@app_commands.describe(member="Thành viên", minutes="Số phút", reason="Lý do")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout_member(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: app_commands.Range[int, 1, 10080],
    reason: str = "Aether Security moderation",
):
    if member.top_role >= interaction.user.top_role and not is_admin(interaction.user):
        return await interaction.response.send_message(
            "❌ Bạn không thể timeout người có role ngang/cao hơn bạn.",
            ephemeral=True,
        )

    await member.timeout(
        timedelta(minutes=minutes),
        reason=reason,
    )

    await interaction.response.send_message(
        f"🔇 {member.mention} đã bị timeout `{minutes}` phút."
    )
    await security_log(
        interaction.guild,
        "TIMEOUT",
        f"{member.mention} bị timeout bởi {interaction.user.mention}\n"
        f"Duration: `{minutes} min`\nReason: `{reason}`",
    )


@bot.tree.command(name="kick", description="Kick một thành viên.")
@app_commands.describe(member="Thành viên cần kick", reason="Lý do")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "Aether Security moderation",
):
    if member.top_role >= interaction.user.top_role and not is_admin(interaction.user):
        return await interaction.response.send_message(
            "❌ Không thể kick người có role ngang/cao hơn bạn.",
            ephemeral=True,
        )

    await member.kick(reason=reason)
    await interaction.response.send_message(f"👢 Đã kick **{member}**.")
    await security_log(
        interaction.guild,
        "KICK",
        f"**{member}** bị kick bởi {interaction.user.mention}\nReason: `{reason}`",
    )


@bot.tree.command(name="ban", description="Ban một thành viên.")
@app_commands.describe(member="Thành viên cần ban", reason="Lý do")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "Aether Security moderation",
):
    if member.top_role >= interaction.user.top_role and not is_admin(interaction.user):
        return await interaction.response.send_message(
            "❌ Không thể ban người có role ngang/cao hơn bạn.",
            ephemeral=True,
        )

    await member.ban(reason=reason, delete_message_days=1)
    await interaction.response.send_message(f"🔨 Đã ban **{member}**.")
    await security_log(
        interaction.guild,
        "BAN",
        f"**{member}** bị ban bởi {interaction.user.mention}\nReason: `{reason}`",
    )


@bot.tree.command(name="lock", description="Khóa channel hiện tại.")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock(interaction: discord.Interaction):
    channel = interaction.channel
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite,
        reason="Aether Security channel lock",
    )
    await interaction.response.send_message("🔒 Channel đã được khóa.")
    await security_log(
        interaction.guild,
        "CHANNEL LOCK",
        f"{interaction.user.mention} đã khóa {channel.mention}.",
    )


@bot.tree.command(name="unlock", description="Mở khóa channel hiện tại.")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock(interaction: discord.Interaction):
    channel = interaction.channel
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None
    await channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite,
        reason="Aether Security channel unlock",
    )
    await interaction.response.send_message("🔓 Channel đã được mở khóa.")
    await security_log(
        interaction.guild,
        "CHANNEL UNLOCK",
        f"{interaction.user.mention} đã mở khóa {channel.mention}.",
    )


# -----------------------------
# Error handler cho slash commands
# -----------------------------
@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ Bạn không có quyền dùng lệnh này."
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = "⏳ Bạn đang dùng lệnh quá nhanh."
    else:
        print("Command error:", repr(error))
        message = "❌ Đã xảy ra lỗi khi thực hiện lệnh."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# -----------------------------
# Status task
# -----------------------------
@tasks.loop(minutes=10)
async def security_status():
    """Kiểm tra bot còn kết nối và cập nhật presence."""
    guild_count = len(bot.guilds)
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{guild_count} server • Aether Security",
        ),
    )


@security_status.before_loop
async def before_security_status():
    await bot.wait_until_ready()


# -----------------------------
# Start
# -----------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
