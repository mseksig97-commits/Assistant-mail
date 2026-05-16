# Assistant Mail IA — Agent Telegram

Agent IA de gestion d'emails piloté via Telegram. Gère 2 comptes Outlook + 1 Gmail avec tri automatique, analyse par IA, ajout au calendrier et résumés quotidiens.

## Fonctionnalités

| Fonctionnalité | Description |
|---|---|
| **Tri automatique** | Classe les emails en catégories (urgent, important, travail, spam…) |
| **Analyse IA** | Note l'importance (1-5) et suggère une action pour chaque email |
| **Calendrier** | Détecte les dates/événements et les ajoute automatiquement à Google Calendar |
| **Réponses IA** | Rédige des brouillons de réponse, les soumet pour validation avant envoi |
| **Résumé quotidien** | Synthèse automatique chaque matin à l'heure configurée |
| **Surveillance** | Tri automatique toutes les 30 minutes, alerte sur les emails urgents |
| **Recherche** | Recherche full-text dans tous vos comptes |

## Architecture

```
main.py                     ← Point d'entrée
src/
  agent/
    ai_processor.py         ← Appels Claude (analyse, rédaction, résumé, chat)
    email_manager.py        ← Orchestration de toutes les opérations email
    scheduler.py            ← Tâches planifiées (résumé quotidien, tri auto)
  email/
    gmail_client.py         ← API Gmail (OAuth2)
    outlook_client.py       ← Microsoft Graph API (MSAL)
  calendar/
    google_calendar.py      ← Google Calendar API
  telegram/
    bot.py                  ← Bot Telegram (commandes + chat libre)
  utils/
    logger.py               ← Logging centralisé
setup_auth.py               ← Script de configuration initiale OAuth2
```

## Installation

### 1. Prérequis

- Python 3.11+
- Un bot Telegram (via [@BotFather](https://t.me/BotFather))
- Une clé API Anthropic
- Credentials Google Cloud (Gmail + Calendar)
- Une app Azure (Outlook 1 & 2)

### 2. Installation des dépendances

```bash
pip install -r requirements.txt
```

### 3. Configuration

```bash
cp .env.example .env
# Remplissez toutes les variables dans .env
```

### 4. Configuration OAuth2 (première fois uniquement)

```bash
python setup_auth.py
```

Ce script guide l'authentification OAuth2 pour chaque compte.

#### Gmail / Google Calendar
1. Créer un projet sur [Google Cloud Console](https://console.cloud.google.com)
2. Activer **Gmail API** et **Google Calendar API**
3. Créer des identifiants OAuth2 (type "Application de bureau")
4. Télécharger le JSON → `config/gmail_credentials.json`

#### Outlook (Microsoft Graph)
1. Créer une app sur [Azure App Registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps)
2. Ajouter les permissions : `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`
3. Créer un secret client
4. Renseigner `CLIENT_ID`, `CLIENT_SECRET`, `TENANT_ID` dans `.env`
5. URI de redirection : `http://localhost:8080`

### 5. Démarrage

```bash
python main.py
```

## Commandes Telegram

| Commande | Description |
|---|---|
| `/start` | Message de bienvenue |
| `/trier` | Analyse et classe tous vos emails |
| `/resume` | Résumé quotidien immédiat |
| `/emails [n]` | Liste les n derniers emails (défaut: 10) |
| `/recherche <terme>` | Recherche dans tous vos emails |
| `/repondre <index>` | Rédige une réponse IA pour l'email n°index |
| Message libre | Conversation générale avec l'IA |

## Catégories d'emails

| Catégorie | Label |
|---|---|
| urgent | ⚡ Urgent |
| important | ⭐ Important |
| work | 💼 Travail |
| personal | 👤 Personnel |
| finance | 💰 Finance |
| newsletter | 📰 Newsletter |
| spam | 🗑️ Spam |
| social | 🌐 Social |
| information | ℹ️ Info |
| other | 📁 Autre |

## Variables d'environnement

Voir `.env.example` pour la liste complète et les descriptions.

Variables clés :
- `TELEGRAM_BOT_TOKEN` — Token du bot Telegram
- `TELEGRAM_ALLOWED_USER_ID` — Votre Telegram user ID (sécurité)
- `ANTHROPIC_API_KEY` — Clé API Anthropic (Claude)
- `DAILY_SUMMARY_TIME` — Heure du résumé quotidien (format HH:MM)
- `TIMEZONE` — Fuseau horaire (ex: `Europe/Paris`)
