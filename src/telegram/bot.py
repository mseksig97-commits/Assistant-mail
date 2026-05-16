import os
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
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
from src.utils.unsubscribe import extract_unsubscribe, do_http_unsubscribe

logger = setup_logger("telegram_bot")

PENDING_REPLY_KEY = "pending_reply"
LAST_EMAILS_KEY = "last_emails"
CURRENT_ACCOUNT_KEY = "current_account"

_ACC_ICON = {"gmail": "📧", "outlook1": "📨", "outlook2": "📩"}
_ACC_LABEL = {"gmail": "Gmail", "outlook1": "Outlook 1", "outlook2": "Outlook 2"}
_CAT_LABEL = {
    "urgent": "⚡ Urgent", "important": "⭐ Important", "work": "💼 Travail",
    "personal": "👤 Personnel", "finance": "💰 Finance", "newsletter": "📰 Newsletter",
    "spam": "🗑️ Spam", "social": "🌐 Social", "information": "ℹ️ Info", "other": "📁 Autre",
}

# ── Main menu buttons ──────────────────────────────────────────────────────────
BTN_GMAIL = "📧 Gmail"
BTN_OUTLOOK1 = "📨 Outlook 1"
BTN_OUTLOOK2 = "📩 Outlook 2"
BTN_SORT_ALL = "📊 Trier tout"
BTN_SUMMARY = "📋 Résumé"
BTN_HELP = "❓ Aide"

# ── Account sub-menu buttons ───────────────────────────────────────────────────
BTN_EMAILS = "📬 Emails"
BTN_SEARCH = "🔍 Rechercher"
BTN_NEWSLETTERS = "🔕 Newsletters"
BTN_SORT_ACC = "📊 Trier"
BTN_BACK = "↩️ Retour"

_BTN_TO_ACCOUNT = {
    BTN_GMAIL: "gmail",
    BTN_OUTLOOK1: "outlook1",
    BTN_OUTLOOK2: "outlook2",
}


def _allowed(update: Update) -> bool:
    allowed_id = os.getenv("TELEGRAM_ALLOWED_USER_ID")
    if not allowed_id:
        return True
    return str(update.effective_user.id) == str(allowed_id)


