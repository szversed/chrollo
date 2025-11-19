import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import time
import os
from collections import defaultdict

MINHA_GUILD_ID = 1436733268912242790

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

fila_carentes = []
active_users = set()
active_channels = {}
user_genders = {}
user_preferences = {}
PERMANENT_BLOCKS = {}
ACCEPT_TIMEOUT = 300
CHANNEL_DURATION = 10 * 60

ENCOUNTER_HISTORY = {}
setup_channel_id = None
canal_bloqueado = False
main_message_id = None
user_messages = {}
user_queues = {}
user_queue_time = {}

# === NOVAS VARIÁVEIS PARA OS SISTEMAS ===
COOLDOWN_DURATION = 24 * 60 * 60  # 24 horas em segundos
STRIKE_LIMIT = 3
STRIKE_BLOCK_DURATION = 10 * 60  # 10 minutos em segundos

# Dicionários para os novos sistemas
cooldown_pairs = {}  # {frozenset({user1_id, user2_id}): expiration_time}
user_strikes = defaultdict(int)  # {user_id: strike_count}
user_strike_expiry = {}  # {user_id: expiry_time}
strike_blocked_users = {}  # {user_id: unblock_time}
user_pending_invites = defaultdict(list)  # {user_id: [invite_timestamps]}

def pair_key(u1_id, u2_id):
    return frozenset({u1_id, u2_id})

def is_permanently_blocked(u1_id, u2_id):
    key = pair_key(u1_id, u2_id)
    return key in PERMANENT_BLOCKS

def set_permanent_block(u1_id, u2_id):
    key = pair_key(u1_id, u2_id)
    PERMANENT_BLOCKS[key] = True

def have_encountered(u1_id, u2_id):
    key = pair_key(u1_id, u2_id)
    return key in ENCOUNTER_HISTORY

def mark_encounter(u1_id, u2_id):
    key = pair_key(u1_id, u2_id)
    ENCOUNTER_HISTORY[key] = True

# === NOVAS FUNÇÕES PARA COOLDOWN ===
def is_on_cooldown(u1_id, u2_id):
    """Verifica se um par está em cooldown"""
    key = pair_key(u1_id, u2_id)
    if key in cooldown_pairs:
        if time.time() < cooldown_pairs[key]:
            return True
        else:
            # Cooldown expirado, remove do dicionário
            del cooldown_pairs[key]
    return False

def set_cooldown(u1_id, u2_id):
    """Define cooldown de 24 horas para um par"""
    key = pair_key(u1_id, u2_id)
    cooldown_pairs[key] = time.time() + COOLDOWN_DURATION

# === NOVAS FUNÇÕES PARA STRIKE SYSTEM ===
def add_strike(user_id):
    """Adiciona um strike ao usuário e verifica se deve bloquear"""
    current_time = time.time()
    
    # Limpa strikes expirados (strikes expiram após 1 hora)
    user_pending_invites[user_id] = [ts for ts in user_pending_invites[user_id] 
                                   if current_time - ts < 3600]
    
    # Adiciona o novo convite
    user_pending_invites[user_id].append(current_time)
    
    # Se tem 3 ou mais convites não respondidos em 1 hora, adiciona strike
    if len(user_pending_invites[user_id]) >= STRIKE_LIMIT:
        user_strikes[user_id] += 1
        user_strike_expiry[user_id] = current_time + 3600  # Strike expira em 1 hora
        
        # Limpa os convites pendentes
        user_pending_invites[user_id].clear()
        
        # Se atingiu o limite de strikes, bloqueia
        if user_strikes[user_id] >= STRIKE_LIMIT:
            strike_blocked_users[user_id] = current_time + STRIKE_BLOCK_DURATION
            return True  # Usuário foi bloqueado
    
    return False  # Usuário não foi bloqueado

def is_strike_blocked(user_id):
    """Verifica se o usuário está bloqueado por strikes"""
    if user_id in strike_blocked_users:
        if time.time() < strike_blocked_users[user_id]:
            return True
        else:
            # Bloqueio expirado, remove do dicionário
            del strike_blocked_users[user_id]
            user_strikes[user_id] = 0  # Reseta strikes após bloqueio
    return False

def get_strike_info(user_id):
    """Retorna informações sobre strikes do usuário"""
    current_time = time.time()
    
    # Limpa strikes expirados
    if user_id in user_strike_expiry and current_time > user_strike_expiry[user_id]:
        user_strikes[user_id] = 0
        user_pending_invites[user_id].clear()
    
    pending_count = len(user_pending_invites[user_id])
    strike_count = user_strikes[user_id]
    
    return pending_count, strike_count

def get_gender_display(gender):
    return "👨🏻 Anônimo" if gender == "homem" else "👩🏻 Anônima"

