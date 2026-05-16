import os
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.utils.logger import setup_logger

logger = setup_logger("scheduler")


class DailyScheduler:
    def __init__(self, manager, send_message_fn):
        """
        manager: EmailManager instance
        send_message_fn: async callable(text) that sends a Telegram message
        """
        self.manager = manager
        self.send = send_message_fn
        self.scheduler = AsyncIOScheduler(timezone=os.getenv("TIMEZONE", "Europe/Paris"))

    def start(self):
        time_str = os.getenv("DAILY_SUMMARY_TIME", "08:00")
        hour, minute = time_str.split(":")
        self.scheduler.add_job(
            self._daily_job,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id="daily_summary",
            replace_existing=True,
        )
        # Also sort and label new emails every 30 minutes
        self.scheduler.add_job(
            self._sort_job,
            "interval",
            minutes=30,
            id="auto_sort",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info(f"Scheduler started — daily summary at {time_str}, auto-sort every 30 min")

    def stop(self):
        self.scheduler.shutdown(wait=False)

    async def _daily_job(self):
        logger.info("Running daily summary job")
        try:
            summary = await __import__("asyncio").get_event_loop().run_in_executor(
                None, self.manager.generate_daily_summary
            )
            await self.send(f"📊 *Résumé quotidien*\n\n{summary}")
        except Exception as e:
            logger.error(f"Daily summary job error: {e}")
            await self.send(f"❌ Erreur lors du résumé quotidien : {e}")

    async def _sort_job(self):
        logger.info("Running auto-sort job")
        try:
            results = await __import__("asyncio").get_event_loop().run_in_executor(
                None, lambda: self.manager.analyze_and_sort(10)
            )
            new_urgent = [e for e in results if e.get("importance", 0) >= 4]
            if new_urgent:
                lines = ["⚡ *Nouveaux emails importants :*"]
                for e in new_urgent[:5]:
                    lines.append(f"• [{e['account']}] {e.get('subject','')[:60]} — {e.get('from','')[:30]}")
                await self.send("\n".join(lines))
        except Exception as e:
            logger.error(f"Auto-sort job error: {e}")
