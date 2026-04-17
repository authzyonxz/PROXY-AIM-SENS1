import logging
import requests
import io
import json
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ─── CONFIGURAÇÕES ───────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "8644462603:AAFQ2BUuAB58un-QjBgAHvTO5vrERZtdfO4")
API_BASE = os.getenv("API_BASE", "http://212.227.7.153:9945")
API_KEY = os.getenv("API_KEY", "43FUHF78FWIUTPULMH")

DEFAULT_ADMIN_IDS = [int(os.getenv("ADMIN_ID", 7499536776)), 5881589518]


def parse_admin_ids() -> list[int]:
    configured_ids = []
    raw_admin_ids = os.getenv("ADMIN_IDS", "")

    if raw_admin_ids.strip():
        for item in raw_admin_ids.replace(";", ",").split(","):
            item = item.strip()
            if item.isdigit():
                configured_ids.append(int(item))

    configured_ids.extend(DEFAULT_ADMIN_IDS)
    return sorted(set(configured_ids))


ADMIN_IDS = parse_admin_ids()
PRIMARY_ADMIN_ID = ADMIN_IDS[0]
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", PRIMARY_ADMIN_ID))

# Arquivo para salvar revendedores (Simples JSON para persistência)
RESELLERS_FILE = "resellers.json"

# ─── ESTADOS DA CONVERSA ─────────────────────────────────────────────────────
(
    GERAR_QTD, GERAR_DIAS,
    DELETAR_KEY,
    CHECAR_KEY,
    UPDATE_KEY, UPDATE_IP,
    ADD_RESELLER_ID, ADD_RESELLER_SALDO,
    REM_RESELLER_ID,
) = range(9)

