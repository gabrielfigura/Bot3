import requests
import json
import time
import schedule
from telegram.ext import Application, CommandHandler
from datetime import datetime, timezone
import asyncio
import logging
import os

# Configuração de logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configurações
API_URL = "https://api.casinoscores.com/svc-evolution-game-events/api/bacbo/latest"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "7703975421:AAG-CG5Who2xs4NlevJqB5TNvjjzeUEDz8o")
CHAT_ID = "-1002859771274"
CHECK_INTERVAL = 5
ROUND_DURATION = 30
SIGNAL_DEADLINE = 7
PATTERNS_FILE = "patterns.json"

# Carregar padrões
try:
    with open(PATTERNS_FILE, 'r') as f:
        PATTERNS = json.load(f)
except FileNotFoundError:
    logger.error(f"Arquivo {PATTERNS_FILE} não encontrado.")
    PATTERNS = []
except json.JSONDecodeError as e:
    logger.error(f"Erro ao decodificar {PATTERNS_FILE}: {e}")
    PATTERNS = []

# Estado do bot
last_game_id = None
current_streak = 0
last_message_id = None
gale_active = False
last_bet = None
last_pattern_id = None
GAME_HISTORY = []

async def fetch_latest_game():
    """Busca os dados mais recentes da API do CasinoScores."""
    try:
        headers = {}  # Ex.: {"Authorization": "Bearer SEU_TOKEN_API"}
        response = requests.get(API_URL, headers=headers, timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Erro ao buscar dados da API: {e}")
        return None

def load_game_history():
    """Carrega o histórico de jogos em memória."""
    return GAME_HISTORY

def save_game_history(history):
    """Salva o histórico de jogos em memória."""
    global GAME_HISTORY
    GAME_HISTORY = history[-100:]  # Limita a 100 resultados

def map_outcome_to_emoji(outcome):
    """Mapeia o resultado do jogo para emoji."""
    if outcome == "BankerWon":
        return "🔴"
    elif outcome == "PlayerWon":
        return "🔵"
    elif outcome == "Tie":
        return "🟡"
    return None

def check_pattern(history):
    """Verifica se algum padrão foi detectado no histórico."""
    if not PATTERNS:
        logger.warning("Nenhum padrão carregado. Verifique o arquivo patterns.json.")
        return None
    max_pattern_length = max(len(pattern['sequencia']) for pattern in PATTERNS)
    history_emojis = [map_outcome_to_emoji(game['data']['result']['outcome']) for game in history][-max_pattern_length:]
    for pattern in PATTERNS:
        pattern_seq = pattern['sequencia']
        if len(history_emojis) >= len(pattern_seq) and history_emojis[-len(pattern_seq):] == pattern_seq:
            logger.info(f"Padrão {pattern['id']} detectado: {pattern_seq}")
            return pattern
    return None

def determine_bet(pattern):
    """Determina a aposta com base na ação do padrão."""
    action = pattern['acao']
    seq = pattern['sequencia']
    last_result = seq[-1]
    first_result = seq[0]
    
    if action == "Entrar a favor":
        return "Banker" if last_result == "🔴" else "Player"
    elif action == "Entrar no oposto do último":
        return "Player" if last_result == "🔴" else "Banker"
    elif action == "Entrar contra":
        return "Player" if last_result == "🔴" else "Banker"
    elif action == "Entrar no lado que inicia":
        return "Banker" if first_result == "🔴" else "Player"
    elif action == "Seguir rompimento":
        return "Player" if last_result == "🔵" else "Banker"
    elif action == "Seguir alternância":
        return "Player" if last_result == "🔴" else "Banker"
    elif action == "Seguir nova cor":
        return "Player" if last_result == "🔵" else "Banker"
    elif action == "Seguir 🔴":
        return "Banker"
    elif action == "Seguir 🔵":
        return "Player"
    elif action == "Ignorar Tie e seguir 🔴":
        return "Banker"
    elif action == "Voltar para 🔵":
        return "Player"
    elif action == "Seguir pares":
        return "Banker" if seq[-2] == "🔴" else "Player"
    elif action == "Seguir ciclo":
        return "Banker" if first_result == "🔴" else "Player"
    elif action == "Novo início":
        return "Player" if first_result == "🔵" else "Banker"
    elif action == "Seguir padrão 2x":
        second_last = seq[-2]
        return "Banker" if second_last == "🔴" else "Player"
    return None

async def send_signal(context, pattern, bet):
    """Envia o sinal de aposta no Telegram."""
    global last_message_id, last_bet, last_pattern_id
    bet_emoji = "🔴" if bet == "Banker" else "🔵"
    message = f"""
ATENÇÃO PADRÃO {pattern['id']} DETECTADO
Entrar no {bet}: {bet_emoji}
Proteger o empate: 🟡
Fazer até 1 gale 🔥
Mais dinheiro e menos amigos 🤏
"""
    if last_message_id:
        try:
            await context.bot.delete_message(chat_id=CHAT_ID, message_id=last_message_id)
        except Exception as e:
            logger.warning(f"Erro ao deletar mensagem: {e}")
    sent_message = await context.bot.send_message(chat_id=CHAT_ID, text=message.strip())
    logger.info(f"Sinal enviado: {bet} para padrão {pattern['id']}")
    last_bet = bet
    last_pattern_id = pattern['id']
    last_message_id = None

async def validate_bet(context, game_data):
    """Valida o resultado da aposta."""
    global current_streak, gale_active, last_bet, last_pattern_id
    outcome = game_data['data']['result']['outcome']
    bet_won = (
        (last_bet == "Banker" and outcome == "BankerWon") or
        (last_bet == "Player" and outcome == "PlayerWon") or
        outcome == "Tie"
    )
    
    if bet_won:
        current_streak += 1
        message = f"""
Mais Dinheiro no bolso🤌
Placar de acertos: {current_streak} ✅
"""
        gale_active = False
    else:
        if not gale_active:
            gale_active = True
            message = f"""
Vamos entrar no 1 Gale🔥
"""
        else:
            message = f"""
Perdemos no 1 Gale😔, vamos pegar a outra rodada🤌
"""
            current_streak = 0
            gale_active = False
    
    await context.bot.send_message(chat_id=CHAT_ID, text=message.strip())
    logger.info(f"Validação: {'Acerto' if bet_won else 'Erro'}, Placar: {current_streak}")
    last_bet = None
    last_pattern_id = None

async def monitor_table(context):
    """Monitora a mesa e envia sinais quando necessário."""
    global last_game_id, last_message_id
    game_data = await fetch_latest_game()
    if not game_data:
        return

    game_id = game_data['id']
    if game_id == last_game_id:
        return

    history = load_game_history()
    history.append(game_data)
    save_game_history(history)

    pattern = check_pattern(history)
    if pattern:
        bet = determine_bet(pattern)
        if bet:
            try:
                started_at = datetime.strptime(game_data['data']['startedAt'], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                time_diff = (now - started_at).total_seconds()
                if time_diff < (ROUND_DURATION - SIGNAL_DEADLINE):
                    await send_signal(context, pattern, bet)
            except KeyError as e:
                logger.error(f"Erro ao processar startedAt: {e}")
                return

    if last_bet and game_data['data'].get('status') == "Resolved":
        await validate_bet(context, game_data)

    last_game_id = game_id

    if not last_bet and not last_message_id:
        message = """MONITORANDO A MESA🤌"""
        sent_message = await context.bot.send_message(chat_id=CHAT_ID, text=message.strip())
        last_message_id = sent_message.message_id

async def schedule_monitoring(app):
    """Agenda a verificação periódica da API."""
    while True:
        schedule.run_pending()
        await asyncio.sleep(CHECK_INTERVAL)

async def start(update, context):
    """Comando /start para iniciar o bot."""
    await update.message.reply_text("""Bot de monitoramento de Bac Bo iniciado! 🤌""")
    asyncio.create_task(schedule_monitoring(context.application))

async def main():
    """Função principal do bot."""
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN não configurado.")
        return
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    
    # Iniciar agendamento em uma tarefa separada
    asyncio.create_task(schedule_monitoring(app))
    
    # Iniciar polling
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
