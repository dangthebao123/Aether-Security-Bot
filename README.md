# Aether Security Bot

Bot Discord tập trung vào moderation và security.

## Tính năng
- Anti-spam + auto timeout
- Anti-link
- Anti mass-mention
- Warning system lưu SQLite
- Ban / Kick / Timeout
- Clear message
- Lock / Unlock channel
- Security status
- Security logging
- Slash commands

## Cài đặt

```powershell
py -m pip install -r requirements.txt
```

Copy `.env.example` thành `.env`, sau đó đặt token mới:

```env
DISCORD_TOKEN=TOKEN_MOI
```

Chạy:

```powershell
py bot.py
```

## Quyền và Intents

Trong Discord Developer Portal, bật:
- Server Members Intent
- Message Content Intent

Khi invite bot, cấp các quyền moderation cần thiết như:
- View Channels
- Send Messages
- Manage Messages
- Moderate Members
- Kick Members
- Ban Members
- Manage Channels

Bot không thể vượt qua role hierarchy của Discord.

## QUAN TRỌNG VỀ TOKEN

Token không được đặt trực tiếp trong `bot.py`.
Nếu token cũ đã bị lộ, hãy reset/regenerate token trước khi chạy bot.
