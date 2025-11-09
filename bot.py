import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import time
import os

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
PERMANENT_BLOCKS = {}  # Bloqueio permanente
ACCEPT_TIMEOUT = 300  # 5 minutos para aceitar/recusar
CHANNEL_DURATION = 10 * 60  # 10 minutos de conversa

# Histórico de pares que já se encontraram (BLOQUEIO PERMANENTE)
ENCOUNTER_HISTORY = {}

setup_channel_id = None
canal_bloqueado = False
main_message_id = None
user_messages = {}
user_queues = {}

def get_gender_display(gender):
    return "👨🏻 Anônimo" if gender == "homem" else "👩🏻 Anônima"

def get_preference_display(pref):
    if pref == "homem":
        return "👨🏻 Anônimos"
    elif pref == "mulher":
        return "👩🏻 Anônimas"
    else:
        return "👨🏻👩🏻 Ambos"

def pair_key(u1_id, u2_id):
    return frozenset({u1_id, u2_id})

def is_permanently_blocked(u1_id, u2_id):
    """Verifica se existe bloqueio permanente entre dois usuários"""
    key = pair_key(u1_id, u2_id)
    return key in PERMANENT_BLOCKS

def set_permanent_block(u1_id, u2_id):
    """Define bloqueio permanente entre dois usuários"""
    key = pair_key(u1_id, u2_id)
    PERMANENT_BLOCKS[key] = True
    print(f"🔒 BLOQUEIO PERMANENTE definido entre {u1_id} e {u2_id}")

def have_encountered(u1_id, u2_id):
    """Verifica se dois usuários já se encontraram anteriormente - BLOQUEIO PERMANENTE"""
    key = pair_key(u1_id, u2_id)
    return key in ENCOUNTER_HISTORY

def mark_encounter(u1_id, u2_id):
    """Marca que dois usuários se encontraram - BLOQUEIO PERMANENTE ATIVADO"""
    key = pair_key(u1_id, u2_id)
    ENCOUNTER_HISTORY[key] = True
    print(f"🚫 BLOQUEIO PERMANENTE: {u1_id} e {u2_id} NUNCA MAIS se encontrarão")

def gerar_nome_canal(guild, user1_id, user2_id):
    """Gera nome do canal com os nomes dos usuários"""
    user1 = guild.get_member(user1_id)
    user2 = guild.get_member(user2_id)
    
    if user1 and user2:
        nome_u1 = user1.display_name[:10]
        nome_u2 = user2.display_name[:10]
        base = f"💕-{nome_u1}-{nome_u2}"[:20]
    else:
        base = f"chat-{user1_id}-{user2_id}"[-20:]
    
    existing = {c.name for c in guild.text_channels}
    if base not in existing:
        return base
    i = 1
    while True:
        candidate = f"{base}-{i}"
        if candidate not in existing:
            return candidate
        i += 1

def gerar_nome_call(u1, u2):
    """Gera nome da call com os nomes dos usuários"""
    nome_u1 = u1.display_name[:10]
    nome_u2 = u2.display_name[:10]
    return f"💕 {nome_u1} & {nome_u2}"

