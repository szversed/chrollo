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
intents.voice_states = True  # Adicionado para calls de voz

bot = commands.Bot(command_prefix="!", intents=intents)

fila_carentes = []
active_users = set()
active_channels = {}
user_genders = {}
user_preferences = {}
PAIR_COOLDOWNS = {}
PAIR_COOLDOWN_SECONDS = 5 * 60
ACCEPT_TIMEOUT = 60  # Mudado para 60 segundos
CHANNEL_DURATION = 10 * 60

setup_channel_id = None
canal_bloqueado = False
main_message_id = None  # ID da mensagem principal fixa
user_messages = {}  # Dicionário para armazenar a mensagem individual de cada usuário
user_queues = {}  # Controla se o usuário está ativamente na fila

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

def can_pair(u1_id, u2_id):
    key = pair_key(u1_id, u2_id)
    ts = PAIR_COOLDOWNS.get(key)
    if not ts:
        return True
    return time.time() >= ts

def set_pair_cooldown(u1_id, u2_id):
    key = pair_key(u1_id, u2_id)
    PAIR_COOLDOWNS[key] = time.time() + PAIR_COOLDOWN_SECONDS

def gerar_nome_canal(guild, user1_id, user2_id):
    base = f"chat-{user1_id}-{user2_id}"[-20:]  # Nome único baseado nos IDs
    existing = {c.name for c in guild.text_channels}
    if base not in existing:
        return base
    i = 1
    while True:
        candidate = f"{base}-{i}"
        if candidate not in existing:
            return candidate
        i += 1

def gerar_nome_call(guild):
    base = "call-secreta"
    existing = {c.name for c in guild.voice_channels}
    if base not in existing:
        return base
    i = 1
    while True:
        candidate = f"{base}-{i}"
        if candidate not in existing:
            return candidate
        i += 1

