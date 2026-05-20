import os
import re
import json
from datetime import datetime

import anthropic

from src.utils.logger import setup_logger

logger = setup_logger("ai_processor")

CATEGORIES = ["urgent", "important", "information", "spam", "newsletter", "social", "work", "personal", "finance", "other"]

CHAT_TOOLS = [
    {
        "name": "delete_emails",
        "description": "Met à la corbeille un ou plusieurs emails (indices [N] du contexte).",
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {"type": "array", "items": {"type": "integer"},
                            "description": "Indices 0-based des emails à supprimer"}
            },
            "required": ["indices"],
        },
    },
    {
        "name": "archive_emails",
        "description": "Archive un ou plusieurs emails (retire de la boîte de réception).",
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {"type": "array", "items": {"type": "integer"}}
            },
            "required": ["indices"],
        },
    },
    {
        "name": "move_emails",
        "description": "Déplace des emails vers un dossier spécifique.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {"type": "array", "items": {"type": "integer"}},
                "folder": {"type": "string", "description": "Nom du dossier cible (ex: Travail, Archives)"},
            },
            "required": ["indices", "folder"],
        },
    },
    {
        "name": "mark_read_emails",
        "description": "Marque des emails comme lus.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {"type": "array", "items": {"type": "integer"}}
            },
            "required": ["indices"],
        },
    },
    {
        "name": "mark_unread_emails",
        "description": "Marque des emails comme non lus.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {"type": "array", "items": {"type": "integer"}}
            },
            "required": ["indices"],
        },
    },
    {
        "name": "mark_spam",
        "description": "Marque des emails comme spam / indésirables.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {"type": "array", "items": {"type": "integer"}}
            },
            "required": ["indices"],
        },
    },
    {
        "name": "send_email",
        "description": (
            "Compose et envoie un nouvel email. "
            "Rédige un sujet et un corps complets et professionnels."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Adresse email du destinataire"},
                "subject": {"type": "string", "description": "Sujet de l'email"},
                "body": {"type": "string", "description": "Corps complet du message"},
                "account": {
                    "type": "string",
                    "enum": ["gmail", "outlook1", "outlook2"],
                    "description": "Compte expéditeur (défaut: gmail)",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "reply_to_email",
        "description": (
            "Répond à un email existant. "
            "Rédige une réponse complète et professionnelle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email_index": {"type": "integer", "description": "Indice [N] de l'email auquel répondre"},
                "body": {"type": "string", "description": "Corps complet de la réponse"},
            },
            "required": ["email_index", "body"],
        },
    },
    {
        "name": "forward_email",
        "description": "Transfère un email à une autre adresse.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email_index": {"type": "integer", "description": "Indice [N] de l'email à transférer"},
                "to": {"type": "string", "description": "Adresse de destination"},
                "note": {"type": "string", "description": "Message optionnel ajouté avant l'email transféré"},
            },
            "required": ["email_index", "to"],
        },
    },
    {
        "name": "unsubscribe_email",
        "description": "Se désabonne d'une newsletter ou liste de diffusion.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email_index": {"type": "integer", "description": "Indice [N] de l'email de newsletter"}
            },
            "required": ["email_index"],
        },
    },
    {
        "name": "sort_and_label",
        "description": "Analyse et trie les emails : applique des catégories et labels automatiquement.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_emails": {
                    "type": "integer",
                    "description": "Nombre max d'emails à traiter par compte (défaut: 30)",
                }
            },
            "required": [],
        },
    },
]

_CHAT_SYSTEM = (
    "Tu es un assistant IA de gestion des emails. Tu peux répondre aux questions "
    "sur les emails et effectuer toutes les actions de gestion en utilisant les outils disponibles.\n"
    "Les emails sont listés avec un indice [N] dans le contexte.\n\n"
    "Règles :\n"
    "- Identifie toujours les bons indices d'emails avant d'appeler un outil\n"
    "- Pour send_email et reply_to_email, rédige un message complet et professionnel\n"
    "- Utilise les outils plutôt que de décrire l'action\n"
    "- Si plusieurs actions sont demandées, appelle plusieurs outils\n"
    "Réponds en français, de manière concise."
)

SYSTEM_PROMPT = """Tu es un assistant IA spécialisé dans la gestion des emails.
Tu analyses les emails et fournis des réponses structurées en JSON.
Sois concis, précis et professionnel. Réponds TOUJOURS en JSON valide sans markdown.
"""


