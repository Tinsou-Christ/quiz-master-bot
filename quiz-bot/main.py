"""Bot Telegram de quiz — reponses en PRIVE.

Flux :
- Dans le groupe : /startquiz (bouton), /sethost, /join, /ask, /verdict, /leaderboard, /reset, /stopquiz.
- Les JOUEURS envoient leur reponse en PRIVE au bot (DM).
- Le bot transmet chaque reponse au HOST en PRIVE avec un bouton "🏆 Gagnant (+2)".
- Le host clique sur le bouton de la bonne reponse -> le bot annonce dans le groupe
  que ce joueur gagne 2 pts et cloture le round.
- Le host peut aussi taper /verdict pour cloturer sans gagnant.

Prerequis : le HOST et les JOUEURS doivent avoir demarre le bot en prive une fois
(clic "Start" en DM), sinon le bot ne peut pas leur ecrire.
"""

import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from storage import get_chat, load_all, update_chat

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN manquant. Definis-le dans l'environnement (.env).")


# ---------- Health check HTTP ----------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def run_health_check_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), HealthCheckHandler).serve_forever()


# ---------- Utils ----------
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


def find_active_round_for_user(user_id: int):
    """Cherche un chat (groupe) actif ou l'utilisateur est joueur avec un round ouvert.
    Retourne (chat_id, state) ou (None, None)."""
    data = load_all()
    matches = []
    for key, state in data.items():
        if not state.get("active") or not state.get("round"):
            continue
        if str(user_id) in state.get("players", {}):
            matches.append((int(key), state))
    if len(matches) == 1:
        return matches[0]
    # Si plusieurs, on retourne le plus recent (round ts)
    if matches:
        matches.sort(key=lambda kv: kv[1].get("round", {}).get("opened_at", 0), reverse=True)
        return matches[0]
    return (None, None)


# ---------- Commandes groupe ----------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_type = update.effective_chat.type
    if chat_type == ChatType.PRIVATE:
        await update.message.reply_text(
            "Salut ! 🎯 Je suis le bot Quiz.\n\n"
            "👉 En groupe : ta mere/l'organisateur lance /startquiz puis /sethost et /ask.\n"
            "👉 Toi en tant que joueur : fais /join dans le groupe, puis envoie tes reponses "
            "ICI en prive quand une question est posee.\n\n"
            "Cette conversation privee est indispensable pour que je puisse recevoir tes reponses "
            "et pour que le host recoive les siennes."
        )
    else:
        await update.message.reply_text(
            "Salut ! Je suis le bot Quiz 🎯\n\n"
            "Commandes :\n"
            "• /startquiz — activer le quiz (bouton)\n"
            "• /sethost — designer le poseur de questions\n"
            "• /join — rejoindre en tant que joueur\n"
            "• /ask <question> — (host) poser une question\n"
            "• /verdict — (host) cloturer le round sans gagnant\n"
            "• /leaderboard — voir le classement\n"
            "• /reset — (host/admin) remettre a zero\n"
            "• /stopquiz — desactiver\n\n"
            "⚠️ Les joueurs et le host doivent avoir DEMARRE le bot en PRIVE une fois "
            "(clic Start en DM), sinon je ne peux ni recevoir ni transmettre les reponses."
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
        "➡️ /sethost pour designer le poseur de questions\n"
        "➡️ /join pour rejoindre en tant que joueur\n"
        "➡️ /ask <question> pour demarrer un round\n\n"
        "⚠️ Chaque joueur (et le host) doit avoir demarre le bot en PRIVE une fois."
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
    user = update.effective_user
    chat = update.effective_chat

    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text("Cette commande doit etre tapee dans le groupe.")
        return

    if not await is_group_admin(update, user.id):
        state = get_chat(chat.id)
        if state.get("host_id") is not None:
            await update.message.reply_text("Seul un admin du groupe peut changer le host.")
            return

    target_user = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = update.message.reply_to_message.from_user

    if target_user is None:
        await update.message.reply_text(
            "Usage : reponds au message de la personne avec /sethost.\n"
            "(Cette personne doit d'abord avoir demarre le bot en PRIVE.)"
        )
        return

    # Verifier qu'on peut ecrire au host en PV
    try:
        await context.bot.send_chat_action(chat_id=target_user.id, action="typing")
    except TelegramError:
        await update.message.reply_text(
            f"⚠️ Je ne peux pas ecrire a {display_name(target_user)} en prive. "
            "Cette personne doit d'abord ouvrir le bot en DM et cliquer /start, "
            "puis refais /sethost."
        )
        return

    def mut(state):
        state["host_id"] = target_user.id
        state["host_name"] = display_name(target_user)

    update_chat(chat.id, mut)
    await update.message.reply_text(
        f"🎤 Poseur de questions : {display_name(target_user)}\n"
        "Les reponses des joueurs lui seront transmises en PRIVE."
    )


async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text("Fais /join dans le groupe du quiz.")
        return

    state = get_chat(chat.id)
    if not state.get("active"):
        await update.message.reply_text("Le quiz n'est pas encore actif. Tape /startquiz.")
        return

    # Verifier qu'on peut ecrire au joueur en PV
    can_dm = True
    try:
        await context.bot.send_chat_action(chat_id=user.id, action="typing")
    except TelegramError:
        can_dm = False

    if not can_dm:
        await update.message.reply_text(
            f"⚠️ {display_name(user)}, ouvre le bot en PRIVE et clique /start, "
            "puis refais /join ici. Sinon je ne peux pas recevoir tes reponses."
        )
        return

    def mut(s):
        p = s["players"].setdefault(str(user.id), {"name": display_name(user), "score": 0})
        p["name"] = display_name(user)
        s.setdefault("group_title", chat.title or "")

    update_chat(chat.id, mut)
    await update.message.reply_text(
        f"✅ {display_name(user)} a rejoint le quiz ! Envoie tes reponses en PRIVE au bot."
    )


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text("Utilise /ask dans le groupe.")
        return

    state = get_chat(chat.id)
    if not state.get("active"):
        await update.message.reply_text("Quiz inactif. /startquiz d'abord.")
        return
    if not state.get("host_id"):
        await update.message.reply_text("Aucun host designe. Utilise /sethost.")
        return
    if state["host_id"] != user.id:
        await update.message.reply_text("Seul le host peut poser une question.")
        return

    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text("Usage : /ask <ta question>")
        return

    def mut(s):
        s["round"] = {"question": question, "answered_users": [], "opened_at": time.time(), "closed": False}

    update_chat(chat.id, mut)

    # Prevenir le host en prive
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=(
                f"❓ Round ouvert dans « {chat.title or chat.id} »\n\n"
                f"Question : {question}\n\n"
                "Je vais te transmettre ici chaque reponse recue. "
                "Clique 🏆 sous la bonne reponse pour attribuer +2 pts et cloturer, "
                "ou tape /verdict dans le groupe pour cloturer sans gagnant."
            ),
        )
    except TelegramError:
        await update.message.reply_text(
            "⚠️ Je ne peux pas t'ecrire en prive (host). Ouvre le bot en DM puis /start, puis refais /ask."
        )
        return

    await update.message.reply_text(
        f"❓ Question :\n\n{question}\n\n"
        "📩 Joueurs : envoyez votre reponse en PRIVE au bot.\n"
        "Le host recevra les reponses et validera le gagnant."
    )


