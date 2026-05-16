#!/bin/bash
set -e

echo "=== Assistant Mail — Installation ==="
echo ""

# Check Docker
if ! command -v docker &>/dev/null; then
    echo "❌ Docker n'est pas installé."
    echo "   Téléchargez-le sur : https://docs.docker.com/get-docker/"
    exit 1
fi

if ! command -v docker &>/dev/null || ! docker compose version &>/dev/null 2>&1; then
    if ! command -v docker-compose &>/dev/null; then
        echo "❌ Docker Compose n'est pas installé."
        exit 1
    fi
    COMPOSE="docker-compose"
else
    COMPOSE="docker compose"
fi

echo "✅ Docker détecté"

# Check required files
missing=0
for f in .env config/gmail_credentials.json config/gmail_token.json config/outlook1_token.json config/outlook2_token.json; do
    if [ ! -f "$f" ]; then
        echo "❌ Fichier manquant : $f"
        missing=1
    fi
done

if [ $missing -eq 1 ]; then
    echo ""
    echo "Placez tous les fichiers de config manquants et relancez ce script."
    exit 1
fi

echo "✅ Fichiers de configuration présents"
echo ""
echo "Construction et démarrage du bot..."
$COMPOSE up -d --build

echo ""
echo "✅ Bot démarré ! Écrivez /start à votre bot Telegram."
echo ""
echo "Commandes utiles :"
echo "  Voir les logs    : $COMPOSE logs -f"
echo "  Arrêter le bot   : $COMPOSE down"
echo "  Redémarrer       : $COMPOSE restart"