def get_preference_display(pref):
    if pref == "homem":
        return "👨🏻 Anônimos"
    elif pref == "mulher":
        return "👩🏻 Anônimas"
    else:
        return "👨🏻👩🏻 Ambos"

async def criar_call_secreta(guild, u1, u2):
    nome_call = f"💕 {u1.display_name} & {u2.display_name}"
    
    categoria = discord.utils.get(guild.categories, name="iTinder")
    if not categoria:
        try:
            categoria = await guild.create_category("iTinder")
        except Exception:
            categoria = None
    
    owner = guild.owner
    
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
        u1: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        u2: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        owner: discord.PermissionOverwrite(view_channel=True, connect=False, speak=False),
    }
    
    try:
        if categoria:
            call_channel = await categoria.create_voice_channel(nome_call, overwrites=overwrites)
        else:
            call_channel = await guild.create_voice_channel(nome_call, overwrites=overwrites)
        return call_channel
    except Exception:
        return None

async def encerrar_canal_e_cleanup(canal):
    try:
        cid = canal.id
        data = active_channels.get(cid)
        if not data:
            return
            
        u1_id = data.get("u1")
        u2_id = data.get("u2")
        
        if u1_id and u2_id:
            mark_encounter(u1_id, u2_id)
            # === ADICIONA COOLDOWN AO PAR ===
            set_cooldown(u1_id, u2_id)
        
        call_channel = data.get("call_channel")
        if call_channel:
            try:
                await call_channel.delete()
            except:
                pass
        
        try:
            del active_channels[cid]
        except Exception:
            pass
    except Exception:
        pass
    try:
        await canal.delete()
    except Exception:
        pass

async def tentar_formar_dupla(guild):
    while True:
        await asyncio.sleep(2)
        
        current_time = time.time()
        for user_id in list(user_queues.keys()):
            if user_id in user_queue_time and current_time - user_queue_time[user_id] > 86400:
                user_queues[user_id] = False
                fila_carentes[:] = [entry for entry in fila_carentes if entry["user_id"] != user_id]
                del user_queue_time[user_id]
        
        usuarios_na_fila = [entry for entry in fila_carentes if user_queues.get(entry["user_id"], False)]
        
        if len(usuarios_na_fila) < 2:
            continue

        for i in range(len(usuarios_na_fila)):
            for j in range(i + 1, len(usuarios_na_fila)):
                entry1 = usuarios_na_fila[i]
                entry2 = usuarios_na_fila[j]
                
                u1_id = entry1["user_id"]
                u2_id = entry2["user_id"]
                
                if not user_queues.get(u1_id, False) or not user_queues.get(u2_id, False):
                    continue
                
                # === VERIFICA SE USUÁRIO ESTÁ BLOQUEADO POR STRIKES ===
                if is_strike_blocked(u1_id) or is_strike_blocked(u2_id):
                    continue
                
                if is_permanently_blocked(u1_id, u2_id):
                    continue
                
                # === VERIFICA SE O PAR ESTÁ EM COOLDOWN ===
                if is_on_cooldown(u1_id, u2_id):
                    continue
                
                if any(channel_data.get("u1") == u1_id and channel_data.get("u2") == u2_id or 
                       channel_data.get("u1") == u2_id and channel_data.get("u2") == u1_id 
                       for channel_data in active_channels.values()):
                    continue
                
                pref1 = entry1["preference"]
                pref2 = entry2["preference"]
                gender1 = entry1["gender"]
                gender2 = entry2["gender"]
                
                compatible = False
                if pref1 == gender2 or pref1 == "ambos":
                    if pref2 == gender1 or pref2 == "ambos":
                        compatible = True
                
                if not compatible:
                    continue

                u1 = guild.get_member(u1_id)
                u2 = guild.get_member(u2_id)
                if not u1 or not u2:
                    continue
                
                # === REGISTRA CONVITE PENDENTE PARA AMBOS OS USUÁRIOS ===
                add_strike(u1_id)
                add_strike(u2_id)
                
                nome_canal = f"💕-{u1.display_name[:10]}-{u2.display_name[:10]}"
        
                categoria = discord.utils.get(guild.categories, name="iTinder")
                if not categoria:
                    try:
                        categoria = await guild.create_category("iTinder")
                    except Exception:
                        categoria = None
                
                owner = guild.owner
                
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                    u1: discord.PermissionOverwrite(view_channel=True, send_messages=False),
                    u2: discord.PermissionOverwrite(view_channel=True, send_messages=False),
                    owner: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
                }
                
                try:
                    if categoria:
                        canal = await categoria.create_text_channel(nome_canal, overwrites=overwrites)
                    else:
                        canal = await guild.create_text_channel(nome_canal, overwrites=overwrites)
                except Exception:
                    continue
                
                active_channels[canal.id] = {
                    "u1": u1_id,
                    "u2": u2_id,
                    "accepted": set(),
                    "message_id": None,
                    "created_at": time.time(),
                    "started": False,
                    "call_channel": None,
                    "warning_sent": False,
                    "extensions": 0
                }
                
                gender1_display = get_gender_display(gender1)
                gender2_display = get_gender_display(gender2)
                
                embed = discord.Embed(
                    title="💌 Par Encontrado!",
                    description=(
                        f"**{u1.display_name}** ({gender1_display}) & **{u2.display_name}** ({gender2_display})\n\n"
                        "🎯 **Encontramos alguém para você!**\n\n"
                        "✅ **Aceite** para conversar por 10min\n"
                        "❌ **Recuse** e nunca mais verá esta pessoa\n\n"
                        "💡 Ambos precisam aceitar para começar!"
                    ),
                    color=0xFF6B9E
                )
                view = ConversationView(canal, u1, u2, message_id=0)
                try:
                    msg = await canal.send(embed=embed, view=view)
                    active_channels[canal.id]["message_id"] = msg.id
                    view.message_id = msg.id
                except Exception:
                    await encerrar_canal_e_cleanup(canal)
                    continue
                
                aviso_text = f"💌 **Novo par encontrado!**\n\nVocê foi levado para {canal.mention}\n💬 **Aceite no canal para começar a conversar!**"
                try:
                    await u1.send(aviso_text)
                except Exception:
                    pass
                try:
                    await u2.send(aviso_text)
                except Exception:
                    pass
                
                asyncio.create_task(_accept_timeout_handler(canal))
                break