# ─── LOGGING ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── BANCO DE DADOS LOCAL (REVENDA) ──────────────────────────────────────────
def load_resellers():
    if os.path.exists(RESELLERS_FILE):
        try:
            with open(RESELLERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_resellers(data):
    with open(RESELLERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ─── HELPERS ─────────────────────────────────────────────────────────────────
def is_admin(update: Update) -> bool:
    return update.effective_user.id in ADMIN_IDS


def is_admin_id(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_reseller(user_id: int) -> bool:
    resellers = load_resellers()
    return str(user_id) in resellers


def get_reseller_balance(user_id: int) -> int:
    resellers = load_resellers()
    return resellers.get(str(user_id), {}).get("balance", 0)


def update_reseller_balance(user_id: int, amount: int):
    resellers = load_resellers()
    uid = str(user_id)
    if uid in resellers:
        resellers[uid]["balance"] += amount
        save_resellers(resellers)
        return True
    return False


def api_get(endpoint: str, params: dict) -> dict:
    try:
        params["key"] = API_KEY
        r = requests.get(f"{API_BASE}{endpoint}", params=params, timeout=10)
        r.raise_for_status()
        data = r.json() if r.text else {}
        is_ok = data.get("status") == "success"
        return {"ok": is_ok, "data": data, "raw": r.text}
    except Exception as e:
        logger.error(f"Erro na chamada API: {e}")
        return {"ok": False, "error": str(e)}


async def send_log(context: ContextTypes.DEFAULT_TYPE, message: str):
    try:
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=f"🔔 <b>LOG DE ATIVIDADE</b>\n\n{message}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Erro ao enviar log: {e}")


async def get_telegram_user_info(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> dict:
    info = {
        "username": None,
        "first_name": None,
        "full_name": None,
    }

    try:
        chat = await context.bot.get_chat(chat_id=user_id)
        username = getattr(chat, "username", None)
        first_name = getattr(chat, "first_name", None)
        last_name = getattr(chat, "last_name", None)
        full_name = " ".join(part for part in [first_name, last_name] if part).strip() or None

        info.update({
            "username": username,
            "first_name": first_name,
            "full_name": full_name,
        })
    except Exception as e:
        logger.warning(f"Não foi possível obter os dados públicos do usuário {user_id}: {e}")

    return info


def format_username(username: str | None) -> str:
    return f"@{username}" if username else "não encontrado"


def format_actor_label(user) -> str:
    username = format_username(getattr(user, "username", None))
    return f"ID {user.id} | Username: {username}"


def menu_keyboard(user_id: int):
    buttons = [
        [InlineKeyboardButton("🔑 Gerar Keys", callback_data="menu_gerar")],
        [InlineKeyboardButton("🔍 Checar Key", callback_data="menu_checar")],
        [InlineKeyboardButton("🌐 Atualizar IP", callback_data="menu_update_ip")],
    ]

    if is_admin_id(user_id):
        buttons.append([InlineKeyboardButton("🗑️ Deletar Key", callback_data="menu_deletar")])
        buttons.append([InlineKeyboardButton("👥 Revendedores", callback_data="menu_resellers")])
        buttons.append([InlineKeyboardButton("📊 Estatísticas", callback_data="menu_stats")])

    return InlineKeyboardMarkup(buttons)


BANNER = (
    "╔══════════════════════════════╗\n"
    "║   🤖  <b>PAINEL DE CONTROLE</b>      ║\n"
    "║      Gerenciador de Keys     ║\n"
    "╚══════════════════════════════╝\n\n"
    "Escolha uma opção abaixo 👇"
)

# ─── /start ───────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()

    user_id = update.effective_user.id
    if not is_admin(update) and not is_reseller(user_id):
        await update.message.reply_text("⛔ Acesso negado.")
        return ConversationHandler.END

    msg = BANNER
    if is_reseller(user_id):
        balance = get_reseller_balance(user_id)
        msg += f"\n\n💰 <b>Seu Saldo:</b> {balance} keys"

    await update.message.reply_text(
        msg,
        parse_mode="HTML",
        reply_markup=menu_keyboard(user_id),
    )
    return ConversationHandler.END


# ─── CALLBACK DO MENU ─────────────────────────────────────────────────────────
async def menu_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()

    if not is_admin(update) and not is_reseller(user_id):
        await query.edit_message_text("⛔ Acesso negado.")
        return ConversationHandler.END

    data = query.data

    if data == "menu_gerar":
        await query.edit_message_text(
            "🔑 <b>GERAR KEYS</b>\n\n"
            "Quantas chaves você deseja gerar? (Ex: 1, 5, 10):",
            parse_mode="HTML",
        )
        return GERAR_QTD

    elif data == "menu_deletar" and is_admin_id(user_id):
        await query.edit_message_text(
            "🗑️ <b>DELETAR KEY</b>\n\n"
            "Digite a <b>key</b> que deseja deletar:",
            parse_mode="HTML",
        )
        return DELETAR_KEY

    elif data == "menu_checar":
        await query.edit_message_text(
            "🔍 <b>CHECAR KEY</b>\n\n"
            "Digite a <b>key</b> que deseja verificar:",
            parse_mode="HTML",
        )
        return CHECAR_KEY

    elif data == "menu_update_ip":
        await query.edit_message_text(
            "🌐 <b>ATUALIZAR IP</b>\n\n"
            "Digite a <b>key</b> que deseja atualizar o IP:",
            parse_mode="HTML",
        )
        return UPDATE_KEY

    elif data == "menu_resellers" and is_admin_id(user_id):
        resellers = load_resellers()
        msg = "👥 <b>GERENCIAR REVENDEDORES</b>\n\n"
        if not resellers:
            msg += "Nenhum revendedor cadastrado."
        else:
            for rid, info in resellers.items():
                username = format_username(info.get("username"))
                msg += (
                    f"• ID: <code>{rid}</code> | Username: {username} | "
                    f"Saldo: {info.get('balance', 0)}\n"
                )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Adicionar", callback_data="reseller_add"),
             InlineKeyboardButton("➖ Remover", callback_data="reseller_rem")],
            [InlineKeyboardButton("🏠 Voltar", callback_data="menu_voltar")]
        ])
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)
        return ConversationHandler.END

    elif data == "reseller_add" and is_admin_id(user_id):
        await query.edit_message_text("👤 Digite o <b>ID do Telegram</b> do novo revendedor:", parse_mode="HTML")
        return ADD_RESELLER_ID

    elif data == "reseller_rem" and is_admin_id(user_id):
        await query.edit_message_text("👤 Digite o <b>ID do Telegram</b> para remover:", parse_mode="HTML")
        return REM_RESELLER_ID

    elif data == "menu_stats" and is_admin_id(user_id):
        resellers = load_resellers()
        total_balance = sum(r['balance'] for r in resellers.values())
        await query.edit_message_text(
            "📊 <b>ESTATÍSTICAS</b>\n\n"
            f"👥 <b>Revendedores:</b> {len(resellers)}\n"
            f"💰 <b>Total Saldo Revendas:</b> {total_balance}\n"
            f"🛡️ <b>Admins:</b> {', '.join(str(admin_id) for admin_id in ADMIN_IDS)}\n"
            f"📅 <b>Data:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Voltar", callback_data="menu_voltar")]])
        )
        return ConversationHandler.END

    elif data == "menu_voltar":
        msg = BANNER
        if is_reseller(user_id):
            balance = get_reseller_balance(user_id)
            msg += f"\n\n💰 <b>Seu Saldo:</b> {balance} keys"
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=menu_keyboard(user_id))
        return ConversationHandler.END


