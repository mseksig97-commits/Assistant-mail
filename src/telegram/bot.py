import os
import asyncio

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
from src.utils.unsubscribe import extract_unsubscribe, do_http_unsubscribe

logger = setup_logger("telegram_bot")

PENDING_REPLY_KEY = "pending_reply"
LAST_EMAILS_KEY = "last_emails"
CURRENT_ACCOUNT_KEY = "current_account"
NAV_MSG_KEY = "nav_msg_id"

_ACC_ICON = {"gmail": "📧", "outlook1": "📨", "outlook2": "📩"}
_ACC_LABEL = {"gmail": "Gmail", "outlook1": "Outlook 1", "outlook2": "Outlook 2"}
_CAT_LABEL = {
    "urgent": "⚡ Urgent", "important": "⭐ Important", "work": "💼 Travail",
    "personal": "👤 Personnel", "finance": "💰 Finance", "newsletter": "📰 Newsletter",
    "spam": "🗑️ Spam", "social": "🌐 Social", "information": "ℹ️ Info", "other": "📁 Autre",
}

# Nav callback data constants
CB_NAV_GMAIL = "nav_gmail"
CB_NAV_OUTLOOK1 = "nav_outlook1"
CB_NAV_OUTLOOK2 = "nav_outlook2"
CB_NAV_BACK = "nav_back"
CB_ACT_EMAILS = "act_emails"
CB_ACT_SEARCH = "act_search"
CB_ACT_NEWSLETTERS = "act_newsletters"
CB_ACT_SORT_ACC = "act_sort_acc"
CB_ACT_SORT_ALL = "act_sort_all"
CB_ACT_SUMMARY = "act_summary"
CB_ACT_HELP = "act_help"

_CB_TO_ACCOUNT = {
    CB_NAV_GMAIL: "gmail",
    CB_NAV_OUTLOOK1: "outlook1",
    CB_NAV_OUTLOOK2: "outlook2",
}


def _allowed(update: Update) -> bool:
    allowed_id = os.getenv("TELEGRAM_ALLOWED_USER_ID")
    if not allowed_id:
        return True
    return str(update.effective_user.id) == str(allowed_id)


def _main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📧 Gmail", callback_data=CB_NAV_GMAIL),
            InlineKeyboardButton("📨 Outlook 1", callback_data=CB_NAV_OUTLOOK1),
            InlineKeyboardButton("📩 Outlook 2", callback_data=CB_NAV_OUTLOOK2),
        ],
        [
            InlineKeyboardButton("📊 Trier tout", callback_data=CB_ACT_SORT_ALL),
            InlineKeyboardButton("📋 Résumé", callback_data=CB_ACT_SUMMARY),
            InlineKeyboardButton("❓ Aide", callback_data=CB_ACT_HELP),
        ],
    ])


def _account_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📬 Emails", callback_data=CB_ACT_EMAILS),
            InlineKeyboardButton("🔍 Rechercher", callback_data=CB_ACT_SEARCH),
            InlineKeyboardButton("🔕 Newsletters", callback_data=CB_ACT_NEWSLETTERS),
        ],
        [
            InlineKeyboardButton("📊 Trier", callback_data=CB_ACT_SORT_ACC),
            InlineKeyboardButton("↩️ Retour", callback_data=CB_NAV_BACK),
            InlineKeyboardButton("❓ Aide", callback_data=CB_ACT_HELP),
        ],
    ])


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


def _short_sender(e: dict) -> str:
    sender = e.get("from", "")
    if "<" in sender:
        name = sender.split("<")[0].strip().strip('"').strip()
        if name:
            return name[:22]
    if "@" in sender:
        return sender.split("@")[0][:22]
    return sender[:22] or "?"