async def _accept_timeout_handler(canal, timeout=ACCEPT_TIMEOUT):
    await asyncio.sleep(timeout)
    data = active_channels.get(canal.id)
    if not data:
        return
    
    if not data.get("started", False):
        accepted = data.get("accepted", set())
        if len(accepted) < 2:
            try:
                msg = await canal.fetch_message(data["message_id"])
                embed = discord.Embed(
                    title="⏰ Tempo Esgotado",
                    description="O tempo para aceitar expirou.\n\n🚫 **Nunca mais verão esta pessoa!**",
                    color=0xFF9999
                )
                await msg.edit(embed=embed, view=None)
            except Exception:
                pass
            await asyncio.sleep(2)
            await encerrar_canal_e_cleanup(canal)

async def _auto_close_channel_after(canal, segundos=CHANNEL_DURATION):
    remaining_time = segundos
    
    await asyncio.sleep(remaining_time - 60)
    
    if canal.id not in active_channels:
        return
    
    data = active_channels.get(canal.id)
    if data and not data.get("warning_sent", False):
        try:
            embed = discord.Embed(
                title="⏰ 1 Minuto Restante",
                description="💡 **Querem +5 minutos?** Clique no botão abaixo!\n\n⚠️ **Ambos precisam aceitar para estender o tempo**",
                color=0xFFA500
            )
            view = ExtensionView(canal)
            message = await canal.send(embed=embed, view=view)
            view.message = message
            active_channels[canal.id]["warning_sent"] = True
        except Exception:
            pass
    
    await asyncio.sleep(60)
    
    if canal.id not in active_channels:
        return
        
    data = active_channels.get(canal.id)
    if data:
        if data.get("extensions", 0) > 0:
            data["extensions"] = data["extensions"] - 1
            data["warning_sent"] = False
            asyncio.create_task(_auto_close_channel_after(canal, 5 * 60))
        else:
            try:
                await canal.send("⏰ **Tempo esgotado!** Chat finalizado.")
                await asyncio.sleep(3)
                await encerrar_canal_e_cleanup(canal)
            except Exception:
                pass

