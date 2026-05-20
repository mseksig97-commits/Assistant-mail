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
        "description": (
            "Supprime (met à la corbeille) des emails. "
            "Utilise les indices affichés dans le contexte (colonne [N])."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Indices des emails à supprimer (0-based)",
                }
            },
            "required": ["indices"],
        },
    },
    {
        "name": "mark_read_emails",
        "description": "Marque des emails comme lus.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Indices des emails à marquer comme lus",
                }
            },
            "required": ["indices"],
        },
    },
    {
        "name": "move_emails",
        "description": "Déplace des emails vers un dossier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Indices des emails à déplacer",
                },
                "folder": {
                    "type": "string",
                    "description": "Nom du dossier de destination (ex: Archives, Spam)",
                },
            },
            "required": ["indices", "folder"],
        },
    },
]

_CHAT_SYSTEM = (
    "Tu es un assistant IA de gestion des emails. Tu peux répondre aux questions "
    "sur les emails et effectuer des actions (supprimer, déplacer, marquer comme lu) "
    "en utilisant les outils disponibles. Les emails sont listés avec un indice [N] dans le contexte. "
    "Réponds toujours en français, de manière concise et directe."
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
        tool_executor=None,
    ) -> str:
        """Conversational response with optional tool use for email actions."""
        messages = list(history) if history else []

        content = user_message
        if context:
            content = f"Contexte des emails disponibles :\n{context}\n\nMessage : {user_message}"
        messages.append({"role": "user", "content": content})

        kwargs: dict = dict(
            model=self.model,
            max_tokens=1000,
            system=_CHAT_SYSTEM,
            messages=messages,
        )
        if tool_executor:
            kwargs["tools"] = CHAT_TOOLS

        try:
            response = self.client.messages.create(**kwargs)

            # ── Tool use loop ────────────────────────────────────────────────
            if response.stop_reason == "tool_use" and tool_executor:
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = tool_executor(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result),
                        })

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

                follow_up = self.client.messages.create(
                    model=self.model,
                    max_tokens=500,
                    system=_CHAT_SYSTEM,
                    messages=messages,
                    tools=CHAT_TOOLS,
                )
                return next(
                    (b.text.strip() for b in follow_up.content if hasattr(b, "text")), ""
                )

            # ── Plain text response ──────────────────────────────────────────
            return next(
                (b.text.strip() for b in response.content if hasattr(b, "text")), ""
            )

        except Exception as e:
            logger.error(f"chat error: {type(e).__name__}: {e}")
            return f"❌ Erreur API : {type(e).__name__} — {e}"
