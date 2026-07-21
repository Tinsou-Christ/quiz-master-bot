# Quiz Bot Telegram

Bot Telegram pour organiser des concours de questions/reponses avec comptage de points **persistant**.

## Regles
- La **1re** bonne reponse gagne **2 points**. Les autres : **0**.
- Nombre de joueurs **illimite**.
- Les scores sont sauvegardes sur disque et survivent aux redemarrages.

## Commandes
| Commande | Qui | Description |
|---|---|---|
| `/start` | tous | Aide |
| `/startquiz` | tous | Affiche un bouton "Activer le quiz" |
| `/sethost` (reponse au message) ou `/sethost @pseudo` | admin | Designe le poseur de questions |
| `/join` | tous | Rejoindre en tant que joueur |
| `/ask <question>` | host | Ouvre un round |
| `/verdict <bonne reponse>` | host | Cloture, distribue les points |
| `/leaderboard` | tous | Classement total |
| `/reset` | host/admin | Remet les scores a zero |
| `/stopquiz` | host/admin | Desactive le quiz |

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