async def criar_call_secreta(guild, u1, u2):
    """Cria uma call de voz temporária para o par"""
    nome_call = gerar_nome_call(guild)
    
    categoria = discord.utils.get(guild.categories, name="iTinder")
    if not categoria:
        try:
            categoria = await guild.create_category("iTinder")
        except Exception:
            categoria = None
    
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
        u1: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        u2: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
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
        if data:
            u1 = data.get("u1")
            u2 = data.get("u2")
            # NÃO remove de active_users aqui - o usuário pode continuar na fila
            
            # Encerra a call associada se existir
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
        await asyncio.sleep(2)  # Verifica a cada 2 segundos
        
        # Filtra usuários que ainda estão ativamente na fila
        usuarios_na_fila = [entry for entry in fila_carentes if user_queues.get(entry["user_id"], False)]
        
        if len(usuarios_na_fila) < 2:
            continue

        for i in range(len(usuarios_na_fila)):
            for j in range(i + 1, len(usuarios_na_fila)):
                entry1 = usuarios_na_fila[i]
                entry2 = usuarios_na_fila[j]
                
                u1_id = entry1["user_id"]
                u2_id = entry2["user_id"]
                
                # Verifica se ambos ainda estão ativamente na fila
                if not user_queues.get(u1_id, False) or not user_queues.get(u2_id, False):
                    continue
                
                # Verifica se não estão já em um chat juntos
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
                    
                if not can_pair(u1_id, u2_id):
                    continue

                # Remove da fila temporariamente durante a formação do par
                try:
                    fila_carentes.remove(entry1)
                    fila_carentes.remove(entry2)
                except ValueError:
                    continue
                
                u1 = guild.get_member(u1_id)
                u2 = guild.get_member(u2_id)
                if not u1 or not u2:
                    # Se não encontrou os membros, recoloca na fila
                    fila_carentes.append(entry1)
                    fila_carentes.append(entry2)
                    continue
                
                nome_canal = gerar_nome_canal(guild, u1_id, u2_id)
        
                categoria = discord.utils.get(guild.categories, name="iTinder")
                if not categoria:
                    try:
                        categoria = await guild.create_category("iTinder")
                    except Exception:
                        categoria = None
                
                # Obtém o dono do servidor
                owner = guild.owner
                
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                    u1: discord.PermissionOverwrite(view_channel=True, send_messages=False),
                    u2: discord.PermissionOverwrite(view_channel=True, send_messages=False),
                    owner: discord.PermissionOverwrite(view_channel=True, send_messages=False),  # Dono pode apenas ver
                }
                
                try:
                    if categoria:
                        canal = await categoria.create_text_channel(nome_canal, overwrites=overwrites, reason="Canal iTinder temporário")
                    else:
                        canal = await guild.create_text_channel(nome_canal, overwrites=overwrites, reason="Canal iTinder temporário")
                except Exception:
                    fila_carentes.append(entry1)
                    fila_carentes.append(entry2)
                    continue
                
                active_channels[canal.id] = {
                    "u1": u1_id,
                    "u2": u2_id,
                    "accepted": set(),
                    "message_id": None,
                    "created_at": time.time(),
                    "started": False,
                    "call_channel": None,  # Inicialmente sem call
                    "warning_sent": False  # Para controlar o aviso de 1 minuto
                }
                
                gender1_display = get_gender_display(gender1)
                gender2_display = get_gender_display(gender2)
                
                embed = discord.Embed(
                    title="💌 iTinder - Par Encontrado!",
                    description=(
                        f"**{u1.mention}** ({gender1_display}) & **{u2.mention}** ({gender2_display})\n\n"
                        "📋 **Como funciona:**\n"
                        "• Ambos precisam aceitar para começar a conversar\n"
                        "• ⏰ **10 minutos** de conversa após aceitar\n"
                        "• 🎧 **Call secreta** disponível durante o chat\n"
                        "• ❌ Se recusar: **5 minutos** de espera para encontrar a mesma pessoa\n"
                        "• ⏳ **Chat será fechado em 1 minuto se ninguém aceitar**\n"
                        "• 🔒 Chat totalmente anônimo e privado\n\n"
                        "💡 **Dica:** Sejam respeitosos e aproveitem a conversa!"
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
                    fila_carentes.append(entry1)
                    fila_carentes.append(entry2)
                    continue
                
                aviso_text = (
                    "💌 **Novo par encontrado no iTinder!**\n\n"
                    f"Você foi levado para {canal.mention}\n"
                    "📝 **Lembrete:**\n"
                    "• ⏰ 10 minutos de conversa\n"
                    "• 🎧 Call secreta disponível\n"
                    "• ❌ Recusar = 5 minutos de espera\n"
                    "• ⏳ **Aceite em 1 minuto ou o chat será fechado**\n"
                    "• 💬 Chat anônimo e seguro\n\n"
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
                break  # Sai do loop interno após formar um par

async def _accept_timeout_handler(canal, timeout=ACCEPT_TIMEOUT):
    await asyncio.sleep(timeout)
    data = active_channels.get(canal.id)
    if not data:
        return
    
    if not data.get("started", False):
        accepted = data.get("accepted", set())
        if len(accepted) < 2:
            u1 = data.get("u1")
            u2 = data.get("u2")
            if u1 and u2:
                set_pair_cooldown(u1, u2)
                # Recoloca os usuários na fila se ainda estiverem ativos
                if user_queues.get(u1, False):
                    user_entry = {
                        "user_id": u1,
                        "gender": user_genders.get(u1, "homem"),
                        "preference": user_preferences.get(u1, "ambos")
                    }
                    if user_entry not in fila_carentes:
                        fila_carentes.append(user_entry)
                
                if user_queues.get(u2, False):
                    user_entry = {
                        "user_id": u2,
                        "gender": user_genders.get(u2, "homem"),
                        "preference": user_preferences.get(u2, "ambos")
                    }
                    if user_entry not in fila_carentes:
                        fila_carentes.append(user_entry)
            
            try:
                msg = await canal.fetch_message(data["message_id"])
                embed = discord.Embed(
                    title="⏰ Tempo Esgotado",
                    description=(
                        "O tempo para aceitar expirou (1 minuto).\n\n"
                        "⚠️ **Nenhum dos dois aceitou a conversa a tempo.**\n"
                        "💫 Volte ao canal principal para tentar novamente!"
                    ),
                    color=0xFF9999
                )
                await msg.edit(embed=embed, view=None)
            except Exception:
                pass
            await asyncio.sleep(2)
            await encerrar_canal_e_cleanup(canal)

async def _auto_close_channel_after(canal, segundos=CHANNEL_DURATION):
    # Aviso de 1 minuto antes do fim
    await asyncio.sleep(segundos - 60)  # 9 minutos (aviso com 1 minuto restante)
    
    if canal.id not in active_channels:
        return
    
    data = active_channels.get(canal.id)
    if data and not data.get("warning_sent", False):
        try:
            embed = discord.Embed(
                title="⏰ Aviso: Chat Terminando",
                description=(
                    "**⚠️ O chat termina em 1 minuto!**\n\n"
                    "⏳ **Tempo restante:** 1 minuto\n"
                    "💡 **Dica:** Troquem contatos se quiserem continuar a conversa!\n"
                    "🔒 O chat será automaticamente fechado em 60 segundos."
                ),
                color=0xFFA500  # Laranja para aviso
            )
            await canal.send(embed=embed)
            active_channels[canal.id]["warning_sent"] = True
        except Exception:
            pass
    
    # Espera o último minuto e encerra
    await asyncio.sleep(60)
    
    if canal.id not in active_channels:
        return
    try:
        data = active_channels.get(canal.id)
        if data:
            u1 = data.get("u1")
            u2 = data.get("u2")
            
            # Recoloca os usuários na fila se ainda estiverem ativos
            if user_queues.get(u1, False):
                user_entry = {
                    "user_id": u1,
                    "gender": user_genders.get(u1, "homem"),
                    "preference": user_preferences.get(u1, "ambos")
                }
                if user_entry not in fila_carentes:
                    fila_carentes.append(user_entry)
            
            if user_queues.get(u2, False):
                user_entry = {
                    "user_id": u2,
                    "gender": user_genders.get(u2, "homem"),
                    "preference": user_preferences.get(u2, "ambos")
                }
                if user_entry not in fila_carentes:
                    fila_carentes.append(user_entry)
            
            try:
                msg = await canal.fetch_message(data["message_id"])
                embed = discord.Embed(
                    title="⏰ Tempo de Conversa Esgotado",
                    description=(
                        "Seus **10 minutos** de conversa terminaram!\n\n"
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
        
        # Apaga a mensagem de configuração ephemeral
        await self.setup_message.delete()
        
        # Envia uma mensagem temporária que será apagada após 5 segundos
        embed_explicacao = discord.Embed(
            title="⚙️ Configuração Concluída",
            description=(
                f"✅ **Perfil configurado com sucesso!**\n\n"
                f"**Você:** {gender_display}\n"
                f"**Procurando:** {preference_display}\n\n"
                "💡 Agora você pode entrar na fila para encontrar alguém!"
            ),
            color=0x66FF99
        )
        
        # Envia a mensagem e agenda para apagar após 5 segundos
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
        
        # Remove da fila ativa
        user_queues[interaction.user.id] = False
        
        # Remove todas as entradas do usuário na fila
        fila_carentes[:] = [entry for entry in fila_carentes if entry["user_id"] != interaction.user.id]
        
        # Atualiza a MESMA mensagem individual do usuário
        user_id = interaction.user.id
        if user_id in user_messages:
            embed = discord.Embed(
                title="💌 iTinder - Saiu da Fila",
                description=(
                    f"**🚪 Você saiu da fila!**\n\n"
                    f"**Seu perfil:** {get_gender_display(user_genders.get(user_id, 'homem'))}\n"
                    f"**Procurando:** {get_preference_display(user_preferences.get(user_id, 'ambos'))}\n\n"
                    "💡 Volte ao canal principal para configurar perfil ou entrar na fila novamente!\n\n"
                    "🔍 **Você não está mais procurando novas pessoas.**"
                ),
                color=0xFF9999
            )
            await user_messages[user_id].edit(embed=embed, view=IndividualView())
            await interaction.response.defer()
        else:
            await interaction.response.send_message("✅ Você saiu da fila.", ephemeral=True)

# VIEW para o embed individual do usuário (SEM botão de Configurar Perfil)
class IndividualView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💌 Entrar na Fila", style=discord.ButtonStyle.success, custom_id="individual_entrar")
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        
        if user.id not in user_genders or user.id not in user_preferences:
            # Se não tem perfil, mostra mensagem na MESMA mensagem individual
            embed_explicacao = discord.Embed(
                title="💌 iTinder - Configure seu Perfil",
                description=(
                    "❌ **Você precisa configurar seu perfil primeiro!**\n\n"
                    "📋 **COMO FUNCIONA:**\n"
                    "• 🔍 **Procura contínua** - Encontre múltiplas pessoas\n"
                    "• ⏰ **10 minutos** de conversa por par\n"
                    "• 🎧 **Call secreta** durante o chat\n"
                    "• ❌ Recusar alguém = **5 minutos** de espera\n"
                    "• 💬 Chat 100% anônimo\n\n"
                    "⚙️ **Volte ao canal principal e clique em `Configurar Perfil`!**"
                ),
                color=0xFF6B9E
            )
            
            # Se já existe uma mensagem individual, atualiza ela
            if user.id in user_messages:
                await user_messages[user.id].edit(embed=embed_explicacao, view=IndividualView())
                await interaction.response.defer()
            else:
                # Se não existe, cria uma nova mensagem individual
                message = await interaction.response.send_message(embed=embed_explicacao, view=IndividualView(), ephemeral=True)
                if hasattr(message, 'message'):
                    user_messages[user.id] = message.message
                else:
                    user_messages[user.id] = await interaction.original_response()
            return

        # Verifica se já está ativamente na fila
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
                    "• Clique em **Sair da Fila** para parar de procurar"
                ),
                color=0x66FF99
            )
            
            # Atualiza a MESMA mensagem individual
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

        # Entra na fila ativamente
        user_queues[user.id] = True
        
        fila_entry = {
            "user_id": user.id,
            "gender": user_genders[user.id],
            "preference": user_preferences[user.id]
        }
        
        # Remove entradas antigas do usuário e adiciona a nova
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
                "• ❌ Recusar = 5 minutos de espera\n\n"
                "💡 **Você receberá novos chats automaticamente!**"
            ),
            color=0x66FF99
        )
        
        # Atualiza a MESMA mensagem individual
        if user.id in user_messages:
            await user_messages[user.id].edit(embed=embed, view=LeaveQueueView(user.id))
            await interaction.response.defer()
        else:
            message = await interaction.response.send_message(embed=embed, view=LeaveQueueView(user.id), ephemeral=True)
            if hasattr(message, 'message'):
                user_messages[user.id] = message.message
            else:
                user_messages[user.id] = await interaction.original_response()

# VIEW PRINCIPAL: Para o embed principal (COM botão de Configurar Perfil)
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="👨🏻👩🏻 Configurar Perfil", style=discord.ButtonStyle.primary, custom_id="config_gender")
    async def config_gender(self, interaction: discord.Interaction, button: discord.ui.Button):
        # SEMPRE inicia a configuração, mesmo se já tiver perfil
        embed = discord.Embed(
            title="⚙️ Configurar Perfil",
            description="👥 **Escolha como você se identifica:**",
            color=0x66FF99
        )
        
        # Envia uma mensagem ephemeral para configuração
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
            # Se não tem perfil, cria o embed individual
            embed_explicacao = discord.Embed(
                title="💌 iTinder - Configure seu Perfil",
                description=(
                    "❌ **Você precisa configurar seu perfil primeiro!**\n\n"
                    "📋 **COMO FUNCIONA:**\n"
                    "• 🔍 **Procura contínua** - Encontre múltiplas pessoas\n"
                    "• ⏰ **10 minutos** de conversa por par\n"
                    "• 🎧 **Call secreta** durante o chat\n"
                    "• ❌ Recusar alguém = **5 minutos** de espera\n"
                    "• 💬 Chat 100% anônimo\n\n"
                    "⚙️ **Clique em `Configurar Perfil` no canal principal!**"
                ),
                color=0xFF6B9E
            )
            
            # Cria uma nova mensagem individual
            message = await interaction.response.send_message(embed=embed_explicacao, view=IndividualView(), ephemeral=True)
            if hasattr(message, 'message'):
                user_messages[user.id] = message.message
            else:
                user_messages[user.id] = await interaction.original_response()
            return

        # Se tem perfil, cria o embed individual para entrar na fila
        gender_display = get_gender_display(user_genders[user.id])
        preference_display = get_preference_display(user_preferences[user.id])
        
        embed_inicial = discord.Embed(
            title="💌 iTinder - Pronto para Conversar",
            description=(
                f"**✅ Perfil Configurado!**\n\n"
                f"**Seu perfil:** {gender_display}\n"
                f"**Procurando:** {preference_display}\n\n"
                "🎯 **Modo de Procura Contínua**\n\n"
                "💡 Clique em **Entrar na Fila** para começar a procurar múltiplas pessoas!"
            ),
            color=0x66FF99
        )
        
        # Cria uma nova mensagem individual
        message = await interaction.response.send_message(embed=embed_inicial, view=IndividualView(), ephemeral=True)
        if hasattr(message, 'message'):
            user_messages[user.id] = message.message
        else:
            user_messages[user.id] = await interaction.original_response()

