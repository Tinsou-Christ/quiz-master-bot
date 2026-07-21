"""Bot Telegram de quiz avec comptage de points persistant.

Fonctionnement :
- /startquiz  : active le quiz dans le groupe (bouton "Activer le quiz" aussi)
- /stopquiz   : desactive le quiz
- /sethost    : (admin/creator) designe la personne qui posera les questions
                Usage : /sethost en repondant a un message de la personne, OU /sethost @pseudo
- /join       : n'importe qui rejoint le quiz (nombre illimite)
- /ask <question>  : le host pose la question (ouvre un round)
- /verdict <bonne_reponse>  : le host cloture le round
     -> la PREMIERE personne qui a envoye la bonne reponse (case-insensitive)
        pendant le round recoit 2 points, les autres 0.
- /leaderboard : classement total persistant
- /reset       : (host) remet les scores a zero pour ce chat

Les donnees sont sauvegardees sur disque (DATA_DIR, defaut ./data)
et survivent au redemarrage / redeploiement (monter un volume Docker sur /data).
"""

import logging
import os
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from storage import get_chat, update_chat

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN manquant. Definis-le dans l'environnement (.env).")


# ---------- Health check HTTP (pour Render/Railway/Fly/etc.) ----------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):  # silence
        pass


def run_health_check_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), HealthCheckHandler).serve_forever()


# ---------- Utilitaires ----------
def normalize(text: str) -> str:
    """Comparaison souple : minuscule, sans accents, sans espaces extremes."""
    if not text:
        return ""
    text = text.strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(text.split())


def display_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


async def is_group_admin(update: Update, user_id: int) -> bool:
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        return True
    try:
        member = await chat.get_member(user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


def is_host(state, user_id: int) -> bool:
    return state.get("host_id") == user_id


# ---------- Commandes ----------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salut ! Je suis le bot Quiz 🎯\n\n"
        "Commandes principales :\n"
        "• /startquiz — activer le quiz (bouton fourni)\n"
        "• /sethost — designer le poseur de questions\n"
        "• /join — rejoindre en tant que joueur\n"
        "• /ask <question> — (host) poser une question\n"
        "• /verdict <bonne reponse> — (host) cloturer et attribuer les points\n"
        "• /leaderboard — voir le classement\n"
        "• /reset — (host) remettre a zero\n"
        "• /stopquiz — desactiver\n\n"
        "Regle : la 1re bonne reponse gagne 2 points, les autres 0."
    )


async def startquiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Activer le quiz", callback_data="quiz:activate")]]
    )
    await update.message.reply_text(
        "Pret a lancer le quiz ? Clique pour activer :", reply_markup=keyboard
    )


async def activate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id

    def mut(state):
        state["active"] = True
        state["round"] = None

    update_chat(chat_id, mut)
    await query.edit_message_text(
        "🎉 Quiz ACTIVE !\n\n"
        "➡️ Le poseur de questions doit etre designe avec /sethost\n"
        "➡️ Les joueurs rejoignent avec /join\n"
        "➡️ Puis /ask <question> pour demarrer un round."
    )


async def stopquiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = get_chat(update.effective_chat.id)
    if not (is_host(state, user.id) or await is_group_admin(update, user.id)):
        await update.message.reply_text("Seul le host ou un admin peut arreter le quiz.")
        return

    def mut(s):
        s["active"] = False
        s["round"] = None

    update_chat(update.effective_chat.id, mut)
    await update.message.reply_text("🛑 Quiz desactive. Les scores restent sauvegardes.")