# ─── FLUXO: GERAR KEYS ───────────────────────────────────────────────────────
async def gerar_qtd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    qtd_txt = update.message.text.strip()

    if not qtd_txt.isdigit() or int(qtd_txt) <= 0:
        await update.message.reply_text("❌ Número inválido. Digite um número positivo:")
        return GERAR_QTD

    qtd = int(qtd_txt)

    if not is_admin(update):
        balance = get_reseller_balance(user_id)
        if qtd > balance:
            await update.message.reply_text(
                f"❌ Saldo insuficiente! Você tem apenas {balance} créditos.",
                reply_markup=menu_keyboard(user_id)
            )
            return ConversationHandler.END

    ctx.user_data["gerar_qtd"] = qtd
    await update.message.reply_text(f"✅ Qtd: {qtd}\nInforme os <b>dias</b> de validade:", parse_mode="HTML")
    return GERAR_DIAS


async def gerar_dias(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    dias_txt = update.message.text.strip()
    if not dias_txt.isdigit():
        await update.message.reply_text("❌ Dias inválidos. Digite um número:")
        return GERAR_DIAS

    qtd = ctx.user_data["gerar_qtd"]
    dias = int(dias_txt)

    await update.message.reply_text(f"⏳ Gerando {qtd} chaves...")

    keys_geradas = []
    for _ in range(qtd):
        resp = api_get("/generate", {"days": dias})
        if resp["ok"]:
            keys_geradas.append(resp["data"].get("key"))

    if keys_geradas:
        if not is_admin(update):
            update_reseller_balance(user_id, -len(keys_geradas))

        actor_type = "Admin" if is_admin(update) else "Revendedor"
        await send_log(
            ctx,
            f"👤 <b>{actor_type}</b> {format_actor_label(update.effective_user)} gerou {len(keys_geradas)} keys.\n\nKeys:\n<code>"
            + "\n".join(keys_geradas)
            + "</code>"
        )

        txt_content = "\n".join(keys_geradas)
        file_stream = io.BytesIO(txt_content.encode("utf-8"))
        file_stream.name = f"keys_{dias}dias.txt"

        await update.message.reply_document(
            document=file_stream,
            caption=f"📄 {len(keys_geradas)} keys geradas com sucesso!",
            reply_markup=menu_keyboard(user_id)
        )
    else:
        await update.message.reply_text("❌ Falha ao gerar chaves na API.", reply_markup=menu_keyboard(user_id))

    return ConversationHandler.END


# ─── FLUXO: GERENCIAR REVENDEDORES ───────────────────────────────────────────
async def add_reseller_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rid = update.message.text.strip()

    if not rid.isdigit():
        await update.message.reply_text("❌ ID inválido. Digite apenas números:")
        return ADD_RESELLER_ID

    ctx.user_data["new_reseller_id"] = rid
    await update.message.reply_text("💰 Qual o <b>saldo inicial</b> do revendedor?", parse_mode="HTML")
    return ADD_RESELLER_SALDO


async def add_reseller_saldo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    saldo_txt = update.message.text.strip()
    if not saldo_txt.isdigit():
        await update.message.reply_text("❌ Saldo inválido. Digite um número:")
        return ADD_RESELLER_SALDO

    rid = ctx.user_data["new_reseller_id"]
    rid_int = int(rid)
    saldo = int(saldo_txt)

    user_info = await get_telegram_user_info(ctx, rid_int)
    username = user_info.get("username")
    full_name = user_info.get("full_name")

    resellers = load_resellers()
    resellers[rid] = {
        "balance": saldo,
        "username": username,
        "name": full_name,
        "added_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }
    save_resellers(resellers)

    username_text = format_username(username)
    name_text = full_name or "não encontrado"

    await update.message.reply_text(
        "✅ Revendedor adicionado com sucesso!\n\n"
        f"🆔 ID: <code>{rid}</code>\n"
        f"👤 Username: {username_text}\n"
        f"📛 Nome: {name_text}\n"
        f"💰 Saldo: {saldo} créditos",
        parse_mode="HTML",
        reply_markup=menu_keyboard(update.effective_user.id)
    )

    await send_log(
        ctx,
        "➕ <b>Novo revendedor adicionado</b>\n"
        f"👮 Admin: {format_actor_label(update.effective_user)}\n"
        f"🆔 ID do revendedor: <code>{rid}</code>\n"
        f"👤 Username do revendedor: {username_text}\n"
        f"📛 Nome do revendedor: {name_text}\n"
        f"💰 Saldo inicial: {saldo}"
    )
    return ConversationHandler.END


async def rem_reseller_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rid = update.message.text.strip()
    resellers = load_resellers()

    if rid in resellers:
        removed_info = resellers[rid]
        del resellers[rid]
        save_resellers(resellers)

        await update.message.reply_text(
            f"✅ Revendedor <code>{rid}</code> removido.",
            parse_mode="HTML",
            reply_markup=menu_keyboard(update.effective_user.id)
        )
        await send_log(
            ctx,
            "➖ <b>Revendedor removido</b>\n"
            f"👮 Admin: {format_actor_label(update.effective_user)}\n"
            f"🆔 ID do revendedor: <code>{rid}</code>\n"
            f"👤 Username do revendedor: {format_username(removed_info.get('username'))}"
        )
    else:
        await update.message.reply_text(
            "❌ Revendedor não encontrado.",
            reply_markup=menu_keyboard(update.effective_user.id)
        )

    return ConversationHandler.END


# ─── FLUXO: DELETAR KEY ──────────────────────────────────────────────────────
async def deletar_key(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    resp = api_get("/delete", {"generated_key": key})

    if resp["ok"]:
        await update.message.reply_text(
            f"✅ Key <code>{key}</code> deletada com sucesso!",
            parse_mode="HTML",
            reply_markup=menu_keyboard(update.effective_user.id)
        )
        await send_log(ctx, f"🗑️ Admin {format_actor_label(update.effective_user)} deletou a key: <code>{key}</code>")
    else:
        await update.message.reply_text(
            f"❌ Erro ao deletar: {resp.get('error')}",
            reply_markup=menu_keyboard(update.effective_user.id)
        )

    return ConversationHandler.END


# ─── FLUXO: CHECAR KEY ───────────────────────────────────────────────────────
async def checar_key(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    resp = api_get("/check", {"generated_key": key})

    if resp["ok"]:
        data = resp["data"]
        raw_resp = resp.get("raw", "{}")
        msg = (
            f"🔍 <b>DETALHES DA KEY</b>\n\n"
            f"🔑 <b>Key:</b> <code>{key}</code>\n"
            f"📅 <b>Expira em:</b> {data.get('expiry_date') or data.get('expiration') or data.get('expires_at') or 'Não encontrado'}\n"
            f"🌐 <b>IP Vinculado:</b> {data.get('ip') or 'Nenhum'}\n"
            f"✅ <b>Status:</b> Ativa\n\n"
            f"⚙️ <b>Debug API:</b> <code>{raw_resp}</code>"
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=menu_keyboard(update.effective_user.id))
    else:
        raw_resp = resp.get("raw", "Sem resposta")
        await update.message.reply_text(
            f"❌ Erro na API: {resp.get('error')}\nDebug: <code>{raw_resp}</code>",
            parse_mode="HTML",
            reply_markup=menu_keyboard(update.effective_user.id)
        )

    return ConversationHandler.END


# ─── FLUXO: ATUALIZAR IP ─────────────────────────────────────────────────────
async def update_key_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["update_key"] = update.message.text.strip()
    await update.message.reply_text("🌐 Digite o <b>novo IP</b> para vincular:", parse_mode="HTML")
    return UPDATE_IP


async def update_ip_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    key = ctx.user_data["update_key"]
    new_ip = update.message.text.strip()

    resp = api_get("/update", {"generated_key": key, "new_ip": new_ip})

    if resp["ok"]:
        await update.message.reply_text(
            f"✅ IP da key <code>{key}</code> atualizado para <code>{new_ip}</code>!",
            parse_mode="HTML",
            reply_markup=menu_keyboard(update.effective_user.id)
        )
        await send_log(ctx, f"🌐 IP Atualizado: Key <code>{key}</code> -> <code>{new_ip}</code>")
    else:
        raw_resp = resp.get("raw", "Sem resposta bruta")
        error_msg = resp.get("error") or resp.get("data", {}).get("message") or f"Erro na API (Raw: {raw_resp})"
        await update.message.reply_text(f"❌ Erro ao atualizar: {error_msg}", reply_markup=menu_keyboard(update.effective_user.id))

    return ConversationHandler.END


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    logger.info(f"Admins carregados: {ADMIN_IDS}")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_callback)],
        states={
            GERAR_QTD: [MessageHandler(filters.TEXT & ~filters.COMMAND, gerar_qtd)],
            GERAR_DIAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, gerar_dias)],
            DELETAR_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, deletar_key)],
            CHECAR_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, checar_key)],
            UPDATE_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_key_input)],
            UPDATE_IP: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_ip_input)],
            ADD_RESELLER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_reseller_id)],
            ADD_RESELLER_SALDO: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_reseller_saldo)],
            REM_RESELLER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, rem_reseller_id)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CallbackQueryHandler(menu_callback, pattern="^menu_voltar$")
        ],
        per_message=False,
        allow_reentry=True
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    logger.info("Bot iniciado...")
    app.run_polling()


if __name__ == "__main__":
    main()