async def criar_call_secreta(guild, u1, u2):
    """Cria uma call de voz temporária para o par"""
    nome_call = gerar_nome_call(u1, u2)
    
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
            call_channel = await categoria.create_voice_channel(nome_call, overwrites=overwrites, reason="Call iTinder temporária")
        else:
            call_channel = await guild.create_voice_channel(nome_call, overwrites=overwrites, reason="Call iTinder temporária")
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
        
        # MARCA ENCONTRO - SEMPRE QUE UM CANAL É CRIADO, MESMO QUE NÃO ACEITEM
        if u1_id and u2_id:
            mark_encounter(u1_id, u2_id)
            print(f"🚫 CANAL ENCERRADO: {u1_id} e {u2_id} NUNCA MAIS se encontrarão")
        
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
    """Tenta formar pares continuamente para usuários na fila"""
    while True:
        await asyncio.sleep(2)
        
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
                
                # VERIFICAR BLOQUEIO PERMANENTE - AGORA É PARA SEMPRE
                if is_permanently_blocked(u1_id, u2_id):
                    print(f"🚫 BLOQUEIO PERMANENTE: {u1_id} e {u2_id} não podem se conectar nunca mais")
                    continue
                
                # VERIFICAR SE JÁ SE ENCONTRARAM - BLOQUEIO PERMANENTE
                if have_encountered(u1_id, u2_id):
                    print(f"🚫 ENCONTRO ANTERIOR: {u1_id} e {u2_id} já se encontraram e NUNCA MAIS se encontrarão")
                    continue
                
                # Verificar se já estão em um canal ativo juntos
                if any(channel_data.get("u1") == u1_id and channel_data.get("u2") == u2_id or 
                       channel_data.get("u1") == u2_id and channel_data.get("u2") == u1_id 
                       for channel_data in active_channels.values()):
                    continue
                
                # Verificar compatibilidade
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
                
                print(f"🎯 Tentando formar par: {u1.display_name} e {u2.display_name}")
                
                nome_canal = gerar_nome_canal(guild, u1_id, u2_id)
        
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
                        canal = await categoria.create_text_channel(nome_canal, overwrites=overwrites, reason="Canal iTinder temporário")
                    else:
                        canal = await guild.create_text_channel(nome_canal, overwrites=overwrites, reason="Canal iTinder temporário")
                except Exception:
                    continue
                
                # MARCA ENCONTRO IMEDIATAMENTE QUANDO O CANAL É CRIADO - BLOQUEIO PERMANENTE
                mark_encounter(u1_id, u2_id)
                print(f"🚫 NOVO PAR FORMADO: {u1_id} e {u2_id} - NUNCA MAIS se encontrarão")
                
                active_channels[canal.id] = {
                    "u1": u1_id,
                    "u2": u2_id,
                    "accepted": set(),
                    "message_id": None,
                    "created_at": time.time(),
                    "started": False,
                    "call_channel": None,
                    "warning_sent": False,
                    "extensions": 0,
                    "extension_requested": False,
                    "extension_accepted": set()
                }
                
                gender1_display = get_gender_display(gender1)
                gender2_display = get_gender_display(gender2)
                
                embed = discord.Embed(
                    title="💌 iTinder - Par Encontrado!",
                    description=(
                        f"**{u1.display_name}** ({gender1_display}) & **{u2.display_name}** ({gender2_display})\n\n"
                        "🚨 **ATENÇÃO - BLOQUEIO PERMANENTE ATIVADO:**\n"
                        "• ❌ **DEPOIS DESTE ENCONTRO: NUNCA MAIS** se encontrarão\n"
                        "• ⏰ **10 minutos** de conversa se ambos aceitarem\n"
                        "• 🎧 **Call secreta** disponível durante o chat\n"
                        "• 🔒 **BLOQUEIO PERMANENTE** após qualquer interação\n"
                        f"• ⏳ **Chat será fechado em {ACCEPT_TIMEOUT//60} minutos se ninguém aceitar**\n"
                        "• 💬 Chat totalmente anônimo e privado\n\n"
                        "⚠️ **Esta é sua ÚNICA chance de conversar com esta pessoa!**"
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
                
                aviso_text = (
                    "💌 **NOVO PAR ENCONTRADO NO iTINDER!**\n\n"
                    f"Você foi levado para {canal.mention}\n"
                    "🚨 **BLOQUEIO PERMANENTE ATIVADO:**\n"
                    "• ❌ **DEPOIS DESSE ENCONTRO: NUNCA MAIS** encontrará esta pessoa\n"
                    "• ⏰ 10 minutos de conversa se aceitarem\n"
                    "• 🎧 Call secreta disponível\n"
                    f"• ⏳ **Aceite em {ACCEPT_TIMEOUT//60} minutos ou o chat será fechado**\n"
                    "• 🔒 **BLOQUEIO PERMANENTE** após qualquer interação\n\n"
                    "🔍 **Você continua na fila procurando mais pessoas!**"
                )
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
                    title="⏰ Tempo Esgotado - BLOQUEIO PERMANENTE",
                    description=(
                        f"O tempo para aceitar expirou ({ACCEPT_TIMEOUT//60} minutos).\n\n"
                        "⚠️ **Nenhum dos dois aceitou a conversa a tempo.**\n"
                        "🚫 **BLOQUEIO PERMANENTE:** Vocês NUNCA MAIS se encontrarão!\n"
                        "💫 Volte ao canal principal para tentar com outras pessoas!"
                    ),
                    color=0xFF9999
                )
                await msg.edit(embed=embed, view=None)
            except Exception:
                pass
            await asyncio.sleep(2)
            await encerrar_canal_e_cleanup(canal)

async def _auto_close_channel_after(canal, segundos=CHANNEL_DURATION):
    """Controla o tempo da conversa e oferece extensões"""
    remaining_time = segundos
    
    # Enviar aviso quando faltar 1 minuto
    await asyncio.sleep(remaining_time - 60)
    
    if canal.id not in active_channels:
        return
    
    data = active_channels.get(canal.id)
    if data and not data.get("warning_sent", False):
        try:
            # Oferecer extensão de tempo
            embed = discord.Embed(
                title="⏰ Aviso: Chat Terminando",
                description=(
                    "**⚠️ O chat termina em 1 minuto!**\n\n"
                    "⏳ **Tempo restante:** 1 minuto\n"
                    "💡 **Desejam adicionar mais 5 minutos de conversa?**\n"
                    "🔒 Ambos precisam aceitar para estender o tempo!\n"
                    "🚫 **Lembrete:** Após este chat, NUNCA MAIS se encontrarão!"
                ),
                color=0xFFA500
            )
            view = ExtensionView(canal)
            await canal.send(embed=embed, view=view)
            active_channels[canal.id]["warning_sent"] = True
            active_channels[canal.id]["extension_requested"] = True
        except Exception:
            pass
    
    # Aguardar resposta da extensão ou finalizar
    await asyncio.sleep(60)
    
    if canal.id not in active_channels:
        return
        
    data = active_channels.get(canal.id)
    if data and data.get("extension_requested", False):
        # Verificar se ambos aceitaram a extensão
        extension_accepted = data.get("extension_accepted", set())
        if len(extension_accepted) >= 2:
            # Ambos aceitaram - adicionar 5 minutos
            active_channels[canal.id]["extensions"] += 1
            active_channels[canal.id]["extension_requested"] = False
            active_channels[canal.id]["extension_accepted"] = set()
            active_channels[canal.id]["warning_sent"] = False
            
            try:
                embed = discord.Embed(
                    title="✅ Tempo Estendido!",
                    description=(
                        "**🎉 +5 minutos adicionados!**\n\n"
                        f"⏰ **Tempo total:** {10 + (active_channels[canal.id]['extensions'] * 5)} minutos\n"
                        "💬 Continuem aproveitando a conversa!\n"
                        "⏳ Novo aviso em 4 minutos...\n"
                        "🚫 **Lembrete:** Após este chat, NUNCA MAIS se encontrarão!"
                    ),
                    color=0x66FF99
                )
                await canal.send(embed=embed)
            except Exception:
                pass
            
            # Reiniciar o timer com +5 minutos
            asyncio.create_task(_auto_close_channel_after(canal, 5 * 60))
            return
    
    # Finalizar o chat se não houve extensão ou não foi aceita
    try:
        data = active_channels.get(canal.id)
        if data:
            try:
                msg = await canal.fetch_message(data["message_id"])
                embed = discord.Embed(
                    title="⏰ Tempo de Conversa Esgotado - BLOQUEIO PERMANENTE",
                    description=(
                        "Seus **10 minutos** de conversa terminaram!\n\n"
                        "🚫 **BLOQUEIO PERMANENTE ATIVADO:**\n"
                        "⚠️ **Vocês NUNCA MAIS se encontrarão no iTinder!**\n\n"
                        "💫 Esperamos que tenha sido uma boa experiência.\n"
                        "🔍 **Você continua na fila procurando mais pessoas!**"
                    ),
                    color=0x9999FF
                )
                await msg.edit(embed=embed, view=None)
            except Exception:
                pass
            await asyncio.sleep(3)
            await encerrar_canal_e_cleanup(canal)
    except Exception:
        pass

class ExtensionView(discord.ui.View):
    """View para aceitar extensão de tempo"""
    def __init__(self, canal):
        super().__init__(timeout=60)  # 1 minuto para responder
        self.canal = canal

    @discord.ui.button(label="✅ Sim, +5min", style=discord.ButtonStyle.success, custom_id="extend_yes")
    async def extend_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = active_channels.get(self.canal.id)
        if not data:
            await interaction.response.send_message("❌ Canal não encontrado.", ephemeral=True)
            return
            
        user_id = interaction.user.id
        if user_id not in [data.get("u1"), data.get("u2")]:
            await interaction.response.send_message("❌ Você não pode interagir aqui.", ephemeral=True)
            return
            
        data.setdefault("extension_accepted", set()).add(user_id)
        accepted_count = len(data["extension_accepted"])
        
        if accepted_count == 1:
            await interaction.response.send_message(
                "✅ Você aceitou a extensão! Aguardando o outro usuário...", 
                ephemeral=True
            )
        elif accepted_count >= 2:
            await interaction.response.send_message(
                "✅ Extensão aceita por ambos! +5 minutos adicionados.", 
                ephemeral=True
            )
            # A extensão será processada no timer principal

    @discord.ui.button(label="❌ Não, finalizar", style=discord.ButtonStyle.danger, custom_id="extend_no")
    async def extend_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = active_channels.get(self.canal.id)
        if not data:
            await interaction.response.send_message("❌ Canal não encontrado.", ephemeral=True)
            return
            
        user_id = interaction.user.id
        if user_id not in [data.get("u1"), data.get("u2")]:
            await interaction.response.send_message("❌ Você não pode interagir aqui.", ephemeral=True)
            return
            
        # Se um usuário recusar, finalizar imediatamente
        try:
            embed = discord.Embed(
                title="❌ Extensão Recusada - BLOQUEIO PERMANENTE",
                description=(
                    "Um dos usuários preferiu não estender o tempo.\n\n"
                    "🚫 **BLOQUEIO PERMANENTE ATIVADO:**\n"
                    "⚠️ **Vocês NUNCA MAIS se encontrarão!**\n\n"
                    "💫 Obrigado por usar o iTinder!\n"
                    "🔍 **Você continua na fila procurando mais pessoas!**"
                ),
                color=0xFF9999
            )
            await interaction.response.send_message(embed=embed)
            
            await asyncio.sleep(2)
            await encerrar_canal_e_cleanup(self.canal)
        except Exception:
            pass

class GenderSetupView(discord.ui.View):
    def __init__(self, setup_message):
        super().__init__(timeout=None)
        self.setup_message = setup_message

    @discord.ui.button(label="👨🏻 Anônimo", style=discord.ButtonStyle.primary, custom_id="gender_homem")
    async def set_homem(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_genders[interaction.user.id] = "homem"
        
        embed = discord.Embed(
            title="⚙️ Configurar Perfil",
            description="✅ **Você é:** 👨🏻 Anônimo\n\nAgora escolha quem você quer encontrar:",
            color=0x66FF99
        )
        await self.setup_message.edit(embed=embed, view=PreferenceSetupView(self.setup_message))
        await interaction.response.defer()

    @discord.ui.button(label="👩🏻 Anônima", style=discord.ButtonStyle.primary, custom_id="gender_mulher")
    async def set_mulher(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_genders[interaction.user.id] = "mulher"
        
        embed = discord.Embed(
            title="⚙️ Configurar Perfil",
            description="✅ **Você é:** 👩🏻 Anônima\n\nAgora escolha quem você quer encontrar:",
            color=0x66FF99
        )
        await self.setup_message.edit(embed=embed, view=PreferenceSetupView(self.setup_message))
        await interaction.response.defer()

class PreferenceSetupView(discord.ui.View):
    def __init__(self, setup_message):
        super().__init__(timeout=None)
        self.setup_message = setup_message

    @discord.ui.button(label="👨🏻 Anônimos", style=discord.ButtonStyle.primary, custom_id="pref_homem")
    async def pref_homem(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_preferences[interaction.user.id] = "homem"
        await self.finalizar_configuracao(interaction)

    @discord.ui.button(label="👩🏻 Anônimas", style=discord.ButtonStyle.primary, custom_id="pref_mulher")
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
            title="⚙️ Configuração Concluída",
            description=(
                f"✅ **Perfil configurado com sucesso!**\n\n"
                f"**Você:** {gender_display}\n"
                f"**Procurando:** {preference_display}\n\n"
                "💡 Agora você pode entrar na fila para encontrar alguém!\n"
                "🚫 **Lembrete:** Cada pessoa que você encontrar, NUNCA MAIS encontrará novamente!"
            ),
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
                title="💌 iTinder - Saiu da Fila",
                description=(
                    f"**🚪 Você saiu da fila!**\n\n"
                    f"**Seu perfil:** {get_gender_display(user_genders.get(user_id, 'homem'))}\n"
                    f"**Procurando:** {get_preference_display(user_preferences.get(user_id, 'ambos'))}\n\n"
                    "💡 Volte ao canal principal para configurar perfil ou entrar na fila novamente!\n\n"
                    "🔍 **Você não está mais procurando novas pessoas.**\n"
                    "🚫 **Bloqueios permanentes anteriores continuam ativos.**"
                ),
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
        
        if user.id not in user_genders or user.id not in user_preferences:
            embed_explicacao = discord.Embed(
                title="💌 iTinder - Configure seu Perfil",
                description=(
                    "❌ **Você precisa configurar seu perfil primeiro!**\n\n"
                    "📋 **COMO FUNCIONA:**\n"
                    "• 🔍 **Procura contínua** - Encontre múltiplas pessoas\n"
                    "• ⏰ **10 minutos** de conversa por par\n"
                    "• 🎧 **Call secreta** durante o chat\n"
                    "• 🚫 **BLOQUEIO PERMANENTE** - NUNCA MAIS encontrará a mesma pessoa\n"
                    "• 💬 Chat 100% anônimo\n\n"
                    "⚙️ **Volte ao canal principal e clique em `Configurar Perfil`!**"
                ),
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
            
            embed = discord.Embed(
                title="💌 iTinder - Na Fila Ativamente",
                description=(
                    f"**🔍 Você já está procurando pessoas!**\n\n"
                    f"**Seu perfil:** {gender_display}\n"
                    f"**Procurando:** {preference_display}\n\n"
                    "⏳ **Procurando pessoas compatíveis...**\n\n"
                    "💡 **Você pode:**\n"
                    "• Conversar com múltiplas pessoas ao mesmo tempo\n"
                    "• Cada chat dura 10 minutos\n"
                    "• 🚫 **BLOQUEIO PERMANENTE** - Nunca mais encontrará a mesma pessoa\n"
                    "• Clique em **Sair da Fila** para parar de procurar"
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
        
        fila_entry = {
            "user_id": user.id,
            "gender": user_genders[user.id],
            "preference": user_preferences[user.id]
        }
        
        fila_carentes[:] = [entry for entry in fila_carentes if entry["user_id"] != user.id]
        fila_carentes.append(fila_entry)
        
        gender_display = get_gender_display(user_genders[user.id])
        preference_display = get_preference_display(user_preferences[user.id])
        
        embed = discord.Embed(
            title="💌 iTinder - Procurando Pessoas!",
            description=(
                f"**🔍 Agora você está procurando pessoas!**\n\n"
                f"**Seu perfil:** {gender_display}\n"
                f"**Procurando:** {preference_display}\n\n"
                "🎯 **Modo de Procura Contínua Ativado**\n\n"
                "📋 **Como funciona:**\n"
                "• 🔍 **Procura contínua** por pessoas compatíveis\n"
                "• 💬 **Chats simultâneos** com múltiplas pessoas\n"
                "• ⏰ Cada chat dura **10 minutos**\n"
                "• 🎧 **Call secreta** disponível\n"
                "• 🚫 **BLOQUEIO PERMANENTE** - Nunca mais encontrará a mesma pessoa\n\n"
                "💡 **Você receberá novos chats automaticamente!**"
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
        
        if user.id not in user_genders or user.id not in user_preferences:
            embed_explicacao = discord.Embed(
                title="💌 iTinder - Configure seu Perfil",
                description=(
                    "❌ **Você precisa configurar seu perfil primeiro!**\n\n"
                    "📋 **COMO FUNCIONA:**\n"
                    "• 🔍 **Procura contínua** - Encontre múltiplas pessoas\n"
                    "• ⏰ **10 minutos** de conversa por par\n"
                    "• 🎧 **Call secreta** durante o chat\n"
                    "• 🚫 **BLOQUEIO PERMANENTE** - NUNCA MAIS encontrará a mesma pessoa\n"
                    "• 💬 Chat 100% anônimo\n\n"
                    "⚙️ **Clique em `Configurar Perfil` no canal principal!**"
                ),
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
            description=(
                f"**✅ Perfil Configurado!**\n\n"
                f"**Seu perfil:** {gender_display}\n"
                f"**Procurando:** {preference_display}\n\n"
                "🎯 **Modo de Procura Contínua**\n\n"
                "🚨 **BLOQUEIO PERMANENTE ATIVADO:**\n"
                "• 🚫 **Cada pessoa que você encontrar, NUNCA MAIS encontrará novamente**\n"
                "• 💡 Pense bem antes de recusar uma conversa!\n"
                "• ⚠️ **Esta regra é permanente e irreversível**\n\n"
                "💡 Clique em **Entrar na Fila** para começar a procurar múltiplas pessoas!"
            ),
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
        
        try:
            msg = await self.canal.fetch_message(self.message_id)
            embed = discord.Embed(
                title="💌 iTinder - Confirmação",
                description=(
                    f"{self.u1.display_name} {'✅' if self.u1.id in accepted else '⏳'}\n"
                    f"{self.u2.display_name} {'✅' if self.u2.id in accepted else '⏳'}\n\n"
                    f"⏰ **Aguardando ambos aceitarem...**\n"
                    f"⏳ **Chat será fechado em {ACCEPT_TIMEOUT//60} minutos se ninguém aceitar**\n"
                    "💡 **Lembrete:** 10 minutos de conversa após aceitar\n"
                    "🚨 **BLOQUEIO PERMANENTE:** Nunca mais se encontrarão após este encontro"
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
                    description=(
                        f"{self.u1.display_name} e {self.u2.display_name}\n\n"
                        "🎉 **A conversa foi liberada!**\n"
                        "⏰ **Tempo:** 10 minutos\n"
                        "🎧 **Call secreta:** Disponível durante o chat\n"
                        "💬 **Chat:** Anônimo e privado\n\n"
                        "🚨 **LEMBRE-SE:** Após esta conversa, **NUNCA MAIS** se encontrarão!\n"
                        "🌟 **Aproveite bem este momento único!**"
                    ),
                    color=0x66FF99
                )
                await msg.edit(embed=embed, view=enc_view)
            except Exception:
                pass
            
            active_channels[cid]["started"] = True
            asyncio.create_task(_auto_close_channel_after(canal=self.canal))
        
        await interaction.response.send_message("✅ Sua resposta foi registrada.", ephemeral=True)

    @discord.ui.button(label="❌ Recusar (NUNCA MAIS)", style=discord.ButtonStyle.danger, custom_id="conv_recusar")
    async def recusar(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        cid = self.canal.id
        if uid not in (self.u1.id, self.u2.id):
            await interaction.response.send_message("❌ Você não pode interagir aqui.", ephemeral=True)
            return

        # BLOQUEIO PERMANENTE - JÁ ESTÁ MARCADO NO ENCOUNTER_HISTORY
        # Apenas confirma e encerra
        
        try:
            msg = await self.canal.fetch_message(self.message_id)
            embed = discord.Embed(
                title="💔 Conversa Recusada - BLOQUEIO PERMANENTE",
                description=(
                    f"**{interaction.user.display_name} recusou a conversa.**\n\n"
                    "🚫 **BLOQUEIO PERMANENTE ATIVADO**\n"
                    "⚠️ **Vocês NUNCA MAIS se encontrarão no iTinder!**\n\n"
                    "📝 **O encontro foi registrado no sistema permanentemente.**\n"
                    "💫 Não desanime! Ainda há muitas outras pessoas para conhecer."
                ),
                color=0xFF3333
            )
            await msg.edit(embed=embed, view=None)
        except Exception:
            pass
        
        await asyncio.sleep(3)
        await encerrar_canal_e_cleanup(self.canal)
        await interaction.response.send_message(
            "🚫 Você recusou a conversa. **NUNCA MAIS** encontrará esta pessoa no iTinder!", 
            ephemeral=True
        )

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
            await interaction.response.send_message("❌ Já existe uma call ativa para este chat.", ephemeral=True)
            return

        call_channel = await criar_call_secreta(interaction.guild, self.u1, self.u2)
        if call_channel:
            data["call_channel"] = call_channel
            embed = discord.Embed(
                title="🎧 Call Secreta Criada!",
                description=(
                    f"**Call criada com sucesso!**\n\n"
                    f"📞 **Canal:** {call_channel.mention}\n"
                    f"👥 **Participantes:** {self.u1.display_name} e {self.u2.display_name}\n\n"
                    "💡 **A call será automaticamente encerrada quando o chat terminar.**\n"
                    "🚫 **Lembrete:** Após este chat, NUNCA MAIS se encontrarão!"
                ),
                color=0x66FF99
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message("❌ Erro ao criar a call secreta.", ephemeral=True)

    @discord.ui.button(label="🔒 Encerrar Chat", style=discord.ButtonStyle.danger, custom_id="encerrar_agora")
    async def encerrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.u1.id, self.u2.id):
            await interaction.response.send_message("❌ Você não pode encerrar.", ephemeral=True)
            return

        try:
            msg = None
            data = active_channels.get(self.canal.id, {})
            if data and data.get("message_id"):
                try:
                    msg = await self.canal.fetch_message(data["message_id"])
                except Exception:
                    msg = None
            if msg:
                embed = discord.Embed(
                    title="🔒 Chat Encerrado - BLOQUEIO PERMANENTE",
                    description=(
                        "O chat foi encerrado pelo usuário.\n\n"
                        "🚫 **BLOQUEIO PERMANENTE ATIVADO:**\n"
                        "⚠️ **Vocês NUNCA MAIS se encontrarão!**\n\n"
                        "💫 Obrigado por usar o iTinder!\n"
                        "🔍 **Você continua na fila procurando mais pessoas!**"
                    ),
                    color=0x9999FF
                )
                await msg.edit(embed=embed, view=None)
        except Exception:
            pass
        
        await encerrar_canal_e_cleanup(self.canal)
        await interaction.response.send_message("✅ Chat encerrado. 🚫 NUNCA MAIS encontrará esta pessoa! Você continua na fila.", ephemeral=True)

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
        title="💌 iTinder - Sistema de Chat Anônimo",
        description=(
            "**Bem-vindo ao iTinder!** 🌟\n\n"
            "🔒 **Sistema totalmente anônimo e seguro**\n\n"
            "🎯 **NOVO: Procura Contínua!**\n"
            "• 🔍 **Encontre múltiplas pessoas** simultaneamente\n"
            "• 💬 **Vários chats ao mesmo tempo**\n"
            "• ⏰ **10 minutos** por conversa\n"
            "• 🎧 **Call secreta** durante o chat\n"
            "• 🚫 **BLOQUEIO PERMANENTE** - NUNCA MAIS encontrará a mesma pessoa\n\n"
            "⚙️ **PASSO A PASSO:**\n"
            "1. Clique em `⚙️ Configurar Perfil`\n"
            "2. Escolha sua identidade e preferência\n"
            "3. Clique em `💌 Entrar na Fila`\n"
            "4. **Converse com várias pessoas!**\n"
            "5. Clique em `Sair da Fila` quando quiser parar\n\n"
            "🚨 **BLOQUEIO PERMANENTE - ATENÇÃO:**\n"
            "• **Cada pessoa que você encontrar, NUNCA MAIS encontrará novamente**\n"
            "• **Esta regra é permanente e irreversível**\n"
            "• **Funciona em TODAS as situações:** conversa, recusa, tempo esgotado\n"
            "• **Pense bem antes de cada interação!**\n\n"
            "⚠️ **ESTE CANAL FOI BLOQUEADO**\n"
            "Apenas os botões abaixo funcionam aqui."
        ),
        color=0xFF6B9E
    )
    embed.set_footer(text="iTinder - Conectando pessoas anonimamente 💫")
    
    view = TicketView()
    try:
        message = await interaction.channel.send(embed=embed, view=view)
        main_message_id = message.id
        await interaction.response.send_message("✅ Sistema iTinder configurado com sucesso! Canal bloqueado para mensagens comuns.", ephemeral=True)
    except Exception:
        await interaction.response.send_message("❌ Erro ao enviar mensagem de setup", ephemeral=True)

@bot.tree.command(name="reset_encounters", description="[ADMIN] Resetar todos os encontros e bloqueios")
async def reset_encounters(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Apenas administradores podem usar este comando.", ephemeral=True)
        return
    
    global ENCOUNTER_HISTORY, PERMANENT_BLOCKS
    ENCOUNTER_HISTORY.clear()
    PERMANENT_BLOCKS.clear()
    
    await interaction.response.send_message(
        "✅ **Todos os encontros e bloqueios foram resetados!**\n\n"
        "📝 Histórico de encontros: LIMPO\n"
        "🔒 Bloqueios permanentes: REMOVIDOS\n"
        "🔄 Os usuários agora podem se encontrar novamente",
        ephemeral=True
    )

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
async def on_member_join(member):
    pass

@bot.event
async def on_guild_channel_delete(channel):
    if not isinstance(channel, discord.TextChannel):
        return
    cid = channel.id
    if cid in active_channels:
        data = active_channels.get(cid, {})
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

@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel and before.channel != after.channel:
        for cid, data in active_channels.items():
            if data.get("call_channel") and data["call_channel"].id == before.channel.id:
                if len(before.channel.members) == 0:
                    try:
                        await before.channel.delete()
                        data["call_channel"] = None
                    except:
                        pass

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if hasattr(interaction, 'guild') and interaction.guild:
        if interaction.guild.id != MINHA_GUILD_ID:
            if interaction.type == discord.InteractionType.application_command:
                await interaction.response.send_message("❌ Este bot não está disponível neste servidor.", ephemeral=True)
            return
    await bot.process_application_commands(interaction)

@bot.event
async def on_ready():
    print(f"✅ iTinder online! Conectado como {bot.user.name}")
    
    guild = discord.Object(id=MINHA_GUILD_ID)
    try:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print("✅ Comandos sincronizados na guild!")
    except Exception as e:
        print(f"⚠️ Erro ao sincronizar comandos: {e}")
    
    guild_instance = bot.get_guild(MINHA_GUILD_ID)
    if guild_instance:
        asyncio.create_task(tentar_formar_dupla(guild_instance))

if __name__ == "__main__":
    token = os.getenv("TOKEN")
    if not token:
        print("❌ Token não encontrado!")
    else:
        bot.run(token)
