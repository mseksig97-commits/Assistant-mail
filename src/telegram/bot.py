import os
import asyncio
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from src.agent.email_manager import EmailManager
from src.utils.logger import setup_logger

logger = setup_logger("telegram_bot")

# State stored in bot_data for simplicity (single-user bot)
PENDING_REPLY_KEY = "pending_reply"
LAST_EMAILS_KEY = "last_emails"


def _allowed(update: Update) -> bool:
    allowed_id = os.getenv("TELEGRAM_ALLOWED_USER_ID")
    if not allowed_id:
        return True
    return str(update.effective_user.id) == str(allowed_id)


def _unauthorized_msg() -> str:
    return "⛔ Accès non autorisé."


class MailBot:
    def __init__(self, manager: EmailManager):
        self.manager = manager
        self.app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
        self._register_handlers()

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("aide", self.cmd_help))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("trier", self.cmd_sort))
        self.app.add_handler(CommandHandler("resume", self.cmd_summary))
        self.app.add_handler(CommandHandler("emails", self.cmd_list_emails))
        self.app.add_handler(CommandHandler("recherche", self.cmd_search))
        self.app.add_handler(CommandHandler("repondre", self.cmd_reply))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    # ─── Commands ────────────────────────────────────────────────────────────

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text(_unauthorized_msg())
            return
        await update.message.reply_text(
            "👋 Bonjour ! Je suis votre assistant email IA.\n\n"
            "Voici ce que je peux faire :\n"
            "• /trier — Trier et analyser tous vos emails\n"
            "• /resume — Résumé quotidien de vos emails\n"
            "• /emails — Voir les derniers emails importants\n"
            "• /recherche <terme> — Rechercher des emails\n"
            "• /repondre <id> — Rédiger une réponse\n"
            "• /aide — Afficher l'aide\n\n"
            "Vous pouvez aussi m'écrire librement pour me donner des instructions !"
        )

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text(_unauthorized_msg())
            return
        await update.message.reply_text(
            "📖 *Commandes disponibles*\n\n"
            "/trier — Analyse et classe tous vos emails (Gmail + 2×Outlook)\n"
            "/resume — Génère le résumé quotidien\n"
            "/emails [n] — Liste les n derniers emails importants (défaut: 10)\n"
            "/recherche <terme> — Recherche dans vos emails\n"
            "/repondre <id_email> — Rédige une réponse avec IA\n"
            "/aide — Cette aide\n\n"
            "💬 Écrivez librement pour poser des questions ou donner des instructions.",
            parse_mode="Markdown",
        )

    async def cmd_sort(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text(_unauthorized_msg())
            return
        msg = await update.message.reply_text("⏳ Analyse et tri de vos emails en cours... (peut prendre 1-2 min)")

        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.analyze_and_sort(30)
            )
            ctx.bot_data[LAST_EMAILS_KEY] = results

            # Build summary stats
            cats: dict[str, int] = {}
            events_count = 0
            for e in results:
                cat = e.get("category", "other")
                cats[cat] = cats.get(cat, 0) + 1
                events_count += len(e.get("events", []))

            lines = [f"✅ *{len(results)} emails analysés et triés*\n"]
            for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
                label = {
                    "urgent": "⚡ Urgent", "important": "⭐ Important", "work": "💼 Travail",
                    "personal": "👤 Personnel", "finance": "💰 Finance", "newsletter": "📰 Newsletter",
                    "spam": "🗑️ Spam", "social": "🌐 Social", "information": "ℹ️ Info", "other": "📁 Autre",
                }.get(cat, cat)
                lines.append(f"  {label}: {count}")
            if events_count:
                lines.append(f"\n📅 {events_count} événement(s) ajouté(s) au calendrier")

            await msg.edit_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"cmd_sort error: {e}")
            await msg.edit_text(f"❌ Erreur lors du tri : {e}")

    async def cmd_summary(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text(_unauthorized_msg())
            return
        msg = await update.message.reply_text("⏳ Génération du résumé en cours...")
        try:
            summary = await asyncio.get_event_loop().run_in_executor(
                None, self.manager.generate_daily_summary
            )
            # Split if too long for Telegram (4096 chars limit)
            if len(summary) <= 4096:
                await msg.edit_text(summary)
            else:
                await msg.delete()
                for chunk in [summary[i:i+4000] for i in range(0, len(summary), 4000)]:
                    await update.message.reply_text(chunk)
        except Exception as e:
            logger.error(f"cmd_summary error: {e}")
            await msg.edit_text(f"❌ Erreur : {e}")

    async def cmd_list_emails(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text(_unauthorized_msg())
            return
        args = ctx.args
        n = int(args[0]) if args and args[0].isdigit() else 10

        msg = await update.message.reply_text("⏳ Récupération des emails...")
        try:
            emails = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.fetch_all_emails(max_per_account=n)
            )
            ctx.bot_data[LAST_EMAILS_KEY] = emails

            # Filter to show only importance >= 3 unless very few results
            lines = [f"📬 *{len(emails)} emails récupérés*\n"]
            for i, e in enumerate(emails[:n]):
                acc_icon = {"gmail": "📧", "outlook1": "📨", "outlook2": "📩"}.get(e.get("account"), "✉️")
                lines.append(
                    f"{acc_icon} `[{i}]` *{e.get('subject', '(pas de sujet)')[:50]}*\n"
                    f"    De : {e.get('from', '')[:40]}\n"
                )

            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:4000] + "\n…"
            await msg.edit_text(text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"cmd_list_emails error: {e}")
            await msg.edit_text(f"❌ Erreur : {e}")

    async def cmd_search(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text(_unauthorized_msg())
            return
        if not ctx.args:
            await update.message.reply_text("Usage : /recherche <terme>")
            return
        query = " ".join(ctx.args)
        msg = await update.message.reply_text(f"🔍 Recherche de « {query} »...")
        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.search_emails(query)
            )
            if not results:
                await msg.edit_text("Aucun email trouvé.")
                return
            lines = [f"🔍 *{len(results)} résultat(s) pour « {query} »*\n"]
            for i, e in enumerate(results[:10]):
                lines.append(f"`[{i}]` *{e.get('subject','')[:50]}* — {e.get('from','')[:30]}")
            await msg.edit_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"cmd_search error: {e}")
            await msg.edit_text(f"❌ Erreur : {e}")

    async def cmd_reply(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text(_unauthorized_msg())
            return
        emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
        if not ctx.args or not ctx.args[0].isdigit():
            await update.message.reply_text("Usage : /repondre <index>\nUtilisez /emails pour voir les index.")
            return
        idx = int(ctx.args[0])
        if idx >= len(emails):
            await update.message.reply_text(f"Index invalide. Max : {len(emails)-1}")
            return

        email = emails[idx]
        instructions = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else ""
        msg = await update.message.reply_text("⏳ Rédaction de la réponse...")
        try:
            draft = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.draft_reply(email, instructions)
            )
            ctx.bot_data[PENDING_REPLY_KEY] = {"email": email, "draft": draft}

            keyboard = [
                [
                    InlineKeyboardButton("✅ Envoyer", callback_data="send_reply"),
                    InlineKeyboardButton("✏️ Modifier", callback_data="edit_reply"),
                    InlineKeyboardButton("❌ Annuler", callback_data="cancel_reply"),
                ]
            ]
            await msg.edit_text(
                f"📝 *Brouillon de réponse à :* {email.get('from','')}\n"
                f"*Sujet :* Re: {email.get('subject','')}\n\n"
                f"---\n{draft}\n---",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"cmd_reply error: {e}")
            await msg.edit_text(f"❌ Erreur : {e}")

    # ─── Callbacks ───────────────────────────────────────────────────────────

    async def handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "send_reply":
            pending = ctx.bot_data.get(PENDING_REPLY_KEY)
            if not pending:
                await query.edit_message_text("❌ Aucun brouillon en attente.")
                return
            success = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.send_reply(pending["email"], pending["draft"])
            )
            ctx.bot_data.pop(PENDING_REPLY_KEY, None)
            if success:
                await query.edit_message_text("✅ Réponse envoyée !")
            else:
                await query.edit_message_text("❌ Échec de l'envoi. Vérifiez les logs.")

        elif data == "edit_reply":
            await query.edit_message_text(
                "✏️ Envoyez-moi votre correction ou instructions supplémentaires, "
                "je régénérerai le brouillon."
            )
            ctx.user_data["mode"] = "editing_reply"

        elif data == "cancel_reply":
            ctx.bot_data.pop(PENDING_REPLY_KEY, None)
            await query.edit_message_text("❌ Réponse annulée.")

    # ─── Free text handler ───────────────────────────────────────────────────

    async def handle_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text(_unauthorized_msg())
            return

        text = update.message.text.strip()
        mode = ctx.user_data.get("mode")

        if mode == "editing_reply":
            ctx.user_data.pop("mode", None)
            pending = ctx.bot_data.get(PENDING_REPLY_KEY)
            if not pending:
                await update.message.reply_text("Aucun brouillon en cours.")
                return
            msg = await update.message.reply_text("⏳ Régénération du brouillon...")
            try:
                draft = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.manager.draft_reply(pending["email"], text)
                )
                ctx.bot_data[PENDING_REPLY_KEY]["draft"] = draft
                keyboard = [[
                    InlineKeyboardButton("✅ Envoyer", callback_data="send_reply"),
                    InlineKeyboardButton("✏️ Modifier", callback_data="edit_reply"),
                    InlineKeyboardButton("❌ Annuler", callback_data="cancel_reply"),
                ]]
                await msg.edit_text(
                    f"📝 *Nouveau brouillon :*\n\n---\n{draft}\n---",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
            except Exception as e:
                await msg.edit_text(f"❌ Erreur : {e}")
            return

        # General chat
        msg = await update.message.reply_text("💭 Réflexion en cours...")
        try:
            # Build context from last emails if available
            context = ""
            last_emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
            if last_emails:
                context = f"Derniers emails récupérés : {len(last_emails)} emails. " \
                          f"Sujets : {', '.join(e.get('subject','') for e in last_emails[:5])}"

            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.chat(text, context)
            )
            await msg.edit_text(response)
        except Exception as e:
            logger.error(f"handle_message error: {e}")
            await msg.edit_text(f"❌ Erreur : {e}")

    # ─── Run ─────────────────────────────────────────────────────────────────

    def run(self):
        logger.info("Starting Telegram bot...")
        self.app.run_polling(drop_pending_updates=True)