# (As classes ConversationView e EncerrarView permanecem as mesmas do código anterior)
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
                    f"{self.u1.mention} {'✅' if self.u1.id in accepted else '⏳'}\n"
                    f"{self.u2.mention} {'✅' if self.u2.id in accepted else '⏳'}\n\n"
                    "⏰ **Aguardando ambos aceitarem...**\n"
                    "⏳ **Chat será fechado em 1 minuto se ninguém aceitar**\n"
                    "💡 **Lembrete:** 10 minutos de conversa após aceitar"
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
                        f"{self.u1.mention} e {self.u2.mention}\n\n"
                        "🎉 **A conversa foi liberada!**\n"
                        "⏰ **Tempo:** 10 minutos\n"
                        "🎧 **Call secreta:** Disponível durante o chat\n"
                        "💬 **Chat:** Anônimo e privado\n\n"
                        "🌟 **Dica:** Sejam criativos e respeitosos!\n"
                        "📝 Compartilhem interesses, sonhos, histórias..."
                    ),
                    color=0x66FF99
                )
                await msg.edit(embed=embed, view=enc_view)
            except Exception:
                pass
            
            active_channels[cid]["started"] = True
            asyncio.create_task(_auto_close_channel_after(canal=self.canal))
        
        await interaction.response.send_message("✅ Sua resposta foi registrada.", ephemeral=True)

    @discord.ui.button(label="❌ Recusar", style=discord.ButtonStyle.danger, custom_id="conv_recusar")
    async def recusar(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        cid = self.canal.id
        if uid not in (self.u1.id, self.u2.id):
            await interaction.response.send_message("❌ Você não pode interagir aqui.", ephemeral=True)
            return

        set_pair_cooldown(self.u1.id, self.u2.id)
        
        # Recoloca os usuários na fila se ainda estiverem ativos
        if user_queues.get(self.u1.id, False):
            user_entry = {
                "user_id": self.u1.id,
                "gender": user_genders.get(self.u1.id, "homem"),
                "preference": user_preferences.get(self.u1.id, "ambos")
            }
            if user_entry not in fila_carentes:
                fila_carentes.append(user_entry)
        
        if user_queues.get(self.u2.id, False):
            user_entry = {
                "user_id": self.u2.id,
                "gender": user_genders.get(self.u2.id, "homem"),
                "preference": user_preferences.get(self.u2.id, "ambos")
            }
            if user_entry not in fila_carentes:
                fila_carentes.append(user_entry)
        
        try:
            msg = await self.canal.fetch_message(self.message_id)
            embed = discord.Embed(
                title="💔 Conversa Recusada",
                description=(
                    f"{interaction.user.mention} recusou a conversa.\n\n"
                    "⚠️ **Atenção:** Se você recusar alguém, só poderá encontrar a mesma pessoa novamente após **5 minutos**.\n\n"
                    "💫 Não desanime! Tente novamente com outra pessoa."
                ),
                color=0xFF9999
            )
            await msg.edit(embed=embed, view=None)
        except Exception:
            pass
        
        await asyncio.sleep(2)
        await encerrar_canal_e_cleanup(self.canal)
        await interaction.response.send_message("❌ Você recusou a conversa.", ephemeral=True)

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

        # Verifica se já existe uma call
        if data.get("call_channel"):
            await interaction.response.send_message("❌ Já existe uma call ativa para este chat.", ephemeral=True)
            return

        # Cria a call secreta
        call_channel = await criar_call_secreta(interaction.guild, self.u1, self.u2)
        if call_channel:
            data["call_channel"] = call_channel
            embed = discord.Embed(
                title="🎧 Call Secreta Criada!",
                description=(
                    f"**Call criada com sucesso!**\n\n"
                    f"📞 **Canal:** {call_channel.mention}\n"
                    f"👥 **Participantes:** {self.u1.mention} e {self.u2.mention}\n\n"
                    "💡 **A call será automaticamente encerrada quando o chat terminar.**\n"
                    "⚠️ **Lembrete:** A call é totalmente anônima e segura."
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

        data = active_channels.get(self.canal.id, {})
        try:
            msg = None
            if data and data.get("message_id"):
                try:
                    msg = await self.canal.fetch_message(data["message_id"])
                except Exception:
                    msg = None
            if msg:
                embed = discord.Embed(
                    title="🔒 Chat Encerrado",
                    description=(
                        "O chat foi encerrado pelo usuário.\n\n"
                        "💫 Obrigado por usar o iTinder!\n"
                        "🔍 **Você continua na fila procurando mais pessoas!**"
                    ),
                    color=0x9999FF
                )
                await msg.edit(embed=embed, view=None)
        except Exception:
            pass
        
        await encerrar_canal_e_cleanup(canal)
        await interaction.response.send_message("✅ Chat encerrado. Você continua na fila!", ephemeral=True)

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
            "• ❌ Recusar = **5 minutos** de espera\n\n"
            "⚙️ **PASSO A PASSO:**\n"
            "1. Clique em `⚙️ Configurar Perfil`\n"
            "2. Escolha sua identidade e preferência\n"
            "3. Clique em `💌 Entrar na Fila`\n"
            "4. **Converse com várias pessoas!**\n"
            "5. Clique em `Sair da Fila` quando quiser parar\n\n"
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

# Inicia o loop de formação de duplas quando o bot liga
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
    
    # Inicia o loop contínuo de formação de duplas
    guild_instance = bot.get_guild(MINHA_GUILD_ID)
    if guild_instance:
        asyncio.create_task(tentar_formar_dupla(guild_instance))

# (Os outros eventos permanecem iguais)
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
        u1 = data.get("u1")
        u2 = data.get("u2")
        
        # Encerra a call associada se existir
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
    """Monitora calls secretas para limpeza automática"""
    # Se um usuário saiu de uma call que está no active_channels
    if before.channel and before.channel != after.channel:
        for cid, data in active_channels.items():
            if data.get("call_channel") and data["call_channel"].id == before.channel.id:
                # Verifica se a call está vazia
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

if __name__ == "__main__":
    token = os.getenv("TOKEN")
    if not token:
        print("❌ Token não encontrado!")
    else:
        bot.run(token)
