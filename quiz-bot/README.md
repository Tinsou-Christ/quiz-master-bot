# Quiz Bot Telegram

Bot Telegram pour concours de questions/reponses avec comptage **persistant**.

## Flux
- Le host pose la question dans le **groupe** avec `/ask ...`
- Les joueurs envoient leur reponse **en PRIVE au bot** (DM)
- Le bot transmet chaque reponse au host en PRIVE avec un bouton "🏆 Gagnant (+2)"
- Le host clique sur la bonne reponse -> le bot annonce le gagnant dans le groupe (+2 pts)

⚠️ Chaque joueur et le host doivent avoir demarre le bot en PRIVE une fois (bouton Start en DM).

## Commandes
| Commande | Ou | Qui | Description |
|---|---|---|---|
| `/start` | DM ou groupe | tous | Aide |
| `/startquiz` | groupe | tous | Bouton "Activer le quiz" |
| `/sethost` (repondre au message) | groupe | admin | Designe le host |
| `/join` | groupe | tous | Rejoindre en tant que joueur |
| `/ask <question>` | groupe | host | Ouvre un round |
| `/verdict` | groupe | host | Cloture le round sans gagnant |
| `/leaderboard` | groupe | tous | Classement total |
| `/reset` | groupe | host/admin | Remet les scores a zero |
| `/stopquiz` | groupe | host/admin | Desactive le quiz |

## Lancer en local
```bash
cd quiz-bot
cp .env.example .env       # colle ton BOT_TOKEN
pip install -r requirements.txt
BOT_TOKEN=xxxx python main.py
```

## Docker
Le `Dockerfile` est a la racine du projet pour faciliter le deploiement sur Render.

```bash
# Depuis la racine du projet
docker build -t quiz-bot .
docker run -d --name quiz-bot \
  -e BOT_TOKEN=123:ABC \
  -v quizdata:/data \
  -p 8080:8080 \
  quiz-bot
```

Le volume `quizdata` (monte sur `/data`) garde les scores entre les redemarrages.

## Deploiement (Render / Railway / Fly.io)
- Le `Dockerfile` est a la racine du projet.
- Ajoute la variable d'env `BOT_TOKEN`.
- Monte un volume persistant sur `/data` (sinon les scores seront perdus a chaque redeploy).
- Le health check HTTP tourne sur le port `PORT` (defaut 8080).