async def sethost_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Definit le poseur de questions.
    Usage : /sethost en reponse au message de la personne, OU /sethost @pseudo
    """
    user = update.effective_user
    chat = update.effective_chat

    if not await is_group_admin(update, user.id) and chat.type != ChatType.PRIVATE:
        # tolere aussi si personne n'est encore host
        state = get_chat(chat.id)
        if state.get("host_id") is not None:
            await update.message.reply_text("Seul un admin du groupe peut changer le host.")
            return

    target_user = None
    target_name = None

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = update.message.reply_to_message.from_user
        target_name = display_name(target_user)
    elif context.args:
        arg = context.args[0].lstrip("@")
        # On ne peut pas resoudre un @pseudo sans l'avoir vu ; on stocke le nom.
        # Demander idealement de repondre au message de la personne.
        target_name = f"@{arg}"

    if target_user is None and target_name is None:
        await update.message.reply_text(
            "Usage : reponds au message de la personne avec /sethost, "
            "ou tape /sethost @pseudo (mieux : repondre au message)."
        )
        return

    def mut(state):
        if target_user is not None:
            state["host_id"] = target_user.id
            state["host_name"] = display_name(target_user)
        else:
            # Pas d'ID connu : pas de restriction stricte possible.
            state["host_id"] = None
            state["host_name"] = target_name

    update_chat(chat.id, mut)
    await update.message.reply_text(f"🎤 Poseur de questions : {target_name}")


async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = get_chat(update.effective_chat.id)
    if not state.get("active"):
        await update.message.reply_text("Le quiz n'est pas encore actif. Tape /startquiz.")
        return

    def mut(s):
        p = s["players"].setdefault(str(user.id), {"name": display_name(user), "score": 0})
        p["name"] = display_name(user)

    update_chat(update.effective_chat.id, mut)
    await update.message.reply_text(f"✅ {display_name(user)} a rejoint le quiz !")


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = get_chat(update.effective_chat.id)
    if not state.get("active"):
        await update.message.reply_text("Quiz inactif. /startquiz d'abord.")
        return
    if state.get("host_id") and state["host_id"] != user.id:
        await update.message.reply_text("Seul le host peut poser une question.")
        return

    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text("Usage : /ask <ta question>")
        return

    def mut(s):
        s["round"] = {"question": question, "answers": []}

    update_chat(update.effective_chat.id, mut)
    await update.message.reply_text(
        f"❓ Question du host :\n\n{question}\n\n"
        "Repondez ici. La 1re bonne reponse gagne 2 pts.\n"
        "Le host tape /verdict <bonne reponse> pour cloturer."
    )


async def collect_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture les messages texte pendant un round."""
    msg = update.message
    if not msg or not msg.text or msg.text.startswith("/"):
        return
    user = msg.from_user
    state = get_chat(update.effective_chat.id)
    if not state.get("active") or not state.get("round"):
        return
    if state.get("host_id") == user.id:
        return  # le host ne joue pas

    # Une seule reponse comptee par joueur par round (la premiere)
    round_ = state["round"]
    if any(a["user_id"] == user.id for a in round_["answers"]):
        return

    def mut(s):
        s["round"]["answers"].append({
            "user_id": user.id,
            "name": display_name(user),
            "text": msg.text,
            "ts": time.time(),
        })
        # inscrit auto le joueur si pas deja
        p = s["players"].setdefault(str(user.id), {"name": display_name(user), "score": 0})
        p["name"] = display_name(user)

    update_chat(update.effective_chat.id, mut)


async def verdict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = get_chat(update.effective_chat.id)
    if not state.get("active"):
        await update.message.reply_text("Quiz inactif.")
        return
    if state.get("host_id") and state["host_id"] != user.id:
        await update.message.reply_text("Seul le host peut donner le verdict.")
        return
    if not state.get("round"):
        await update.message.reply_text("Aucun round en cours. Utilise /ask <question>.")
        return

    correct = " ".join(context.args).strip()
    if not correct:
        await update.message.reply_text("Usage : /verdict <la bonne reponse>")
        return

    target = normalize(correct)
    round_ = state["round"]
    # Tri par timestamp (ordre d'arrivee)
    answers_sorted = sorted(round_["answers"], key=lambda a: a["ts"])

    winner = None
    correct_ones = []
    wrong_ones = []
    for a in answers_sorted:
        if normalize(a["text"]) == target:
            correct_ones.append(a)
            if winner is None:
                winner = a
        else:
            wrong_ones.append(a)

    def mut(s):
        if winner is not None:
            uid = str(winner["user_id"])
            p = s["players"].setdefault(uid, {"name": winner["name"], "score": 0})
            p["score"] += 2
            p["name"] = winner["name"]
        # les autres : 0 point (rien a faire, on ne retire pas)
        s["round"] = None

    update_chat(update.effective_chat.id, mut)

    lines = [f"📢 Bonne reponse : *{correct}*", ""]
    if winner:
        lines.append(f"🥇 +2 pts a {winner['name']} (1re bonne reponse)")
        also = [a["name"] for a in correct_ones if a["user_id"] != winner["user_id"]]
        if also:
            lines.append(f"✅ Aussi correct (0 pt, trop tard) : {', '.join(also)}")
    else:
        lines.append("❌ Personne n'a trouve la bonne reponse.")
    if wrong_ones:
        lines.append(f"❌ Mauvaises reponses : {', '.join(a['name'] for a in wrong_ones)}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = get_chat(update.effective_chat.id)
    players = state.get("players", {})
    if not players:
        await update.message.reply_text("Aucun joueur pour l'instant. Tape /join.")
        return
    ranking = sorted(players.values(), key=lambda p: p["score"], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 *Classement*", ""]
    for i, p in enumerate(ranking):
        prefix = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{prefix} {p['name']} — {p['score']} pt(s)")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = get_chat(update.effective_chat.id)
    if not (is_host(state, user.id) or await is_group_admin(update, user.id)):
        await update.message.reply_text("Seul le host ou un admin peut faire /reset.")
        return

    def mut(s):
        for p in s["players"].values():
            p["score"] = 0
        s["round"] = None

    update_chat(update.effective_chat.id, mut)
    await update.message.reply_text("♻️ Scores remis a zero.")


# ---------- Main ----------
def main():
    threading.Thread(target=run_health_check_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("startquiz", startquiz_command))
    app.add_handler(CommandHandler("stopquiz", stopquiz_command))
    app.add_handler(CommandHandler("sethost", sethost_command))
    app.add_handler(CommandHandler("join", join_command))
    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(CommandHandler("verdict", verdict_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CommandHandler("reset", reset_command))

    app.add_handler(CallbackQueryHandler(activate_callback, pattern=r"^quiz:activate$"))

    # Capture des reponses pendant un round
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, collect_answer))

    logger.info("Quiz bot demarre, polling en cours...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
