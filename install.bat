@echo off
echo === Assistant Mail - Installation ===
echo.

docker --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo Docker n'est pas installe.
    echo Telechargez-le sur : https://docs.docker.com/get-docker/
    pause
    exit /b 1
)

echo Docker detecte.

IF NOT EXIST ".env" (
    echo Fichier manquant : .env
    pause
    exit /b 1
)
IF NOT EXIST "config\gmail_token.json" (
    echo Fichier manquant : config\gmail_token.json
    pause
    exit /b 1
)
IF NOT EXIST "config\outlook1_token.json" (
    echo Fichier manquant : config\outlook1_token.json
    pause
    exit /b 1
)
IF NOT EXIST "config\outlook2_token.json" (
    echo Fichier manquant : config\outlook2_token.json
    pause
    exit /b 1
)

echo Fichiers de configuration presents.
echo.
echo Construction et demarrage du bot...
docker compose up -d --build

echo.
echo Bot demarre ! Ecrivez /start a votre bot Telegram.
echo.
echo Commandes utiles :
echo   Voir les logs   : docker compose logs -f
echo   Arreter le bot  : docker compose down
echo   Redemarrer      : docker compose restart
pause