def _main_kb() -> ReplyKeyboardMarkup:
    """Level 1 — mailbox selection."""
    return ReplyKeyboardMarkup(
        [
            [BTN_GMAIL, BTN_OUTLOOK1, BTN_OUTLOOK2],
            [BTN_SORT_ALL, BTN_SUMMARY, BTN_HELP],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def _account_kb() -> ReplyKeyboardMarkup:
    """Level 2 — actions on the selected mailbox."""
    return ReplyKeyboardMarkup(
        [
            [BTN_EMAILS, BTN_SEARCH, BTN_NEWSLETTERS],
            [BTN_SORT_ACC, BTN_BACK, BTN_HELP],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def _sep() -> str:
    return "━━━━━━━━━━━━━━━━━━━━"


def _imp_bar(imp) -> str:
    try:
        n = int(imp)
        return "●" * n + "○" * (5 - n) if 1 <= n <= 5 else ""
    except (ValueError, TypeError):
        return ""


def _email_card(i: int, e: dict, show_snippet: bool = True) -> str:
    acc = e.get("account", "")
    icon = _ACC_ICON.get(acc, "✉️")
    subj = e.get("subject", "(sans sujet)")[:55]
    sender = e.get("from", "")[:40]
    date = e.get("date", "")[:16]
    snippet = e.get("snippet", "")[:70]
    cat = _CAT_LABEL.get(e.get("category", ""), "")
    imp_bar = _imp_bar(e.get("importance", ""))

    lines = [f"{icon} `[{i}]` *{subj}*"]
    lines.append(f"    👤 {sender}   🕐 {date}")
    if cat or imp_bar:
        lines.append(f"    {cat}  {imp_bar}")
    if show_snippet and snippet:
        lines.append(f"    _{snippet}_")
    return "\n".join(lines)


def _current_account(ctx: ContextTypes.DEFAULT_TYPE) -> str:
    return ctx.user_data.get(CURRENT_ACCOUNT_KEY, "")


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
        self.app.add_handler(CommandHandler("voir", self.cmd_view_email))
        self.app.add_handler(CommandHandler("desabonner", self.cmd_unsubscribe))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    # ─── Commands ────────────────────────────────────────────────────────────

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text("⛔ Accès non autorisé.")
            return
        ctx.user_data.pop(CURRENT_ACCOUNT_KEY, None)
        await update.message.reply_text(
            "👋 *Bonjour ! Je suis votre assistant email IA.*\n"
            f"{_sep()}\n\n"
            "📬 Sélectionnez une boîte mail pour commencer,\n"
            "ou utilisez les actions globales ci-dessous.",
            parse_mode="Markdown",
            reply_markup=_main_kb(),
        )

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text("⛔ Accès non autorisé.")
            return
        account = _current_account(ctx)
        acc_label = f" — {_ACC_ICON.get(account,'')} {_ACC_LABEL.get(account,'')}" if account else ""
        await update.message.reply_text(
            f"❓ *Aide{acc_label}*\n"
            f"{_sep()}\n\n"
            "*Menu principal :*\n"
            "📧 Gmail / 📨 Outlook 1 / 📩 Outlook 2 — Sélectionner une boîte\n"
            "📊 Trier tout — Analyser tous les emails\n"
            "📋 Résumé — Résumé quotidien IA\n\n"
            "*Sous-menu de la boîte :*\n"
            "📬 Emails — Voir les derniers emails\n"
            "🔍 Rechercher — Chercher dans la boîte\n"
            "🔕 Newsletters — Gérer les newsletters\n"
            "📊 Trier — Analyser cette boîte\n"
            "↩️ Retour — Retour au menu principal\n\n"
            f"{_sep()}\n\n"
            "*Commandes texte :*\n"
            "`/voir <n>` — Lire un email complet\n"
            "`/repondre <n>` — Rédiger une réponse IA\n"
            "`/desabonner <n>` — Se désabonner\n"
            "`/recherche <terme>` — Recherche avancée\n\n"
            "💬 Écrivez librement pour poser des questions !",
            parse_mode="Markdown",
            reply_markup=_account_kb() if account else _main_kb(),
        )

    async def cmd_sort(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text("⛔ Accès non autorisé.")
            return
        account = _current_account(ctx)
        acc_label = f"{_ACC_ICON.get(account,'')} {_ACC_LABEL.get(account,'')}" if account else "tous les comptes"
        msg = await update.message.reply_text(f"⏳ Analyse de *{acc_label}* en cours… (1-2 min)", parse_mode="Markdown")

        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.analyze_and_sort(30)
            )
            # Filter by account if in account context
            if account:
                results = [e for e in results if e.get("account") == account]
            ctx.bot_data[LAST_EMAILS_KEY] = results

            cats: dict[str, int] = {}
            events_count = 0
            for e in results:
                cat = e.get("category", "other")
                cats[cat] = cats.get(cat, 0) + 1
                events_count += len(e.get("events", []))

            lines = [
                f"✅ *{len(results)} emails analysés — {acc_label}*",
                _sep(),
                "",
            ]
            for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
                label = _CAT_LABEL.get(cat, f"📁 {cat}")
                lines.append(f"{label} — *{count}*")
            if events_count:
                lines.append("")
                lines.append(f"📅 *{events_count}* événement(s) ajouté(s) au calendrier")
            lines.append("")
            lines.append("💡 Appuyez sur *Emails* pour voir la liste.")

            await msg.edit_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"cmd_sort error: {e}")
            await msg.edit_text(f"❌ Erreur lors du tri : {e}")

    async def cmd_summary(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text("⛔ Accès non autorisé.")
            return
        msg = await update.message.reply_text("⏳ Génération du résumé en cours…")
        try:
            summary = await asyncio.get_event_loop().run_in_executor(
                None, self.manager.generate_daily_summary
            )
            account = _current_account(ctx)
            kb = _account_kb() if account else _main_kb()
            if len(summary) <= 4096:
                await msg.edit_text(summary, reply_markup=kb)
            else:
                await msg.delete()
                chunks = [summary[i:i+4000] for i in range(0, len(summary), 4000)]
                for j, chunk in enumerate(chunks):
                    await update.message.reply_text(chunk, reply_markup=kb if j == len(chunks) - 1 else None)
        except Exception as e:
            logger.error(f"cmd_summary error: {e}")
            await msg.edit_text(f"❌ Erreur : {e}")

    async def cmd_list_emails(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text("⛔ Accès non autorisé.")
            return
        args = ctx.args
        n = int(args[0]) if args and args[0].isdigit() else 15
        account = _current_account(ctx)
        acc_label = f"{_ACC_ICON.get(account,'')} {_ACC_LABEL.get(account,'')}" if account else "tous les comptes"

        msg = await update.message.reply_text(f"⏳ Récupération des emails — *{acc_label}*…", parse_mode="Markdown")
        try:
            if account:
                emails = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.manager.fetch_account_emails(account, max_results=n)
                )
            else:
                emails = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.manager.fetch_all_emails(max_per_account=n)
                )
            ctx.bot_data[LAST_EMAILS_KEY] = emails

            lines = [
                f"📬 *{len(emails)} emails — {acc_label}*",
                _sep(),
                "",
            ]
            for i, e in enumerate(emails[:n]):
                lines.append(_email_card(i, e, show_snippet=False))
                lines.append("")

            lines.append(_sep())
            lines.append("💡 `/voir <n>` pour lire • `/repondre <n>` pour répondre")

            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:4000] + "\n…"
            await msg.edit_text(text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"cmd_list_emails error: {e}")
            await msg.edit_text(f"❌ Erreur : {e}")

    async def cmd_search(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text("⛔ Accès non autorisé.")
            return
        if not ctx.args:
            account = _current_account(ctx)
            acc_label = f" dans {_ACC_ICON.get(account,'')} {_ACC_LABEL.get(account,'')}" if account else ""
            await update.message.reply_text(
                f"🔍 *Recherche d'emails{acc_label}*\n"
                f"{_sep()}\n\n"
                "Usage : `/recherche <terme>`\n\n"
                "Exemples :\n"
                "  `/recherche facture`\n"
                "  `/recherche newsletter`\n"
                "  `/recherche jean@example.com`",
                parse_mode="Markdown",
                reply_markup=_account_kb() if account else _main_kb(),
            )
            return
        query = " ".join(ctx.args)
        await self._do_search(update, ctx, query)

    async def _do_search(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE, query: str):
        account = _current_account(ctx)
        acc_label = f" dans {_ACC_ICON.get(account,'')} {_ACC_LABEL.get(account,'')}" if account else ""
        msg = await update.message.reply_text(f"🔍 Recherche de « {query} »{acc_label}…")
        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.search_emails(query, account=account)
            )
            if not results:
                await msg.edit_text(
                    f"🔍 Aucun résultat pour *« {query} »*{acc_label}\n\nEssayez un autre terme.",
                    parse_mode="Markdown",
                )
                return

            ctx.bot_data[LAST_EMAILS_KEY] = results
            lines = [
                f"🔍 *{len(results)} résultat(s) pour « {query} »{acc_label}*",
                _sep(),
                "",
            ]
            for i, e in enumerate(results[:15]):
                lines.append(_email_card(i, e, show_snippet=True))
                lines.append("")

            lines.append(_sep())
            lines.append("💡 `/voir <n>` pour lire • `/repondre <n>` pour répondre • `/desabonner <n>` pour se désabonner")

            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:4000] + "\n…"
            await msg.edit_text(text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"_do_search error: {e}")
            await msg.edit_text(f"❌ Erreur : {e}")

    async def cmd_view_email(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text("⛔ Accès non autorisé.")
            return
        emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
        if not ctx.args or not ctx.args[0].isdigit():
            account = _current_account(ctx)
            await update.message.reply_text(
                "Usage : `/voir <index>`\nUtilisez *Emails* ou *Rechercher* d'abord.",
                parse_mode="Markdown",
                reply_markup=_account_kb() if account else _main_kb(),
            )
            return
        idx = int(ctx.args[0])
        if idx >= len(emails):
            account = _current_account(ctx)
            await update.message.reply_text(
                f"❌ Index invalide. Max : {len(emails)-1}",
                reply_markup=_account_kb() if account else _main_kb(),
            )
            return

        e = emails[idx]
        acc_icon = _ACC_ICON.get(e.get("account", ""), "✉️")
        body = e.get("body", e.get("snippet", "(pas de contenu)"))
        body_preview = body[:800] + ("…" if len(body) > 800 else "")

        cat = _CAT_LABEL.get(e.get("category", ""), "")
        imp_bar = _imp_bar(e.get("importance", ""))

        text = (
            f"{acc_icon} *Email `[{idx}]`*\n"
            f"{_sep()}\n"
            f"*De :* {e.get('from','')}\n"
            f"*À :* {e.get('to','')}\n"
            f"*Date :* {e.get('date','')[:25]}\n"
            f"*Sujet :* {e.get('subject','')}\n"
        )
        if cat or imp_bar:
            text += f"*Catégorie :* {cat}  {imp_bar}\n"
        text += f"{_sep()}\n\n{body_preview}"

        unsub_info = extract_unsubscribe(e)
        buttons = [InlineKeyboardButton("✏️ Répondre", callback_data=f"reply_{idx}")]
        if unsub_info["url"] or unsub_info["mailto"]:
            buttons.append(InlineKeyboardButton("🔕 Se désabonner", callback_data=f"unsub_{idx}"))

        if len(text) > 4000:
            text = text[:4000] + "\n…"
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup([buttons]),
            parse_mode="Markdown",
        )

    async def cmd_reply(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text("⛔ Accès non autorisé.")
            return
        emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
        account = _current_account(ctx)
        if not ctx.args or not ctx.args[0].isdigit():
            await update.message.reply_text(
                "Usage : `/repondre <index>`\nUtilisez *Emails* pour voir les index.",
                parse_mode="Markdown",
                reply_markup=_account_kb() if account else _main_kb(),
            )
            return
        idx = int(ctx.args[0])
        if idx >= len(emails):
            await update.message.reply_text(
                f"❌ Index invalide. Max : {len(emails)-1}",
                reply_markup=_account_kb() if account else _main_kb(),
            )
            return

        email = emails[idx]
        instructions = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else ""
        msg = await update.message.reply_text("⏳ Rédaction de la réponse…")
        try:
            draft = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.draft_reply(email, instructions)
            )
            ctx.bot_data[PENDING_REPLY_KEY] = {"email": email, "draft": draft}
            keyboard = [[
                InlineKeyboardButton("✅ Envoyer", callback_data="send_reply"),
                InlineKeyboardButton("✏️ Modifier", callback_data="edit_reply"),
                InlineKeyboardButton("❌ Annuler", callback_data="cancel_reply"),
            ]]
            await msg.edit_text(
                f"📝 *Brouillon — réponse à :*\n"
                f"👤 {email.get('from','')}\n"
                f"📌 Re: {email.get('subject','')}\n"
                f"{_sep()}\n\n"
                f"{draft}\n\n"
                f"{_sep()}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"cmd_reply error: {e}")
            await msg.edit_text(f"❌ Erreur : {e}")

    async def cmd_unsubscribe(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text("⛔ Accès non autorisé.")
            return
        emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
        account = _current_account(ctx)
        if not ctx.args or not ctx.args[0].isdigit():
            await update.message.reply_text(
                "Usage : `/desabonner <index>`\n"
                "Utilisez *Newsletters* ou *Emails* pour trouver l'index.",
                parse_mode="Markdown",
                reply_markup=_account_kb() if account else _main_kb(),
            )
            return
        idx = int(ctx.args[0])
        if idx >= len(emails):
            await update.message.reply_text(
                f"❌ Index invalide. Max : {len(emails)-1}",
                reply_markup=_account_kb() if account else _main_kb(),
            )
            return

        email = emails[idx]
        info = extract_unsubscribe(email)

        if not info["url"] and not info["mailto"]:
            await update.message.reply_text(
                f"⚠️ *Aucun lien de désabonnement trouvé*\n\n"
                f"Email : *{email.get('subject', '')}*\n\n"
                f"Utilisez `/voir {idx}` pour chercher manuellement.",
                parse_mode="Markdown",
                reply_markup=_account_kb() if account else _main_kb(),
            )
            return

        method_label = {
            "one_click": "✅ Désabonnement en 1 clic (RFC 8058)",
            "http": "🌐 Lien de désabonnement web",
            "mailto": "📧 Email de désabonnement",
        }.get(info["method"], "")

        target = info["url"] or info["mailto"]
        ctx.user_data["pending_unsub"] = {"email": email, "info": info, "idx": idx}

        keyboard = [[
            InlineKeyboardButton("✅ Confirmer le désabonnement", callback_data="confirm_unsub"),
            InlineKeyboardButton("❌ Annuler", callback_data="cancel_unsub"),
        ]]
        await update.message.reply_text(
            f"🔕 *Désabonnement*\n"
            f"{_sep()}\n"
            f"*De :* {email.get('from', '')}\n"
            f"*Sujet :* {email.get('subject', '')}\n\n"
            f"{method_label}\n"
            f"`{target[:80]}`\n\n"
            f"Confirmes-tu le désabonnement ?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

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
                await query.edit_message_text("✅ *Réponse envoyée avec succès !*", parse_mode="Markdown")
            else:
                await query.edit_message_text("❌ Échec de l'envoi. Vérifiez les logs.")

        elif data == "edit_reply":
            await query.edit_message_text(
                "✏️ *Modification du brouillon*\n\n"
                "Envoyez vos instructions ou corrections, je régénèrerai le brouillon.",
                parse_mode="Markdown",
            )
            ctx.user_data["mode"] = "editing_reply"

        elif data == "cancel_reply":
            ctx.bot_data.pop(PENDING_REPLY_KEY, None)
            await query.edit_message_text("❌ Réponse annulée.")

        elif data == "confirm_unsub":
            pending = ctx.user_data.pop("pending_unsub", None)
            if not pending:
                await query.edit_message_text("❌ Aucune demande en cours.")
                return
            info = pending["info"]
            email = pending["email"]
            await query.edit_message_text("⏳ Désabonnement en cours…")
            try:
                if info["url"]:
                    one_click = info["method"] == "one_click"
                    success, msg_txt = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: do_http_unsubscribe(info["url"], one_click)
                    )
                    if success:
                        await query.edit_message_text(
                            f"✅ *Désabonné avec succès !*\n"
                            f"{_sep()}\n"
                            f"*De :* {email.get('from','')}\n"
                            f"{msg_txt}",
                            parse_mode="Markdown",
                        )
                    else:
                        await query.edit_message_text(
                            f"⚠️ *Le désabonnement a échoué.*\n{msg_txt}\n\n"
                            f"Essaie manuellement : `/voir {pending['idx']}`",
                            parse_mode="Markdown",
                        )
                elif info["mailto"]:
                    addr = info["mailto"].replace("mailto:", "").split("?")[0]
                    subject_part = "unsubscribe"
                    if "subject=" in info["mailto"]:
                        subject_part = info["mailto"].split("subject=")[1].split("&")[0]
                    account = email.get("account", "gmail")
                    if account == "gmail":
                        ok = await asyncio.get_event_loop().run_in_executor(
                            None, lambda: self.manager.gmail.send_email(addr, subject_part, "unsubscribe")
                        )
                    elif account == "outlook1":
                        ok = await asyncio.get_event_loop().run_in_executor(
                            None, lambda: self.manager.outlook1.send_email(addr, subject_part, "unsubscribe")
                        )
                    else:
                        ok = await asyncio.get_event_loop().run_in_executor(
                            None, lambda: self.manager.outlook2.send_email(addr, subject_part, "unsubscribe")
                        )
                    if ok:
                        await query.edit_message_text(
                            f"✅ *Email de désabonnement envoyé !*\n\nÀ : `{addr}`",
                            parse_mode="Markdown",
                        )
                    else:
                        await query.edit_message_text("❌ Échec de l'envoi de l'email de désabonnement.")
            except Exception as e:
                await query.edit_message_text(f"❌ Erreur : {e}")

        elif data == "cancel_unsub":
            ctx.user_data.pop("pending_unsub", None)
            await query.edit_message_text("❌ Désabonnement annulé.")

        elif data.startswith("unsub_"):
            idx = int(data.split("_")[1])
            emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
            if idx >= len(emails):
                await query.edit_message_text("❌ Email introuvable.")
                return
            email = emails[idx]
            info = extract_unsubscribe(email)
            if not info["url"] and not info["mailto"]:
                await query.edit_message_text("⚠️ Aucun lien de désabonnement trouvé dans cet email.")
                return
            ctx.user_data["pending_unsub"] = {"email": email, "info": info, "idx": idx}
            target = info["url"] or info["mailto"] or ""
            method_label = {"one_click": "✅ 1 clic", "http": "🌐 Lien web", "mailto": "📧 Email"}.get(info["method"], "")
            keyboard = [[
                InlineKeyboardButton("✅ Confirmer", callback_data="confirm_unsub"),
                InlineKeyboardButton("❌ Annuler", callback_data="cancel_unsub"),
            ]]
            await query.edit_message_text(
                f"🔕 *Désabonnement de :*\n"
                f"👤 {email.get('from','')}\n"
                f"{method_label} — `{target[:80]}`\n\n"
                f"Confirmer ?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        elif data.startswith("reply_"):
            idx = int(data.split("_")[1])
            emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
            if idx >= len(emails):
                await query.edit_message_text("❌ Email introuvable.")
                return
            email = emails[idx]
            await query.edit_message_text("⏳ Rédaction de la réponse…")
            try:
                draft = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.manager.draft_reply(email, "")
                )
                ctx.bot_data[PENDING_REPLY_KEY] = {"email": email, "draft": draft}
                keyboard = [[
                    InlineKeyboardButton("✅ Envoyer", callback_data="send_reply"),
                    InlineKeyboardButton("✏️ Modifier", callback_data="edit_reply"),
                    InlineKeyboardButton("❌ Annuler", callback_data="cancel_reply"),
                ]]
                await query.edit_message_text(
                    f"📝 *Brouillon — réponse à :*\n"
                    f"👤 {email.get('from','')}\n"
                    f"📌 Re: {email.get('subject','')}\n"
                    f"{_sep()}\n\n"
                    f"{draft}\n\n"
                    f"{_sep()}",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
            except Exception as e:
                await query.edit_message_text(f"❌ Erreur : {e}")

    # ─── Free text / button handler ──────────────────────────────────────────

    async def handle_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text("⛔ Accès non autorisé.")
            return

        text = update.message.text.strip()
        mode = ctx.user_data.get("mode")

        # ── Editing reply draft ──────────────────────────────────────────────
        if mode == "editing_reply":
            ctx.user_data.pop("mode", None)
            pending = ctx.bot_data.get(PENDING_REPLY_KEY)
            if not pending:
                account = _current_account(ctx)
                await update.message.reply_text("Aucun brouillon en cours.", reply_markup=_account_kb() if account else _main_kb())
                return
            msg = await update.message.reply_text("⏳ Régénération du brouillon…")
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
                    f"📝 *Nouveau brouillon :*\n"
                    f"{_sep()}\n\n"
                    f"{draft}\n\n"
                    f"{_sep()}",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
            except Exception as e:
                await msg.edit_text(f"❌ Erreur : {e}")
            return

        # ── Waiting for search term ──────────────────────────────────────────
        if mode == "waiting_search":
            ctx.user_data.pop("mode", None)
            await self._do_search(update, ctx, text)
            return

        # ── Level 1 — Mailbox selection ──────────────────────────────────────
        if text in _BTN_TO_ACCOUNT:
            account = _BTN_TO_ACCOUNT[text]
            ctx.user_data[CURRENT_ACCOUNT_KEY] = account
            acc_icon = _ACC_ICON[account]
            acc_label = _ACC_LABEL[account]

            msg = await update.message.reply_text(
                f"{acc_icon} *{acc_label}*\n"
                f"{_sep()}\n\n"
                f"Récupération des emails…",
                parse_mode="Markdown",
                reply_markup=_account_kb(),
            )
            try:
                emails = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.manager.fetch_account_emails(account, max_results=15)
                )
                ctx.bot_data[LAST_EMAILS_KEY] = emails

                lines = [
                    f"{acc_icon} *{acc_label} — {len(emails)} emails*",
                    _sep(),
                    "",
                ]
                for i, e in enumerate(emails[:15]):
                    lines.append(_email_card(i, e, show_snippet=False))
                    lines.append("")

                lines.append(_sep())
                lines.append("💡 `/voir <n>` pour lire • `/repondre <n>` pour répondre")

                text_out = "\n".join(lines)
                if len(text_out) > 4000:
                    text_out = text_out[:4000] + "\n…"
                await msg.edit_text(text_out, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"mailbox select error: {e}")
                await msg.edit_text(f"❌ Erreur : {e}")
            return

        # ── Level 2 — Account sub-menu actions ───────────────────────────────
        account = _current_account(ctx)

        if text == BTN_EMAILS:
            await self.cmd_list_emails(update, ctx)
            return

        if text == BTN_SEARCH:
            ctx.user_data["mode"] = "waiting_search"
            acc_label = f"{_ACC_ICON.get(account,'')} {_ACC_LABEL.get(account,'')}" if account else "tous les comptes"
            await update.message.reply_text(
                f"🔍 *Recherche — {acc_label}*\n\nEntrez votre terme de recherche :",
                parse_mode="Markdown",
                reply_markup=_account_kb() if account else _main_kb(),
            )
            return

        if text == BTN_NEWSLETTERS:
            await self._do_search(update, ctx, "newsletter")
            return

        if text == BTN_SORT_ACC:
            await self.cmd_sort(update, ctx)
            return

        if text == BTN_BACK:
            ctx.user_data.pop(CURRENT_ACCOUNT_KEY, None)
            await update.message.reply_text(
                "↩️ *Menu principal*\n\nSélectionnez une boîte mail :",
                parse_mode="Markdown",
                reply_markup=_main_kb(),
            )
            return

        if text == BTN_SORT_ALL:
            ctx.user_data.pop(CURRENT_ACCOUNT_KEY, None)
            await self.cmd_sort(update, ctx)
            return

        if text == BTN_SUMMARY:
            await self.cmd_summary(update, ctx)
            return

        if text == BTN_HELP:
            await self.cmd_help(update, ctx)
            return

        # ── General AI chat ──────────────────────────────────────────────────
        msg = await update.message.reply_text("💭 Réflexion en cours…")
        try:
            last_emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
            if not last_emails:
                await msg.edit_text("📥 Récupération de vos emails en cours…")
                if account:
                    last_emails = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: self.manager.fetch_account_emails(account, max_results=50)
                    )
                else:
                    last_emails = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: self.manager.fetch_all_emails(max_per_account=50)
                    )
                ctx.bot_data[LAST_EMAILS_KEY] = last_emails
                await msg.edit_text("💭 Réflexion en cours…")

            context = ""
            if last_emails:
                acc_ctx = f"Boîte active : {_ACC_LABEL.get(account, 'toutes')}\n" if account else ""
                lines = [f"{acc_ctx}{len(last_emails)} emails disponibles :"]
                for i, e in enumerate(last_emails):
                    lines.append(
                        f"[{i}] {e.get('account','')} | cat={e.get('category','')} imp={e.get('importance','')} "
                        f"| De: {e.get('from','')[:40]} | Sujet: {e.get('subject','')}"
                    )
                context = "\n".join(lines)

            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.chat(update.message.text.strip(), context)
            )
            kb = _account_kb() if account else _main_kb()
            await msg.edit_text(response, reply_markup=kb)
        except Exception as e:
            logger.error(f"handle_message error: {e}")
            await msg.edit_text(f"❌ Erreur : {e}")

    # ─── Run ─────────────────────────────────────────────────────────────────

    def run(self):
        logger.info("Starting Telegram bot...")
        self.app.run_polling(drop_pending_updates=True)
