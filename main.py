import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from src.utils.logger import setup_logger
from src.agent.email_manager import EmailManager
from src.agent.scheduler import DailyScheduler
from src.telegram.bot import MailBot

logger = setup_logger("main")


async def main():
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