async def verdict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cloture manuelle sans gagnant (ou si le host veut annuler)."""
    user = update.effective_user
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text("Utilise /verdict dans le groupe.")
        return
    state = get_chat(chat.id)
    if not state.get("active") or not state.get("round"):
        await update.message.reply_text("Aucun round en cours.")
        return
    if state.get("host_id") != user.id:
        await update.message.reply_text("Seul le host peut cloturer le round.")
        return

    def mut(s):
        s["round"] = None

    update_chat(chat.id, mut)
    await update.message.reply_text("🔚 Round cloture sans gagnant.")


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


# ---------- Reponses en PRIVE ----------
async def private_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Un joueur envoie sa reponse en DM au bot."""
    msg = update.message
    if not msg or not msg.text or msg.text.startswith("/"):
        return
    user = msg.from_user

    group_chat_id, state = find_active_round_for_user(user.id)
    if not group_chat_id:
        await msg.reply_text(
            "Aucun round en cours pour toi. Fais /join dans le groupe et attends une question."
        )
        return

    if state.get("host_id") == user.id:
        await msg.reply_text("Tu es le host, tu ne joues pas ce round.")
        return

    # Une seule reponse par joueur par round
    round_ = state["round"]
    if user.id in round_.get("answered_users", []):
        await msg.reply_text("Tu as deja repondu pour ce round. Attends le verdict.")
        return

    def mut(s):
        s["round"].setdefault("answered_users", []).append(user.id)

    update_chat(group_chat_id, mut)

    # Confirmer au joueur
    await msg.reply_text("✅ Reponse envoyee au host. Attends le verdict.")

    # Transmettre au host avec bouton "Gagnant"
    host_id = state.get("host_id")
    if not host_id:
        return
    # callback_data doit rester court : award:<group_chat_id>:<user_id>
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🏆 Gagnant (+2)", callback_data=f"award:{group_chat_id}:{user.id}"),
    ]])
    try:
        await context.bot.send_message(
            chat_id=host_id,
            text=(
                f"📨 Reponse de {display_name(user)} :\n\n"
                f"« {msg.text} »"
            ),
            reply_markup=kb,
        )
    except TelegramError as e:
        logger.warning("Impossible d'envoyer au host: %s", e)


async def award_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        _, group_id_s, user_id_s = query.data.split(":")
        group_chat_id = int(group_id_s)
        winner_id = int(user_id_s)
    except Exception:
        await query.answer("Donnee invalide", show_alert=True)
        return

    state = get_chat(group_chat_id)
    if state.get("host_id") != query.from_user.id:
        await query.answer("Seul le host peut valider.", show_alert=True)
        return
    if not state.get("round"):
        await query.answer("Round deja cloture.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass
        return

    winner_name = state.get("players", {}).get(str(winner_id), {}).get("name", str(winner_id))
    question = state["round"].get("question", "")

    def mut(s):
        p = s["players"].setdefault(str(winner_id), {"name": winner_name, "score": 0})
        p["score"] += 2
        s["round"] = None

    update_chat(group_chat_id, mut)

    await query.answer("Gagnant enregistre !")
    try:
        await query.edit_message_text(query.message.text + f"\n\n✅ Declare gagnant (+2 pts).")
    except TelegramError:
        pass

    # Annonce dans le groupe
    try:
        await context.bot.send_message(
            chat_id=group_chat_id,
            text=(
                f"🏆 *Verdict*\n\n"
                f"Question : {question}\n"
                f"Gagnant : {winner_name} (+2 pts)"
            ),
            parse_mode="Markdown",
        )
    except TelegramError as e:
        logger.warning("Impossible d'annoncer dans le groupe: %s", e)


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
    app.add_handler(CallbackQueryHandler(award_callback, pattern=r"^award:"))

    # Reponses en PRIVE uniquement
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        private_answer,
    ))

    logger.info("Quiz bot demarre, polling en cours...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