class ExtensionView(discord.ui.View):
    def __init__(self, canal):
        super().__init__(timeout=60)
        self.canal = canal
        self.extended_users = set()

    @discord.ui.button(label="✅ +5min", style=discord.ButtonStyle.success)
    async def extend_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = active_channels.get(self.canal.id)
        if not data:
            await interaction.response.send_message("❌ Canal não encontrado.", ephemeral=True)
            return
            
        user_id = interaction.user.id
        u1_id = data.get("u1")
        u2_id = data.get("u2")
        
        if user_id not in [u1_id, u2_id]:
            await interaction.response.send_message("❌ Você não pode interagir aqui.", ephemeral=True)
            return
        
        self.extended_users.add(user_id)
        
        guild = self.canal.guild
        u1 = guild.get_member(u1_id) if u1_id else None
        u2 = guild.get_member(u2_id) if u2_id else None
        
        if len(self.extended_users) == 1:
            accepted_user = u1 if user_id == u1_id else u2
            embed = discord.Embed(
                title="⏰ Pedido de Extensão",
                description=f"✅ **{accepted_user.display_name}** quer +5 minutos\n⏳ Aguardando o outro usuário...",
                color=0xFFA500
            )
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            embed = discord.Embed(
                title="🎉 Tempo Extendido!",
                description="✅ **Ambos aceitaram! +5 minutos adicionados!**\n💬 Continuem a conversa!",
                color=0x66FF99
            )
            await interaction.response.edit_message(embed=embed, view=None)
            
            data["extensions"] = data.get("extensions", 0) + 1
            data["warning_sent"] = False
            
            asyncio.create_task(_auto_close_channel_after(self.canal, 5 * 60))

    async def on_timeout(self):
        if len(self.extended_users) == 1:
            try:
                embed = discord.Embed(
                    title="⏰ Extensão Não Aceita",
                    description="❌ A outra pessoa não respondeu ao pedido de extensão.\nO tempo original continuará.",
                    color=0xFF9999
                )
                await self.message.edit(embed=embed, view=None)
            except:
                pass

