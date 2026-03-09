import os
import asyncio
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
import pytz
from collections import Counter
import aiohttp
from telegram import Bot
from telegram.error import TelegramError
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "7905452331:AAGI8cYv9ReoFURjKO7I4iw6U1FdsIgqDdk")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "-1003870451338")
API_URL = "https://api.signals-house.com/validate/results?tableId=27&lastResult=13382685"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
}
ANGOLA_TZ = pytz.timezone('Africa/Luanda')
OUTCOME_MAP = {
    "Casa": "🔵", "Visitante": "🔴", "Tie": "🟡",
    "casa": "🔵", "visitante": "🔴", "tie": "🟡",
    "Home": "🔵", "Away": "🔴", "Draw": "🟡",
    "PlayerWon": "🔵", "BankerWon": "🔴",
    "Player": "🔵", "Banker": "🔴",
    "🔵": "🔵", "🔴": "🔴", "🟡": "🟡",
}

API_POLL_INTERVAL = 2.0
SIGNAL_COOLDOWN_DURATION = 4
GREEN_STICKER_ID = "CAACAgQAAxkBAAMCaanfUxV0k3upwRhvlpq9XyODGX4AAvAbAAL92lFROjONnjCocw86BA"

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-5s | %(message)s')
logger = logging.getLogger("FootballStudioBot")
bot = Bot(token=TELEGRAM_BOT_TOKEN)

state: Dict[str, Any] = {
    "history": [], "last_round_id": None, "waiting_for_result": False,
    "last_signal_color": None, "martingale_count": 0, "entrada_message_id": None,
    "martingale_message_ids": [], "greens_seguidos": 0, "total_greens": 0,
    "greens_sem_gale": 0, "greens_gale_1": 0, "greens_gale_2": 0,
    "total_empates": 0, "total_losses": 0, "last_signal_pattern": None,
    "last_signal_sequence": None, "last_signal_round_id": None,
    "signal_cooldown_until": 0.0, "analise_message_id": None,
    "last_reset_date": None, "last_analise_refresh": 0.0,
    "last_result_round_id": None, "player_score_last": None,
    "banker_score_last": None, "new_result_added": False,
}

async def send_to_channel(text: str, parse_mode="HTML") -> Optional[int]:
    try:
        msg = await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=text, parse_mode=parse_mode, disable_web_page_preview=True)
        return msg.message_id
    except Exception as e:
        logger.error(f"Erro ao enviar texto: {e}")
        return None

async def send_sticker_to_channel(sticker_id: str) -> Optional[int]:
    try:
        msg = await bot.send_sticker(chat_id=TELEGRAM_CHANNEL_ID, sticker=sticker_id)
        return msg.message_id
    except Exception as e:
        logger.error(f"Erro ao enviar sticker: {e}")
        return None