class AIProcessor:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = "claude-sonnet-4-6"

    def _call(self, prompt: str, max_tokens: int = 2000) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()

    def _call_json(self, prompt: str, max_tokens: int = 2000) -> dict:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r'^```(?:json)?\s*\n?', '', raw).rstrip('`').strip()
        return json.loads(raw)

    def analyze_email(self, email: dict) -> dict:
        """Returns category, importance (1-5), summary, suggested_action, events."""
        prompt = f"""Analyse cet email et retourne un JSON avec exactement ces champs :
{{
  "category": "<une parmi: {', '.join(CATEGORIES)}>",
  "importance": <entier 1 (faible) à 5 (critique)>,
  "summary": "<résumé en 1-2 phrases>",
  "suggested_action": "<action recommandée>",
  "needs_reply": <true/false>,
  "events": [
    {{
      "title": "<titre>",
      "date": "<YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS>",
      "end_date": "<optionnel>",
      "all_day": <true/false>,
      "description": "<contexte>"
    }}
  ]
}}

Email :
De : {email.get('from', '')}
Sujet : {email.get('subject', '')}
Date : {email.get('date', '')}
Corps : {email.get('body', email.get('snippet', ''))[:3000]}
"""
        try:
            return self._call_json(prompt)
        except Exception as e:
            logger.error(f"analyze_email error: {e}")
            return {"category": "other", "importance": 2, "summary": email.get("snippet", ""),
                    "suggested_action": "Vérifier manuellement", "needs_reply": False, "events": []}

    def draft_reply(self, email: dict, instructions: str = "") -> str:
        """Drafts a reply to the given email."""
        prompt = f"""Rédige une réponse professionnelle à cet email.
{f"Instructions supplémentaires : {instructions}" if instructions else ""}
Retourne uniquement le corps du message (pas d'objet, pas de métadonnées).

Email original :
De : {email.get('from', '')}
Sujet : {email.get('subject', '')}
Corps : {email.get('body', email.get('snippet', ''))[:3000]}
"""
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                system="Tu es un assistant de rédaction d'emails professionnel. Rédige des réponses claires, polies et concises.",
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            logger.error(f"draft_reply error: {e}")
            return ""

    def generate_daily_summary(self, analyzed_emails: list[dict]) -> str:
        """Generates a formatted daily summary from a list of analyzed email dicts."""
        if not analyzed_emails:
            return "Aucun email reçu aujourd'hui."

        emails_json = json.dumps(analyzed_emails, ensure_ascii=False, indent=2)[:8000]
        prompt = f"""Génère un résumé quotidien des emails reçus.
Formate la réponse en texte lisible (pas JSON) avec des sections :
- 🔴 Urgents / importants (importance >= 4)
- 📌 À traiter (importance 2-3, needs_reply=true)
- ℹ️ Informatifs
- 🗑️ Spam / newsletters
- 📅 Événements détectés
- 💡 Actions recommandées

Emails analysés :
{emails_json}
"""
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=1500,
                system="Tu es un assistant de synthèse d'emails. Génère des résumés clairs et actionnables en français.",
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            logger.error(f"daily_summary error: {e}")
            return "Erreur lors de la génération du résumé."

    def chat(
        self,
        user_message: str,
        context: str = "",
        history: list[dict] | None = None,
    ) -> dict:
        """
        Returns a dict:
          {"type": "text",         "text": "..."}
          {"type": "action",       "name": "...", "input": {...}, "preview": "..."}
          {"type": "multi_action", "actions": [...], "preview": "..."}
        """
        messages = list(history) if history else []
        content = (
            f"Contexte des emails disponibles :\n{context}\n\nMessage : {user_message}"
            if context else user_message
        )
        messages.append({"role": "user", "content": content})

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1500,
                system=_CHAT_SYSTEM,
                messages=messages,
                tools=CHAT_TOOLS,
            )

            tool_blocks = [b for b in response.content if b.type == "tool_use"]
            text = " ".join(b.text for b in response.content if hasattr(b, "text")).strip()

            if not tool_blocks:
                return {"type": "text", "text": text}

            if len(tool_blocks) == 1:
                return {
                    "type": "action",
                    "name": tool_blocks[0].name,
                    "input": tool_blocks[0].input,
                    "preview": text,
                }

            return {
                "type": "multi_action",
                "actions": [{"name": b.name, "input": b.input} for b in tool_blocks],
                "preview": text,
            }

        except Exception as e:
            logger.error(f"chat error: {type(e).__name__}: {e}")
            return {"type": "text", "text": f"❌ Erreur API : {type(e).__name__} — {e}"}
