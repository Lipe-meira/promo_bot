"""Control-plane API of the Promo Bot Web SaaS.

Independent from the legacy CLI (`promo_bot`): it never imports the bot runtime, opens no Telegram
session and performs no provider call. It owns identity admission and workspace authorization in
PostgreSQL; identity itself comes from Clerk session tokens.
"""