class GenderSetupView(discord.ui.View):
    def __init__(self, setup_message):
        super().__init__(timeout=None)
        self.setup_message = setup_message

    @discord.ui.button(label="👨🏻 Homem", style=discord.ButtonStyle.primary, custom_id="gender_homem")
    async def set_homem(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_genders[interaction.user.id] = "homem"
        
        embed = discord.Embed(
            title="⚙️ Configurar Perfil",
            description="✅ **Você é:** 👨🏻 Homem\n\nAgora escolha quem você quer encontrar:",
            color=0x66FF99
        )
        await self.setup_message.edit(embed=embed, view=PreferenceSetupView(self.setup_message))
        await interaction.response.defer()

    @discord.ui.button(label="👩🏻 Mulher", style=discord.ButtonStyle.primary, custom_id="gender_mulher")
    async def set_mulher(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_genders[interaction.user.id] = "mulher"
        
        embed = discord.Embed(
            title="⚙️ Configurar Perfil",
            description="✅ **Você é:** 👩🏻 Mulher\n\nAgora escolha quem você quer encontrar:",
            color=0x66FF99
        )
        await self.setup_message.edit(embed=embed, view=PreferenceSetupView(self.setup_message))
        await interaction.response.defer()

class PreferenceSetupView(discord.ui.View):
    def __init__(self, setup_message):
        super().__init__(timeout=None)
        self.setup_message = setup_message

    @discord.ui.button(label="👨🏻 Homens", style=discord.ButtonStyle.primary, custom_id="pref_homem")
    async def pref_homem(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_preferences[interaction.user.id] = "homem"
        await self.finalizar_configuracao(interaction)

    @discord.ui.button(label="👩🏻 Mulheres", style=discord.ButtonStyle.primary, custom_id="pref_mulher")
    async def pref_mulher(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_preferences[interaction.user.id] = "mulher"
        await self.finalizar_configuracao(interaction)

    @discord.ui.button(label="👨🏻👩🏻 Ambos", style=discord.ButtonStyle.primary, custom_id="pref_ambos")
    async def pref_ambos(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_preferences[interaction.user.id] = "ambos"
        await self.finalizar_configuracao(interaction)

    async def finalizar_configuracao(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        gender = user_genders.get(user_id, "homem")
        preference = user_preferences.get(user_id, "ambos")
        
        gender_display = get_gender_display(gender)
        preference_display = get_preference_display(preference)
        
        await self.setup_message.delete()
        
        embed_explicacao = discord.Embed(
            title="✅ Configuração Concluída",
            description=f"**Você:** {gender_display}\n**Procurando:** {preference_display}\n\n💌 **Pronto!** Agora entre na fila para conversar.",
            color=0x66FF99
        )
        
        await interaction.response.send_message(embed=embed_explicacao, ephemeral=True)
        await asyncio.sleep(5)
        await interaction.delete_original_response()

class LeaveQueueView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="🚪 Sair da Fila", style=discord.ButtonStyle.danger, custom_id="leavefila_button")
    async def sair(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Isso é só para você.", ephemeral=True)
            return
        
        user_queues[interaction.user.id] = False
        fila_carentes[:] = [entry for entry in fila_carentes if entry["user_id"] != interaction.user.id]
        
        user_id = interaction.user.id
        if user_id in user_messages:
            embed = discord.Embed(
                title="🚪 Saiu da Fila",
                description="💤 **Você saiu da fila**\n\n💡 Volte quando quiser conversar!",
                color=0xFF9999
            )
            await user_messages[user_id].edit(embed=embed, view=IndividualView())
            await interaction.response.defer()
        else:
            await interaction.response.send_message("✅ Você saiu da fila.", ephemeral=True)

class IndividualView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💌 Entrar na Fila", style=discord.ButtonStyle.success, custom_id="individual_entrar")
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        
        # === VERIFICA SE USUÁRIO ESTÁ BLOQUEADO POR STRIKES ===
        if is_strike_blocked(user.id):
            remaining_time = strike_blocked_users[user.id] - time.time()
            minutes = int(remaining_time // 60)
            seconds = int(remaining_time % 60)
            
            embed = discord.Embed(
                title="🚫 Bloqueado Temporariamente",
                description=(
                    f"⏰ **Você está bloqueado por {minutes} minutos e {seconds} segundos**\n\n"
                    "💡 **Motivo:** Você recebeu muitos convites e não aceitou nenhum\n"
                    "✅ **Solução:** Aguarde o bloqueio expirar automaticamente"
                ),
                color=0xFF3333
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        if user.id not in user_genders or user.id not in user_preferences:
            embed_explicacao = discord.Embed(
                title="💌 iTinder",
                description="❌ **Primeiro configure seu gênero**, depois entre na fila para conversar com alguém.\n\nNinguém além de você verá a confirmação. 🔒",
                color=0xFF6B9E
            )
            
            if user.id in user_messages:
                await user_messages[user.id].edit(embed=embed_explicacao, view=IndividualView())
                await interaction.response.defer()
            else:
                message = await interaction.response.send_message(embed=embed_explicacao, view=IndividualView(), ephemeral=True)
                if hasattr(message, 'message'):
                    user_messages[user.id] = message.message
                else:
                    user_messages[user.id] = await interaction.original_response()
            return

        if user_queues.get(user.id, False):
            gender_display = get_gender_display(user_genders[user.id])
            preference_display = get_preference_display(user_preferences[user.id])
            
            # === MOSTRA INFORMAÇÕES DE STRIKES ===
            pending_count, strike_count = get_strike_info(user.id)
            strike_info = ""
            if pending_count > 0:
                strike_info += f"📨 **Convites pendentes:** {pending_count}/3\n"
            if strike_count > 0:
                strike_info += f"⚠️ **Strikes:** {strike_count}/3\n"
            
            embed = discord.Embed(
                title="🔍 Procurando Pessoas...",
                description=(
                    f"**Seu perfil:** {gender_display}\n"
                    f"**Procurando:** {preference_display}\n\n"
                    f"{strike_info}\n"
                    f"💫 **Você está na fila!**\n"
                    f"⏰ Saída automática em 24h\n"
                    f"💬 Conversas de 10min\n"
                    f"🎧 Call disponível\n"
                    f"🚫 Nunca mais verá a mesma pessoa"
                ),
                color=0x66FF99
            )
            
            if user.id in user_messages:
                await user_messages[user.id].edit(embed=embed, view=LeaveQueueView(user.id))
                await interaction.response.defer()
            else:
                message = await interaction.response.send_message(embed=embed, view=LeaveQueueView(user.id), ephemeral=True)
                if hasattr(message, 'message'):
                    user_messages[user.id] = message.message
                else:
                    user_messages[user.id] = await interaction.original_response()
            return

        user_queues[user.id] = True
        user_queue_time[user.id] = time.time()
        
        fila_entry = {
            "user_id": user.id,
            "gender": user_genders[user.id],
            "preference": user_preferences[user.id]
        }
        
        fila_carentes[:] = [entry for entry in fila_carentes if entry["user_id"] != user.id]
        fila_carentes.append(fila_entry)
        
        gender_display = get_gender_display(user_genders[user.id])
        preference_display = get_preference_display(user_preferences[user.id])
        
        # === MOSTRA INFORMAÇÕES DE STRIKES ===
        pending_count, strike_count = get_strike_info(user.id)
        strike_info = ""
        if pending_count > 0:
            strike_info += f"📨 **Convites pendentes:** {pending_count}/3\n"
        if strike_count > 0:
            strike_info += f"⚠️ **Strikes:** {strike_count}/3\n"
        
        embed = discord.Embed(
            title="🔍 Procurando Pessoas...",
            description=(
                f"**Seu perfil:** {gender_display}\n"
                f"**Procurando:** {preference_display}\n\n"
                f"{strike_info}\n"
                f"💫 **Você está na fila!**\n"
                f"⏰ Saída automática em 24h\n"
                f"💬 Conversas de 10min\n"
                f"🎧 Call disponível\n"
                f"🚫 Nunca mais verá a mesma pessoa"
            ),
            color=0x66FF99
        )
        
        if user.id in user_messages:
            await user_messages[user.id].edit(embed=embed, view=LeaveQueueView(user.id))
            await interaction.response.defer()
        else:
            message = await interaction.response.send_message(embed=embed, view=LeaveQueueView(user.id), ephemeral=True)
            if hasattr(message, 'message'):
                user_messages[user.id] = message.message
            else:
                user_messages[user.id] = await interaction.original_response()

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="👨🏻👩🏻 Configurar Perfil", style=discord.ButtonStyle.primary, custom_id="config_gender")
    async def config_gender(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="⚙️ Configurar Perfil",
            description="👥 **Escolha como você se identifica:**",
            color=0x66FF99
        )
        
        setup_message = await interaction.response.send_message(
            embed=embed, 
            view=GenderSetupView(None),
            ephemeral=True
        )
        
        if hasattr(setup_message, 'message'):
            message = setup_message.message
        else:
            message = await interaction.original_response()
        
        embed = discord.Embed(
            title="⚙️ Configurar Perfil",
            description="👥 **Escolha como você se identifica:**",
            color=0x66FF99
        )
        await message.edit(embed=embed, view=GenderSetupView(message))

    @discord.ui.button(label="💌 Entrar na Fila", style=discord.ButtonStyle.success, custom_id="ticket_entrar")
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        
        # === VERIFICA SE USUÁRIO ESTÁ BLOQUEADO POR STRIKES ===
        if is_strike_blocked(user.id):
            remaining_time = strike_blocked_users[user.id] - time.time()
            minutes = int(remaining_time // 60)
            seconds = int(remaining_time % 60)
            
            embed = discord.Embed(
                title="🚫 Bloqueado Temporariamente",
                description=(
                    f"⏰ **Você está bloqueado por {minutes} minutos e {seconds} segundos**\n\n"
                    "💡 **Motivo:** Você recebeu muitos convites e não aceitou nenhum\n"
                    "✅ **Solução:** Aguarde o bloqueio expirar automaticamente"
                ),
                color=0xFF3333
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        if user.id not in user_genders or user.id not in user_preferences:
            embed_explicacao = discord.Embed(
                title="💌 iTinder",
                description="❌ **Primeiro configure seu gênero**, depois entre na fila para conversar com alguém.\n\nNinguém além de você verá a confirmação. 🔒",
                color=0xFF6B9E
            )
            
            message = await interaction.response.send_message(embed=embed_explicacao, view=IndividualView(), ephemeral=True)
            if hasattr(message, 'message'):
                user_messages[user.id] = message.message
            else:
                user_messages[user.id] = await interaction.original_response()
            return

        gender_display = get_gender_display(user_genders[user.id])
        preference_display = get_preference_display(user_preferences[user.id])
        
        embed_inicial = discord.Embed(
            title="💌 iTinder - Pronto para Conversar",
            description=f"**Seu perfil:** {gender_display}\n**Procurando:** {preference_display}\n\n💌 **Clique em Entrar na Fila para começar!**\n🚫 Nunca mais verá a mesma pessoa",
            color=0x66FF99
        )
        
        message = await interaction.response.send_message(embed=embed_inicial, view=IndividualView(), ephemeral=True)
        if hasattr(message, 'message'):
            user_messages[user.id] = message.message
        else:
            user_messages[user.id] = await interaction.original_response()

class ConversationView(discord.ui.View):
    def __init__(self, canal, u1, u2, message_id):
        super().__init__(timeout=None)
        self.canal = canal
        self.u1 = u1
        self.u2 = u2
        self.message_id = message_id

    @discord.ui.button(label="✅ Aceitar Chat", style=discord.ButtonStyle.success, custom_id="conv_aceitar")
    async def aceitar(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        cid = self.canal.id
        if uid not in (self.u1.id, self.u2.id):
            await interaction.response.send_message("❌ Você não pode interagir aqui.", ephemeral=True)
            return

        data = active_channels.get(cid)
        if not data:
            await interaction.response.send_message("❌ Estado inválido.", ephemeral=True)
            return
        
        accepted = data.setdefault("accepted", set())
        accepted.add(uid)
        
        # === REMOVE STRIKES AO ACEITAR ===
        if uid in user_pending_invites:
            user_pending_invites[uid].clear()
        
        try:
            msg = await self.canal.fetch_message(self.message_id)
            embed = discord.Embed(
                title="💌 Confirmação",
                description=(
                    f"{self.u1.display_name} {'✅' if self.u1.id in accepted else '⏳'}\n"
                    f"{self.u2.display_name} {'✅' if self.u2.id in accepted else '⏳'}\n\n"
                    f"⏰ **Aguardando ambos aceitarem...**\n"
                    "💡 Ambos precisam aceitar para começar!"
                ),
                color=0xFF6B9E
            )
            await msg.edit(embed=embed, view=self)
        except Exception:
            pass
        
        if self.u1.id in accepted and self.u2.id in accepted:
            try:
                await self.canal.set_permissions(self.u1, send_messages=True, view_channel=True)
                await self.canal.set_permissions(self.u2, send_messages=True, view_channel=True)
            except Exception:
                pass
            
            enc_view = EncerrarView(self.canal, self.u1, self.u2)
            try:
                msg = await self.canal.fetch_message(self.message_id)
                embed = discord.Embed(
                    title="💫 Conversa Iniciada!",
                    description="⏰ **10 minutos** de conversa\n🎧 **Call disponível**\n🚫 **Nunca mais** se verão após este chat",
                    color=0x66FF99
                )
                await msg.edit(embed=embed, view=enc_view)
            except Exception:
                pass
            
            active_channels[cid]["started"] = True
            asyncio.create_task(_auto_close_channel_after(canal=self.canal))
        
        await interaction.response.send_message("✅ Aceito!", ephemeral=True)

    @discord.ui.button(label="❌ Sair", style=discord.ButtonStyle.secondary, custom_id="conv_sair")
    async def sair(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        cid = self.canal.id
        if uid not in (self.u1.id, self.u2.id):
            await interaction.response.send_message("❌ Você não pode interagir aqui.", ephemeral=True)
            return

        try:
            msg = await self.canal.fetch_message(self.message_id)
            embed = discord.Embed(
                title="🚪 Usuário Saiu",
                description=f"**{interaction.user.display_name} saiu da conversa.**\n\n💫 **Poderão se encontrar novamente!**",
                color=0xFFA500
            )
            await msg.edit(embed=embed, view=None)
        except Exception:
            pass
        
        await asyncio.sleep(3)
        await encerrar_canal_e_cleanup(self.canal)
        await interaction.response.send_message("🚪 **Você saiu!** Poderá encontrar esta pessoa novamente.", ephemeral=True)

    @discord.ui.button(label="🚫 Bloquear", style=discord.ButtonStyle.danger, custom_id="conv_bloquear")
    async def bloquear(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        cid = self.canal.id
        if uid not in (self.u1.id, self.u2.id):
            await interaction.response.send_message("❌ Você não pode interagir aqui.", ephemeral=True)
            return

        # Define bloqueio permanente
        other_user_id = self.u2.id if uid == self.u1.id else self.u1.id
        set_permanent_block(uid, other_user_id)

        try:
            msg = await self.canal.fetch_message(self.message_id)
            embed = discord.Embed(
                title="🚫 Usuário Bloqueado",
                description=f"**{interaction.user.display_name} bloqueou permanentemente.**\n\n🚫 **Nunca mais se encontrarão!**",
                color=0xFF3333
            )
            await msg.edit(embed=embed, view=None)
        except Exception:
            pass
        
        await asyncio.sleep(3)
        await encerrar_canal_e_cleanup(self.canal)
        await interaction.response.send_message("🚫 **Bloqueado!** Nunca mais verá esta pessoa.", ephemeral=True)

class EncerrarView(discord.ui.View):
    def __init__(self, canal, u1, u2):
        super().__init__(timeout=None)
        self.canal = canal
        self.u1 = u1
        self.u2 = u2

    @discord.ui.button(label="🎧 Criar Call", style=discord.ButtonStyle.secondary, custom_id="criar_call")
    async def criar_call(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.u1.id, self.u2.id):
            await interaction.response.send_message("❌ Você não pode criar calls aqui.", ephemeral=True)
            return

        data = active_channels.get(self.canal.id)
        if not data:
            await interaction.response.send_message("❌ Estado inválido.", ephemeral=True)
            return

        if data.get("call_channel"):
            await interaction.response.send_message("❌ Já existe uma call ativa.", ephemeral=True)
            return

        call_channel = await criar_call_secreta(interaction.guild, self.u1, self.u2)
        if call_channel:
            data["call_channel"] = call_channel
            await interaction.response.send_message(f"🎧 **Call criada!** {call_channel.mention}")
        else:
            await interaction.response.send_message("❌ Erro ao criar call.", ephemeral=True)

    @discord.ui.button(label="🚪 Sair", style=discord.ButtonStyle.secondary, custom_id="sair_chat")
    async def sair(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.u1.id, self.u2.id):
            await interaction.response.send_message("❌ Você não pode encerrar.", ephemeral=True)
            return

        await interaction.response.send_message("🚪 **Você saiu!** Poderá encontrar esta pessoa novamente.", ephemeral=True)
        await encerrar_canal_e_cleanup(self.canal)

    @discord.ui.button(label="🚫 Bloquear", style=discord.ButtonStyle.danger, custom_id="bloquear_chat")
    async def bloquear(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.u1.id, self.u2.id):
            await interaction.response.send_message("❌ Você não pode bloquear.", ephemeral=True)
            return

        # Define bloqueio permanente
        other_user_id = self.u2.id if interaction.user.id == self.u1.id else self.u1.id
        set_permanent_block(interaction.user.id, other_user_id)

        await interaction.response.send_message("🚫 **Bloqueado!** Nunca mais verá esta pessoa.", ephemeral=True)
        await encerrar_canal_e_cleanup(self.canal)

@bot.tree.command(name="setupcarente", description="Configura o sistema iTinder (apenas admin)")
async def setupcarente(interaction: discord.Interaction):
    if interaction.guild.id != MINHA_GUILD_ID:
        await interaction.response.send_message("❌ Este bot não está disponível neste servidor.", ephemeral=True)
        return
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Apenas administradores podem usar este comando.", ephemeral=True)
        return
    
    global setup_channel_id, canal_bloqueado, main_message_id
    
    try:
        await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False)
        canal_bloqueado = True
        setup_channel_id = interaction.channel.id
        
        categoria = discord.utils.get(interaction.guild.categories, name="iTinder")
        if not categoria:
            try:
                await interaction.guild.create_category("iTinder")
            except Exception:
                pass
                
    except Exception:
        await interaction.response.send_message("❌ Erro ao bloquear o canal", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="💌 iTinder - Chat Anônimo",
        description="❌ **Primeiro configure seu gênero**, depois entre na fila para conversar com alguém.\n\nNinguém além de você verá a confirmação. 🔒",
        color=0xFF6B9E
    )
    
    view = TicketView()
    try:
        message = await interaction.channel.send(embed=embed, view=view)
        main_message_id = message.id
        await interaction.response.send_message("✅ Sistema iTinder configurado!", ephemeral=True)
    except Exception:
        await interaction.response.send_message("❌ Erro ao enviar mensagem de setup", ephemeral=True)

@bot.tree.command(name="reset_encounters", description="[ADMIN] Resetar todos os encontros")
async def reset_encounters(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Apenas administradores.", ephemeral=True)
        return
    
    ENCOUNTER_HISTORY.clear()
    PERMANENT_BLOCKS.clear()
    cooldown_pairs.clear()
    user_strikes.clear()
    user_strike_expiry.clear()
    strike_blocked_users.clear()
    user_pending_invites.clear()
    
    await interaction.response.send_message("✅ **Todos os sistemas resetados!**", ephemeral=True)

@bot.tree.command(name="strike_info", description="Ver suas informações de strikes")
async def strike_info(interaction: discord.Interaction):
    pending_count, strike_count = get_strike_info(interaction.user.id)
    
    embed = discord.Embed(
        title="📊 Suas Informações de Strikes",
        color=0xFF6B9E
    )
    
    embed.add_field(
        name="📨 Convites Pendentes",
        value=f"{pending_count}/3 (expira em 1 hora)",
        inline=False
    )
    
    embed.add_field(
        name="⚠️ Strikes Atuais", 
        value=f"{strike_count}/3 (expira em 1 hora)",
        inline=False
    )
    
    if is_strike_blocked(interaction.user.id):
        remaining_time = strike_blocked_users[interaction.user.id] - time.time()
        minutes = int(remaining_time // 60)
        seconds = int(remaining_time % 60)
        embed.add_field(
            name="🚫 Status de Bloqueio",
            value=f"**BLOQUEADO** por {minutes}min {seconds}s",
            inline=False
        )
    else:
        embed.add_field(
            name="✅ Status",
            value="**NÃO BLOQUEADO**",
            inline=False
        )
    
    embed.add_field(
        name="💡 Como Funciona",
        value="3 convites não aceitos = 1 strike\n3 strikes = bloqueio de 10min",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_message(message):
    if message.guild and message.guild.id == MINHA_GUILD_ID:
        if message.channel.id == setup_channel_id:
            if message.author != bot.user and not message.author.guild_permissions.administrator:
                try:
                    await message.delete()
                except:
                    pass
    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f"✅ iTinder online! {bot.user.name}")
    
    bot.add_view(TicketView())
    bot.add_view(IndividualView())
    bot.add_view(EncerrarView(None, None, None))
    
    guild = discord.Object(id=MINHA_GUILD_ID)
    try:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print("✅ Comandos sincronizados!")
    except Exception as e:
        print(f"⚠️ Erro: {e}")
    
    guild_instance = bot.get_guild(MINHA_GUILD_ID)
    if guild_instance:
        asyncio.create_task(tentar_formar_dupla(guild_instance))

if __name__ == "__main__":
    token = os.getenv("TOKEN")
    if token:
        bot.run(token)