def _unsub_keyboard(emails: list[dict]) -> "InlineKeyboardMarkup | None":
    rows = []
    for i, e in enumerate(emails):
        info = extract_unsubscribe(e)
        if info["url"] or info["mailto"]:
            label = f"🔕 [{i}] {_short_sender(e)}"[:30]
            rows.append([InlineKeyboardButton(label, callback_data=f"unsub_{i}")])
    return InlineKeyboardMarkup(rows) if rows else None


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

    # ─── Nav message helpers ──────────────────────────────────────────────────

    async def _set_nav(self, chat_id: int, ctx: ContextTypes.DEFAULT_TYPE,
                       text: str, keyboard: InlineKeyboardMarkup):
        """Send or edit the persistent nav/menu message."""
        nav_id = ctx.chat_data.get(NAV_MSG_KEY)
        if nav_id:
            try:
                await ctx.bot.edit_message_text(
                    chat_id=chat_id, message_id=nav_id,
                    text=text, parse_mode="Markdown", reply_markup=keyboard,
                )
                return
            except Exception:
                pass
        msg = await ctx.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=keyboard,
        )
        ctx.chat_data[NAV_MSG_KEY] = msg.message_id

    async def _nav_loading(self, chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, text: str):
        """Edit nav message to a loading state (no keyboard)."""
        nav_id = ctx.chat_data.get(NAV_MSG_KEY)
        if nav_id:
            try:
                await ctx.bot.edit_message_text(
                    chat_id=chat_id, message_id=nav_id,
                    text=text, parse_mode="Markdown",
                )
                return
            except Exception:
                pass
        msg = await ctx.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        ctx.chat_data[NAV_MSG_KEY] = msg.message_id

    async def _restore_nav(self, chat_id: int, ctx: ContextTypes.DEFAULT_TYPE):
        """Restore nav message to the appropriate keyboard for the current state."""
        account = ctx.user_data.get(CURRENT_ACCOUNT_KEY, "")
        if account:
            acc_icon = _ACC_ICON[account]
            acc_label = _ACC_LABEL[account]
            await self._set_nav(chat_id, ctx, f"{acc_icon} *{acc_label}* — Menu", _account_kb())
        else:
            await self._set_nav(chat_id, ctx, "📬 *Assistant Email* — Menu principal", _main_kb())

    # ─── Commands ────────────────────────────────────────────────────────────

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            await update.message.reply_text("⛔ Accès non autorisé.")
            return
        ctx.user_data.pop(CURRENT_ACCOUNT_KEY, None)
        ctx.chat_data.pop(NAV_MSG_KEY, None)
        await self._set_nav(
            update.effective_chat.id, ctx,
            "👋 *Bonjour ! Je suis votre assistant email IA.*\n"
            f"{_sep()}\n\n"
            "📬 Sélectionnez une boîte mail pour commencer,\n"
            "ou utilisez les actions globales ci-dessous.",
            _main_kb(),
        )

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            return
        account = _current_account(ctx)
        acc_label = f" — {_ACC_ICON.get(account, '')} {_ACC_LABEL.get(account, '')}" if account else ""
        await update.message.reply_text(
            f"❓ *Aide{acc_label}*\n"
            f"{_sep()}\n\n"
            "*Navigation (boutons) :*\n"
            "📧/📨/📩 — Sélectionner une boîte\n"
            "📬 Emails — Voir les derniers emails\n"
            "🔍 Rechercher — Chercher dans la boîte\n"
            "🔕 Newsletters — Gérer les newsletters\n"
            "📊 Trier — Analyser les emails\n"
            "↩️ Retour — Retour au menu principal\n\n"
            f"{_sep()}\n\n"
            "*Commandes texte :*\n"
            "`/voir <n>` — Lire un email complet\n"
            "`/repondre <n>` — Rédiger une réponse IA\n"
            "`/desabonner <n>` — Se désabonner\n"
            "`/recherche <terme>` — Recherche avancée\n\n"
            "💬 Écrivez librement pour poser des questions !",
            parse_mode="Markdown",
        )

    async def cmd_sort(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            return
        chat_id = update.effective_chat.id
        account = _current_account(ctx)
        acc_label = f"{_ACC_ICON.get(account, '')} {_ACC_LABEL.get(account, '')}" if account else "tous les comptes"
        await self._nav_loading(chat_id, ctx, f"⏳ Analyse de *{acc_label}* en cours… (1-2 min)")
        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.analyze_and_sort(30)
            )
            if account:
                results = [e for e in results if e.get("account") == account]
            ctx.bot_data[LAST_EMAILS_KEY] = results
            cats: dict[str, int] = {}
            events_count = 0
            for e in results:
                cat = e.get("category", "other")
                cats[cat] = cats.get(cat, 0) + 1
                events_count += len(e.get("events", []))
            lines = [f"✅ *{len(results)} emails analysés — {acc_label}*", _sep(), ""]
            for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
                lines.append(f"{_CAT_LABEL.get(cat, f'📁 {cat}')} — *{count}*")
            if events_count:
                lines.append("")
                lines.append(f"📅 *{events_count}* événement(s) ajouté(s) au calendrier")
            await ctx.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"cmd_sort error: {e}")
            await ctx.bot.send_message(chat_id=chat_id, text=f"❌ Erreur lors du tri : {e}")
        finally:
            await self._restore_nav(chat_id, ctx)

    async def cmd_summary(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            return
        chat_id = update.effective_chat.id
        await self._nav_loading(chat_id, ctx, "⏳ Génération du résumé en cours…")
        try:
            summary = await asyncio.get_event_loop().run_in_executor(
                None, self.manager.generate_daily_summary
            )
            if len(summary) <= 4096:
                await ctx.bot.send_message(chat_id=chat_id, text=summary)
            else:
                for chunk in [summary[i:i + 4000] for i in range(0, len(summary), 4000)]:
                    await ctx.bot.send_message(chat_id=chat_id, text=chunk)
        except Exception as e:
            logger.error(f"cmd_summary error: {e}")
            await ctx.bot.send_message(chat_id=chat_id, text=f"❌ Erreur : {e}")
        finally:
            await self._restore_nav(chat_id, ctx)

    async def cmd_list_emails(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            return
        chat_id = update.effective_chat.id
        args = ctx.args
        n = int(args[0]) if args and args[0].isdigit() else 15
        account = _current_account(ctx)
        acc_label = f"{_ACC_ICON.get(account, '')} {_ACC_LABEL.get(account, '')}" if account else "tous les comptes"
        await self._nav_loading(chat_id, ctx, f"⏳ Récupération des emails — *{acc_label}*…")
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
            lines = [f"📬 *{len(emails)} emails — {acc_label}*", _sep(), ""]
            for i, e in enumerate(emails[:n]):
                lines.append(_email_card(i, e, show_snippet=False))
                lines.append("")
            lines.append(_sep())
            lines.append("💡 `/voir <n>` pour lire • `/repondre <n>` pour répondre")
            unsub_kb = _unsub_keyboard(emails[:n])
            if unsub_kb:
                lines.append("👇 Cliquez sur un bouton ci-dessous pour vous désabonner")
            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:4000] + "\n…"
            await ctx.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=unsub_kb)
        except Exception as e:
            logger.error(f"cmd_list_emails error: {e}")
            await ctx.bot.send_message(chat_id=chat_id, text=f"❌ Erreur : {e}")
        finally:
            await self._restore_nav(chat_id, ctx)

    async def cmd_search(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            return
        if not ctx.args:
            account = _current_account(ctx)
            acc_label = f" dans {_ACC_ICON.get(account, '')} {_ACC_LABEL.get(account, '')}" if account else ""
            await update.message.reply_text(
                f"🔍 *Recherche d'emails{acc_label}*\n"
                f"{_sep()}\n\n"
                "Usage : `/recherche <terme>`\n\n"
                "Exemples :\n"
                "  `/recherche facture`\n"
                "  `/recherche newsletter`\n"
                "  `/recherche jean@example.com`",
                parse_mode="Markdown",
            )
            return
        query = " ".join(ctx.args)
        await self._do_search(update.effective_chat.id, ctx, query)

    async def _do_search(self, chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, query: str):
        account = _current_account(ctx)
        acc_label = f" dans {_ACC_ICON.get(account, '')} {_ACC_LABEL.get(account, '')}" if account else ""
        await self._nav_loading(chat_id, ctx, f"🔍 Recherche de « {query} »{acc_label}…")
        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.search_emails(query, account=account)
            )
            if not results:
                await ctx.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔍 Aucun résultat pour *« {query} »*{acc_label}\n\nEssayez un autre terme.",
                    parse_mode="Markdown",
                )
            else:
                ctx.bot_data[LAST_EMAILS_KEY] = results
                lines = [f"🔍 *{len(results)} résultat(s) pour « {query} »{acc_label}*", _sep(), ""]
                for i, e in enumerate(results[:15]):
                    lines.append(_email_card(i, e, show_snippet=True))
                    lines.append("")
                lines.append(_sep())
                lines.append("💡 `/voir <n>` pour lire • `/repondre <n>` pour répondre")
                unsub_kb = _unsub_keyboard(results[:15])
                if unsub_kb:
                    lines.append("👇 Cliquez sur un bouton ci-dessous pour vous désabonner")
                text = "\n".join(lines)
                if len(text) > 4000:
                    text = text[:4000] + "\n…"
                await ctx.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=unsub_kb)
        except Exception as e:
            logger.error(f"_do_search error: {e}")
            await ctx.bot.send_message(chat_id=chat_id, text=f"❌ Erreur : {e}")
        finally:
            await self._restore_nav(chat_id, ctx)

    async def cmd_view_email(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            return
        emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
        if not ctx.args or not ctx.args[0].isdigit():
            await update.message.reply_text(
                "Usage : `/voir <index>`\nUtilisez *Emails* ou *Rechercher* d'abord.",
                parse_mode="Markdown",
            )
            return
        idx = int(ctx.args[0])
        if idx >= len(emails):
            await update.message.reply_text(f"❌ Index invalide. Max : {len(emails) - 1}")
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
            f"*De :* {e.get('from', '')}\n"
            f"*À :* {e.get('to', '')}\n"
            f"*Date :* {e.get('date', '')[:25]}\n"
            f"*Sujet :* {e.get('subject', '')}\n"
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
            return
        emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
        if not ctx.args or not ctx.args[0].isdigit():
            await update.message.reply_text(
                "Usage : `/repondre <index>`\nUtilisez *Emails* pour voir les index.",
                parse_mode="Markdown",
            )
            return
        idx = int(ctx.args[0])
        if idx >= len(emails):
            await update.message.reply_text(f"❌ Index invalide. Max : {len(emails) - 1}")
            return
        email = emails[idx]
        instructions = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else ""
        chat_id = update.effective_chat.id
        await self._nav_loading(chat_id, ctx, "⏳ Rédaction de la réponse…")
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
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"📝 *Brouillon — réponse à :*\n"
                    f"👤 {email.get('from', '')}\n"
                    f"📌 Re: {email.get('subject', '')}\n"
                    f"{_sep()}\n\n{draft}\n\n{_sep()}"
                ),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"cmd_reply error: {e}")
            await ctx.bot.send_message(chat_id=chat_id, text=f"❌ Erreur : {e}")
        finally:
            await self._restore_nav(chat_id, ctx)

    async def cmd_unsubscribe(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            return
        emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
        if not ctx.args or not ctx.args[0].isdigit():
            await update.message.reply_text(
                "Usage : `/desabonner <index>`\n"
                "Utilisez *Newsletters* ou *Emails* pour trouver l'index.",
                parse_mode="Markdown",
            )
            return
        idx = int(ctx.args[0])
        if idx >= len(emails):
            await update.message.reply_text(f"❌ Index invalide. Max : {len(emails) - 1}")
            return
        email = emails[idx]
        info = extract_unsubscribe(email)
        if not info["url"] and not info["mailto"]:
            await update.message.reply_text(
                f"⚠️ *Aucun lien de désabonnement trouvé*\n\n"
                f"Email : *{email.get('subject', '')}*\n\n"
                f"Utilisez `/voir {idx}` pour chercher manuellement.",
                parse_mode="Markdown",
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

    # ─── Callback handler ─────────────────────────────────────────────────────

    async def handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not _allowed(update):
            return
        data = query.data
        chat_id = update.effective_chat.id

        # ── Mailbox navigation ───────────────────────────────────────────────
        if data in _CB_TO_ACCOUNT:
            account = _CB_TO_ACCOUNT[data]
            ctx.user_data[CURRENT_ACCOUNT_KEY] = account
            acc_icon = _ACC_ICON[account]
            acc_label = _ACC_LABEL[account]
            await self._set_nav(chat_id, ctx, f"⏳ *{acc_label}* — Récupération des emails…", _account_kb())
            try:
                emails = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.manager.fetch_account_emails(account, max_results=15)
                )
                ctx.bot_data[LAST_EMAILS_KEY] = emails
                lines = [f"{acc_icon} *{acc_label} — {len(emails)} emails*", _sep(), ""]
                for i, e in enumerate(emails[:15]):
                    lines.append(_email_card(i, e, show_snippet=False))
                    lines.append("")
                lines.append(_sep())
                lines.append("💡 `/voir <n>` pour lire • `/repondre <n>` pour répondre")
                unsub_kb = _unsub_keyboard(emails[:15])
                if unsub_kb:
                    lines.append("👇 Cliquez sur un bouton ci-dessous pour vous désabonner")
                text_out = "\n".join(lines)
                if len(text_out) > 4000:
                    text_out = text_out[:4000] + "\n…"
                await ctx.bot.send_message(chat_id=chat_id, text=text_out, parse_mode="Markdown", reply_markup=unsub_kb)
            except Exception as e:
                logger.error(f"nav account error: {e}")
                await ctx.bot.send_message(chat_id=chat_id, text=f"❌ Erreur : {e}")
            finally:
                await self._set_nav(chat_id, ctx, f"{acc_icon} *{acc_label}* — Menu", _account_kb())
            return

        if data == CB_NAV_BACK:
            ctx.user_data.pop(CURRENT_ACCOUNT_KEY, None)
            await self._set_nav(chat_id, ctx, "📬 *Assistant Email* — Menu principal", _main_kb())
            return

        # ── Action callbacks ─────────────────────────────────────────────────
        if data == CB_ACT_EMAILS:
            await self._cb_list_emails(chat_id, ctx)
            return

        if data == CB_ACT_SEARCH:
            ctx.user_data["mode"] = "waiting_search"
            account = _current_account(ctx)
            acc_label = f"{_ACC_ICON.get(account, '')} {_ACC_LABEL.get(account, '')}" if account else "tous les comptes"
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=f"🔍 *Recherche — {acc_label}*\n\nEntrez votre terme de recherche :",
                parse_mode="Markdown",
            )
            return

        if data == CB_ACT_NEWSLETTERS:
            await self._do_search(chat_id, ctx, "newsletter")
            return

        if data in (CB_ACT_SORT_ACC, CB_ACT_SORT_ALL):
            if data == CB_ACT_SORT_ALL:
                ctx.user_data.pop(CURRENT_ACCOUNT_KEY, None)
            await self._cb_sort(chat_id, ctx)
            return

        if data == CB_ACT_SUMMARY:
            await self._cb_summary(chat_id, ctx)
            return

        if data == CB_ACT_HELP:
            account = _current_account(ctx)
            acc_label = f" — {_ACC_ICON.get(account, '')} {_ACC_LABEL.get(account, '')}" if account else ""
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"❓ *Aide{acc_label}*\n"
                    f"{_sep()}\n\n"
                    "*Navigation (boutons) :*\n"
                    "📧/📨/📩 — Sélectionner une boîte\n"
                    "📬 Emails — Voir les derniers emails\n"
                    "🔍 Rechercher — Chercher dans la boîte\n"
                    "🔕 Newsletters — Gérer les newsletters\n"
                    "📊 Trier — Analyser les emails\n"
                    "↩️ Retour — Retour au menu principal\n\n"
                    f"{_sep()}\n\n"
                    "*Commandes texte :*\n"
                    "`/voir <n>` — Lire un email complet\n"
                    "`/repondre <n>` — Rédiger une réponse IA\n"
                    "`/desabonner <n>` — Se désabonner\n"
                    "`/recherche <terme>` — Recherche avancée\n\n"
                    "💬 Écrivez librement pour poser des questions !"
                ),
                parse_mode="Markdown",
            )
            return

        # ── Reply flow ───────────────────────────────────────────────────────
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
            return

        if data == "edit_reply":
            await query.edit_message_text(
                "✏️ *Modification du brouillon*\n\n"
                "Envoyez vos instructions ou corrections, je régénèrerai le brouillon.",
                parse_mode="Markdown",
            )
            ctx.user_data["mode"] = "editing_reply"
            return

        if data == "cancel_reply":
            ctx.bot_data.pop(PENDING_REPLY_KEY, None)
            await query.edit_message_text("❌ Réponse annulée.")
            return

        # ── Unsubscribe flow ─────────────────────────────────────────────────
        if data == "confirm_unsub":
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
                            f"✅ *Désabonné avec succès !*\n{_sep()}\n*De :* {email.get('from', '')}\n{msg_txt}",
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
                    client = (self.manager.gmail if account == "gmail"
                              else self.manager.outlook1 if account == "outlook1"
                              else self.manager.outlook2)
                    ok = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: client.send_email(addr, subject_part, "unsubscribe")
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
            return

        if data == "cancel_unsub":
            ctx.user_data.pop("pending_unsub", None)
            await query.edit_message_text("❌ Désabonnement annulé.")
            return

        if data.startswith("unsub_"):
            idx = int(data.split("_")[1])
            emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
            if idx >= len(emails):
                await query.answer("❌ Email introuvable.", show_alert=True)
                return
            email = emails[idx]
            info = extract_unsubscribe(email)
            if not info["url"] and not info["mailto"]:
                await query.answer("⚠️ Aucun lien de désabonnement trouvé.", show_alert=True)
                return
            ctx.user_data["pending_unsub"] = {"email": email, "info": info, "idx": idx}
            target = info["url"] or info["mailto"] or ""
            method_label = {"one_click": "✅ 1 clic", "http": "🌐 Lien web", "mailto": "📧 Email"}.get(info["method"], "")
            keyboard = [[
                InlineKeyboardButton("✅ Confirmer", callback_data="confirm_unsub"),
                InlineKeyboardButton("❌ Annuler", callback_data="cancel_unsub"),
            ]]
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🔕 *Désabonnement de :*\n"
                    f"👤 {email.get('from', '')}\n"
                    f"{method_label} — `{target[:80]}`\n\n"
                    f"Confirmer ?"
                ),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )
            return

        if data.startswith("reply_"):
            idx = int(data.split("_")[1])
            emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
            if idx >= len(emails):
                await query.answer("❌ Email introuvable.", show_alert=True)
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
                    f"👤 {email.get('from', '')}\n"
                    f"📌 Re: {email.get('subject', '')}\n"
                    f"{_sep()}\n\n{draft}\n\n{_sep()}",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
            except Exception as e:
                await query.edit_message_text(f"❌ Erreur : {e}")
            return

    # ─── Callback action helpers ──────────────────────────────────────────────

    async def _cb_list_emails(self, chat_id: int, ctx: ContextTypes.DEFAULT_TYPE):
        account = _current_account(ctx)
        acc_label = f"{_ACC_ICON.get(account, '')} {_ACC_LABEL.get(account, '')}" if account else "tous les comptes"
        await self._nav_loading(chat_id, ctx, f"⏳ Récupération des emails — *{acc_label}*…")
        try:
            if account:
                emails = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.manager.fetch_account_emails(account, max_results=15)
                )
            else:
                emails = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.manager.fetch_all_emails(max_per_account=15)
                )
            ctx.bot_data[LAST_EMAILS_KEY] = emails
            lines = [f"📬 *{len(emails)} emails — {acc_label}*", _sep(), ""]
            for i, e in enumerate(emails[:15]):
                lines.append(_email_card(i, e, show_snippet=False))
                lines.append("")
            lines.append(_sep())
            lines.append("💡 `/voir <n>` pour lire • `/repondre <n>` pour répondre")
            unsub_kb = _unsub_keyboard(emails[:15])
            if unsub_kb:
                lines.append("👇 Cliquez sur un bouton ci-dessous pour vous désabonner")
            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:4000] + "\n…"
            await ctx.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=unsub_kb)
        except Exception as e:
            logger.error(f"_cb_list_emails error: {e}")
            await ctx.bot.send_message(chat_id=chat_id, text=f"❌ Erreur : {e}")
        finally:
            await self._restore_nav(chat_id, ctx)

    async def _cb_sort(self, chat_id: int, ctx: ContextTypes.DEFAULT_TYPE):
        account = _current_account(ctx)
        acc_label = f"{_ACC_ICON.get(account, '')} {_ACC_LABEL.get(account, '')}" if account else "tous les comptes"
        await self._nav_loading(chat_id, ctx, f"⏳ Analyse de *{acc_label}* en cours… (1-2 min)")
        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.analyze_and_sort(30)
            )
            if account:
                results = [e for e in results if e.get("account") == account]
            ctx.bot_data[LAST_EMAILS_KEY] = results
            cats: dict[str, int] = {}
            events_count = 0
            for e in results:
                cat = e.get("category", "other")
                cats[cat] = cats.get(cat, 0) + 1
                events_count += len(e.get("events", []))
            lines = [f"✅ *{len(results)} emails analysés — {acc_label}*", _sep(), ""]
            for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
                lines.append(f"{_CAT_LABEL.get(cat, f'📁 {cat}')} — *{count}*")
            if events_count:
                lines.append("")
                lines.append(f"📅 *{events_count}* événement(s) ajouté(s) au calendrier")
            await ctx.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"_cb_sort error: {e}")
            await ctx.bot.send_message(chat_id=chat_id, text=f"❌ Erreur lors du tri : {e}")
        finally:
            await self._restore_nav(chat_id, ctx)

    async def _cb_summary(self, chat_id: int, ctx: ContextTypes.DEFAULT_TYPE):
        await self._nav_loading(chat_id, ctx, "⏳ Génération du résumé en cours…")
        try:
            summary = await asyncio.get_event_loop().run_in_executor(
                None, self.manager.generate_daily_summary
            )
            if len(summary) <= 4096:
                await ctx.bot.send_message(chat_id=chat_id, text=summary)
            else:
                for chunk in [summary[i:i + 4000] for i in range(0, len(summary), 4000)]:
                    await ctx.bot.send_message(chat_id=chat_id, text=chunk)
        except Exception as e:
            logger.error(f"_cb_summary error: {e}")
            await ctx.bot.send_message(chat_id=chat_id, text=f"❌ Erreur : {e}")
        finally:
            await self._restore_nav(chat_id, ctx)

    # ─── Free text handler ────────────────────────────────────────────────────

    async def handle_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _allowed(update):
            return
        text = update.message.text.strip()
        mode = ctx.user_data.get("mode")
        chat_id = update.effective_chat.id

        # ── Editing reply draft ──────────────────────────────────────────────
        if mode == "editing_reply":
            ctx.user_data.pop("mode", None)
            pending = ctx.bot_data.get(PENDING_REPLY_KEY)
            if not pending:
                await update.message.reply_text("Aucun brouillon en cours.")
                return
            await self._nav_loading(chat_id, ctx, "⏳ Régénération du brouillon…")
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
                await ctx.bot.send_message(
                    chat_id=chat_id,
                    text=f"📝 *Nouveau brouillon :*\n{_sep()}\n\n{draft}\n\n{_sep()}",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
            except Exception as e:
                await ctx.bot.send_message(chat_id=chat_id, text=f"❌ Erreur : {e}")
            finally:
                await self._restore_nav(chat_id, ctx)
            return

        # ── Waiting for search term ──────────────────────────────────────────
        if mode == "waiting_search":
            ctx.user_data.pop("mode", None)
            await self._do_search(chat_id, ctx, text)
            return

        # ── General AI chat ──────────────────────────────────────────────────
        account = _current_account(ctx)
        await self._nav_loading(chat_id, ctx, "💭 Réflexion en cours…")
        try:
            last_emails = ctx.bot_data.get(LAST_EMAILS_KEY, [])
            if not last_emails:
                await self._nav_loading(chat_id, ctx, "📥 Récupération de vos emails en cours…")
                if account:
                    last_emails = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: self.manager.fetch_account_emails(account, max_results=50)
                    )
                else:
                    last_emails = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: self.manager.fetch_all_emails(max_per_account=50)
                    )
                ctx.bot_data[LAST_EMAILS_KEY] = last_emails
                await self._nav_loading(chat_id, ctx, "💭 Réflexion en cours…")

            context = ""
            if last_emails:
                acc_ctx = f"Boîte active : {_ACC_LABEL.get(account, 'toutes')}\n" if account else ""
                lines = [f"{acc_ctx}{len(last_emails)} emails disponibles :"]
                for i, e in enumerate(last_emails):
                    lines.append(
                        f"[{i}] {e.get('account', '')} | cat={e.get('category', '')} imp={e.get('importance', '')} "
                        f"| De: {e.get('from', '')[:40]} | Sujet: {e.get('subject', '')}"
                    )
                context = "\n".join(lines)

            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.manager.chat(text, context)
            )
            await ctx.bot.send_message(chat_id=chat_id, text=response)
        except Exception as e:
            logger.error(f"handle_message error: {e}")
            await ctx.bot.send_message(chat_id=chat_id, text=f"❌ Erreur : {e}")
        finally:
            await self._restore_nav(chat_id, ctx)

    # ─── Run ─────────────────────────────────────────────────────────────────

    def run(self):
        logger.info("Starting Telegram bot...")
        self.app.run_polling(drop_pending_updates=True)