async def send_error_to_channel(error_msg: str):
    timestamp = datetime.now(ANGOLA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    text = f"⚠️ <b>ERRO DETECTADO</b> ⚠️\n<code>{timestamp}</code>\n\n{error_msg}"
    await send_to_channel(text)

async def delete_messages(message_ids: List[int]):
    if not message_ids:
        return
    for mid in message_ids[:]:
        try:
            await bot.delete_message(TELEGRAM_CHANNEL_ID, mid)
        except:
            pass

def should_reset_placar() -> bool:
    now = datetime.now(ANGOLA_TZ)
    if state["last_reset_date"] != now.date():
        state["last_reset_date"] = now.date()
        return True
    if state["total_losses"] >= 10:
        return True
    return False

def reset_placar_if_needed():
    if should_reset_placar():
        for k in ["total_greens", "greens_sem_gale", "greens_gale_1", "greens_gale_2",
                  "total_empates", "total_losses", "greens_seguidos"]:
            state[k] = 0
        logger.info("Placar resetado")

def calcular_acertividade() -> str:
    total = state["total_greens"] + state["total_losses"]
    return "—" if total == 0 else f"{(state['total_greens'] / total * 100):.1f}%"

def format_placar() -> str:
    acert = calcular_acertividade()
    return (
        "🏆 <b>RESUMO</b> 🏆\n"
        f"✅ Sem gale: <b>{state['greens_sem_gale']}</b>\n"
        f"🔄 Gale 1: <b>{state['greens_gale_1']}</b>\n"
        f"🔄 Gale 2: <b>{state['greens_gale_2']}</b>\n"
        f"⛔ Losses: <b>{state['total_losses']}</b>\n"
        f"🎯 Greens: <b>{state['total_greens']}</b>  |  {acert}"
    )

def format_analise_text() -> str:
    return "⚽ <b>ANALISANDO O JOGO...</b> ⚽\n<i>Buscando a melhor oportunidade!</i>"

async def refresh_analise_message():
    await delete_analise_message()
    msg_id = await send_to_channel(format_analise_text())
    if msg_id:
        state["analise_message_id"] = msg_id

async def delete_analise_message():
    if state["analise_message_id"] is not None:
        await delete_messages([state["analise_message_id"]])
        state["analise_message_id"] = None

async def fetch_api(session: aiohttp.ClientSession) -> Optional[Dict]:
    try:
        async with session.get(API_URL, headers=HEADERS, timeout=7) as resp:
            if resp.status == 200:
                return await resp.json()
            return None
    except:
        return None

async def update_history_from_api(session):
    reset_placar_if_needed()
    data = await fetch_api(session)
    if not data:
        return
    try:
        items = data.get("data", [])
        if isinstance(items, list) and len(items) > 0:
            latest = items[0]
            round_id = latest.get("id")
            if not round_id:
                return
            outcome_raw = latest.get("result")
            if not outcome_raw:
                return
            score = latest.get("score")
            outcome = OUTCOME_MAP.get(outcome_raw)
            if not outcome:
                s = str(outcome_raw or "").lower()
           if any(x in s for x in ["casa", "home", "player"]): outcome = "🔵"
elif any(x in s for x in ["visitante", "away", "banker"]): outcome = "🔴"
elif any(x in s for x in ["tie", "empate", "draw"]): outcome = "🟡"
            if outcome and state["last_round_id"] != round_id:
                state["last_round_id"] = round_id
                state["history"].append(outcome)
                state["player_score_last"] = None
                state["banker_score_last"] = None
                if len(state["history"]) > 200:
                    state["history"].pop(0)
                logger.info(f"Resultado novo: {outcome} (round {round_id}, score={score})")
                state["new_result_added"] = True
                state["signal_cooldown_until"] = datetime.now().timestamp() + 0.5
        elif isinstance(items, dict):
            round_id = items.get("id")
            if not round_id:
                return
            outcome_raw = (items.get("result") or {}).get("outcome") if isinstance(items.get("result"), dict) else items.get("result")
            if not outcome_raw:
                return
            outcome = OUTCOME_MAP.get(outcome_raw)
            if not outcome:
                s = str(outcome_raw or "").lower()
               if any(x in s for x in ["casa", "home", "player"]): outcome = "🔵"
elif any(x in s for x in ["visitante", "away", "banker"]): outcome = "🔴"
elif any(x in s for x in ["tie", "empate", "draw"]): outcome = "🟡"
            if outcome and state["last_round_id"] != round_id:
                state["last_round_id"] = round_id
                state["history"].append(outcome)
                state["player_score_last"] = None
                state["banker_score_last"] = None
                if len(state["history"]) > 200:
                    state["history"].pop(0)
                logger.info(f"Resultado novo: {outcome} (round {round_id})")
                state["new_result_added"] = True
                state["signal_cooldown_until"] = datetime.now().timestamp() + 0.5
    except Exception as e:
        logger.debug(f"Erro processando API: {e}")

def oposto(cor: str) -> str:
    return "🔵" if cor == "🔴" else "🔴"

# ========== ESTRATÉGIAS PARA FOOTBALL STUDIO ==========

def estrategia_tendencia_forte(hist: List[str]):
    """Se uma cor apareceu 4+ vezes nos últimos 5, seguir a tendência."""
    if len(hist) < 5:
        return None
    window = hist[-5:]
    counts = Counter(c for c in window if c in ("🔵", "🔴"))
    if not counts:
        return None
    cor_dominante, qtd = counts.most_common(1)[0]
    if qtd >= 4:
        return ("Tendência Forte", cor_dominante)
    return None

def estrategia_seguir_dupla(hist: List[str]):
    """Após duas cores iguais seguidas, apostar que a terceira repete."""
    if len(hist) < 2:
        return None
    if hist[-1] == hist[-2] and hist[-1] in ("🔵", "🔴"):
        return ("Seguir Dupla", hist[-1])
    return None

def estrategia_quebra_alternancia(hist: List[str]):
    """Detecta padrão ABAB e aposta na quebra (continuação do último)."""
    if len(hist) < 4:
        return None
    last_four = hist[-4:]
    if (last_four[0] != last_four[1] and
        last_four[0] == last_four[2] and
        last_four[1] == last_four[3] and
        all(c in ("🔵", "🔴") for c in last_four)):
        return ("Quebra de Alternância", last_four[-1])
    return None

def estrategia_reversao_longa(hist: List[str]):
    """Após 5+ repetições da mesma cor, apostar na reversão."""
    if len(hist) < 5:
        return None
    streak = 1
    for i in range(len(hist) - 2, -1, -1):
        if hist[i] == hist[-1] and hist[i] in ("🔵", "🔴"):
            streak += 1
        else:
            break
    if streak >= 5:
        return ("Reversão Após Streak", oposto(hist[-1]))
    return None

def gerar_sinal_estrategia(history: List[str], player_score=None, banker_score=None):
    if len(history) < 3 or state["waiting_for_result"]:
        return None, None

    # Prioridade 1: Tendência forte
    res = estrategia_tendencia_forte(history)
    if res:
        return res

    # Prioridade 2: Reversão após streak longo (5+)
    res = estrategia_reversao_longa(history)
    if res:
        return res

    # Sistema de votação para as restantes
    votos = {"🔵": 0, "🔴": 0}
    melhor_nome = ""

    res_dupla = estrategia_seguir_dupla(history)
    if res_dupla:
        nome, cor = res_dupla
        votos[cor] += 2.0
        melhor_nome = nome

    res_alt = estrategia_quebra_alternancia(history)
    if res_alt:
        nome, cor = res_alt
        votos[cor] += 1.5
        if not melhor_nome:
            melhor_nome = nome

    if votos["🔵"] > 0 or votos["🔴"] > 0:
        if abs(votos["🔵"] - votos["🔴"]) >= 1.5:
            cor_final = "🔵" if votos["🔵"] > votos["🔴"] else "🔴"
            return (melhor_nome, cor_final)

    return None, None

# ========== FIM DAS ESTRATÉGIAS ==========

def main_entry_text(color: str) -> str:
    team_name = "CASA 🔵" if color == "🔵" else "VISITANTE🔴"
    return (
        f"⚽ ENTRADA CONFIRMADA ⚽\n\n"
        f"Apostar em: {team_name}\n\n"
        f"Proteger no EMPATE 🟡"
    )

async def send_gale_warning(level: int):
    if level not in (1, 2):
        return
    text = f"🔄 <b>GALE {level}</b> 🔄\nManter a aposta na mesma equipa!"
    msg_id = await send_to_channel(text)
    if msg_id:
        state["martingale_message_ids"].append(msg_id)

async def clear_gale_messages():
    await delete_messages(state["martingale_message_ids"])
    state["martingale_message_ids"] = []

async def resolve_after_result():
    if not state.get("waiting_for_result") or not state.get("last_signal_color"):
        return
    if state["last_result_round_id"] == state["last_round_id"]:
        return
    if not state["history"]:
        return
    if state["last_signal_round_id"] >= state["last_round_id"] and state["martingale_count"] == 0:
        return

    last_outcome = state["history"][-1]
    state["last_result_round_id"] = state["last_round_id"]
    target = state["last_signal_color"]
    acertou = last_outcome == target
    is_tie = last_outcome == "🟡"

    if acertou or is_tie:
        state["total_greens"] += 1
        state["greens_seguidos"] += 1
        if state["martingale_count"] == 0: state["greens_sem_gale"] += 1
        elif state["martingale_count"] == 1: state["greens_gale_1"] += 1
        elif state["martingale_count"] == 2: state["greens_gale_2"] += 1
        await send_sticker_to_channel(GREEN_STICKER_ID)
        await send_to_channel(format_placar())
        await send_to_channel(f"SEQUÊNCIA: {state['greens_seguidos']} greens 🔥")
        await clear_gale_messages()
        state.update({
            "waiting_for_result": False, "last_signal_color": None,
            "martingale_count": 0, "entrada_message_id": None,
            "last_signal_pattern": None, "last_signal_sequence": None,
            "last_signal_round_id": None,
            "signal_cooldown_until": datetime.now().timestamp() + SIGNAL_COOLDOWN_DURATION
        })
        await refresh_analise_message()
        return

    state["martingale_count"] += 1

    if state["martingale_count"] == 1:
        await send_gale_warning(1)
        return
    elif state["martingale_count"] == 2:
        await send_gale_warning(2)
        return

    if state["martingale_count"] >= 3:
        state["greens_seguidos"] = 0
        state["total_losses"] += 1
        await send_to_channel("🟥 <b>LOSS</b> 🟥")
        await send_to_channel(format_placar())
        await clear_gale_messages()
        state.update({
            "waiting_for_result": False, "last_signal_color": None,
            "martingale_count": 0, "entrada_message_id": None,
            "last_signal_pattern": None, "last_signal_sequence": None,
            "last_signal_round_id": None,
            "signal_cooldown_until": datetime.now().timestamp() + SIGNAL_COOLDOWN_DURATION
        })
        reset_placar_if_needed()
        await refresh_analise_message()

async def try_send_signal():
    now = datetime.now().timestamp()
    if state["waiting_for_result"]:
        await delete_analise_message()
        return
    if now < state["signal_cooldown_until"]:
        return
    if len(state["history"]) < 3:
        return
    if not state["new_result_added"]:
        return
    state["new_result_added"] = False
    padrao, cor = gerar_sinal_estrategia(
        state["history"],
        state.get("player_score_last"),
        state.get("banker_score_last")
    )
    if not cor:
        await refresh_analise_message()
        return
    seq = "".join(state["history"][-6:])
    if state["last_signal_pattern"] == padrao and state["last_signal_sequence"] == seq:
        await refresh_analise_message()
        return
    await delete_analise_message()
    state["martingale_message_ids"] = []
    msg_id = await send_to_channel(main_entry_text(cor))
    if msg_id:
        state["entrada_message_id"] = msg_id
        state["waiting_for_result"] = True
        state["last_signal_color"] = cor
        state["martingale_count"] = 0
        state["last_signal_pattern"] = padrao
        state["last_signal_sequence"] = seq
        state["last_signal_round_id"] = state["last_round_id"]
        state["signal_cooldown_until"] = now + SIGNAL_COOLDOWN_DURATION
        logger.info(f"Sinal enviado → {cor} ({padrao})")

async def api_worker():
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await update_history_from_api(session)
                await asyncio.sleep(0.3)
                await resolve_after_result()
                await try_send_signal()
            except Exception as e:
                logger.debug(f"Erro loop principal: {e}")
            await asyncio.sleep(API_POLL_INTERVAL)

async def main():
    logger.info("Football Studio Bot iniciado...")
    await send_to_channel("⚽ Bot online – Football Studio – Gale 2 ativo")
    await api_worker()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot parado pelo usuário")
    except Exception as e:
        logger.critical("Erro fatal", exc_info=True)
