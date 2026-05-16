import asyncio
import base64
import os

from dotenv import load_dotenv

load_dotenv()

# Restore token files from base64 env vars (used in cloud deployments)
def _restore_file(env_var: str, path: str):
    data = os.getenv(env_var)
    if data and not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))

_restore_file("GMAIL_CREDENTIALS_B64", os.getenv("GMAIL_CREDENTIALS_FILE", "config/gmail_credentials.json"))
_restore_file("GMAIL_TOKEN_B64",       os.getenv("GMAIL_TOKEN_FILE",        "config/gmail_token.json"))
_restore_file("OUTLOOK1_TOKEN_B64",    os.getenv("OUTLOOK1_TOKEN_FILE",     "config/outlook1_token.json"))
_restore_file("OUTLOOK2_TOKEN_B64",    os.getenv("OUTLOOK2_TOKEN_FILE",     "config/outlook2_token.json"))

from src.utils.logger import setup_logger
from src.agent.email_manager import EmailManager
from src.agent.scheduler import DailyScheduler
from src.telegram.bot import MailBot

logger = setup_logger("main")

REQUIRED_ENV_VARS = [
    "TELEGRAM_BOT_TOKEN",
    "ANTHROPIC_API_KEY",
    "GMAIL_TOKEN_B64",
    "OUTLOOK1_TOKEN_B64",
    "OUTLOOK2_TOKEN_B64",
]

def _check_env_vars():
    missing = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
    present = [v for v in REQUIRED_ENV_VARS if os.getenv(v)]
    if present:
        logger.info(f"Env vars present: {', '.join(present)}")
    if missing:
        logger.error(f"MISSING env vars: {', '.join(missing)}")
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


async def main():
    _check_env_vars()
    logger.info("Initializing Email AI Agent...")

    manager = EmailManager()
    bot = MailBot(manager)

    # Wrap Telegram send for the scheduler
    allowed_id = os.getenv("TELEGRAM_ALLOWED_USER_ID")

    async def send_telegram(text: str):
        if allowed_id:
            await bot.app.bot.send_message(
                chat_id=int(allowed_id),
                text=text,
                parse_mode="Markdown",
            )

    scheduler = DailyScheduler(manager, send_telegram)
    scheduler.start()

    logger.info("Agent ready — starting Telegram bot polling")
    await bot.app.initialize()
    await bot.app.start()
    await bot.app.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.Event().wait()  # run forever
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    finally:
        scheduler.stop()
        await bot.app.updater.stop()
        await bot.app.stop()
        await bot.app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
