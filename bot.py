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

bot = commands.Bot(command_prefix="!", intents=intents)

fila_carentes = []
active_users = set()
active_channels = {}
user_genders = {}
user_preferences = {}
PAIR_COOLDOWNS = {}
PAIR_COOLDOWN_SECONDS = 5 * 60
ACCEPT_TIMEOUT = 60
CHANNEL_DURATION = 10 * 60

setup_channel_id = None
canal_bloqueado = False
main_message_id = None  # ID da mensagem principal fixa

def get_gender_display(gender):
    return "👤 Anônimo" if gender == "homem" else "👩 Anônima"

def get_preference_display(pref):
    if pref == "homem":
        return "👤 Anônimos"
    elif pref == "mulher":
        return "👩 Anônimas"
    else:
        return "💑 Ambos"

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

def gerar_nome_canal(guild):
    base = "chat-secreto"
    existing = {c.name for c in guild.text_channels}
    if base not in existing:
        return base
    i = 1
    while True:
        candidate = f"{base}-{i}"
        if candidate not in existing:
            return candidate
        i += 1

async def encerrar_canal_e_cleanup(canal):
    try:
        cid = canal.id
        data = active_channels.get(cid)
        if data:
            u1 = data.get("u1")
            u2 = data.get("u2")
            if u1:
                active_users.discard(u1)
            if u2:
                active_users.discard(u2)
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
    if len(fila_carentes) < 2:
        return

    for i in range(len(fila_carentes)):
        for j in range(i + 1, len(fila_carentes)):
            entry1 = fila_carentes[i]
            entry2 = fila_carentes[j]
            
            u1_id = entry1["user_id"]
            u2_id = entry2["user_id"]
            
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
                
            if u1_id in active_users or u2_id in active_users:
                continue
            if not can_pair(u1_id, u2_id):
                continue

            try:
                fila_carentes.remove(entry1)
                fila_carentes.remove(entry2)
            except ValueError:
                pass
            
            u1 = guild.get_member(u1_id)
            u2 = guild.get_member(u2_id)
            if not u1 or not u2:
                continue
            
            nome_canal = gerar_nome_canal(guild)
    
            categoria = discord.utils.get(guild.categories, name="RandoChat")
            if not categoria:
                try:
                    categoria = await guild.create_category("RandoChat")
                except Exception:
                    categoria = None
            
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                u1: discord.PermissionOverwrite(view_channel=True, send_messages=False),
                u2: discord.PermissionOverwrite(view_channel=True, send_messages=False),
            }
            
            try:
                if categoria:
                    canal = await categoria.create_text_channel(nome_canal, overwrites=overwrites, reason="Canal RandoChat temporário")
                else:
                    canal = await guild.create_text_channel(nome_canal, overwrites=overwrites, reason="Canal RandoChat temporário")
            except Exception:
                fila_carentes.append(entry1)
                fila_carentes.append(entry2)
                return
            
            active_users.add(u1_id)
            active_users.add(u2_id)
            active_channels[canal.id] = {
                "u1": u1_id,
                "u2": u2_id,
                "accepted": set(),
                "message_id": None,
                "created_at": time.time(),
                "started": False
            }
            
            gender1_display = get_gender_display(gender1)
            gender2_display = get_gender_display(gender2)
            
            embed = discord.Embed(
                title="💌 RandoChat - Par Encontrado!",
                description=(
                    f"**{u1.mention}** ({gender1_display}) & **{u2.mention}** ({gender2_display})\n\n"
                    "📋 **Como funciona:**\n"
                    "• Ambos precisam aceitar para começar a conversar\n"
                    "• ⏰ **10 minutos** de conversa após aceitar\n"
                    "• ❌ Se recusar: **5 minutos** de espera para encontrar a mesma pessoa\n"
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
                return
            
            aviso_text = (
                "💌 **Par encontrado no RandoChat!**\n\n"
                f"Você foi levado para {canal.mention}\n"
                "📝 **Lembrete:**\n"
                "• ⏰ 10 minutos de conversa\n"
                "• ❌ Recusar = 5 minutos de espera\n"
                "• 💬 Chat anônimo e seguro"
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
            return

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
            
            try:
                msg = await canal.fetch_message(data["message_id"])
                embed = discord.Embed(
                    title="⏰ Tempo Esgotado",
                    description=(
                        "O tempo para aceitar expirou.\n\n"
                        "⚠️ **Atenção:** Se você recusar alguém, só poderá encontrar a mesma pessoa novamente após **5 minutos**."
                    ),
                    color=0xFF9999
                )
                await msg.edit(embed=embed, view=None)
            except Exception:
                pass
            await asyncio.sleep(2)
            await encerrar_canal_e_cleanup(canal)

async def _auto_close_channel_after(canal, segundos=CHANNEL_DURATION):
    await asyncio.sleep(segundos)
    if canal.id not in active_channels:
        return
    try:
        data = active_channels.get(canal.id)
        if data:
            try:
                msg = await canal.fetch_message(data["message_id"])
                embed = discord.Embed(
                    title="⏰ Tempo de Conversa Esgotado",
                    description=(
                        "Seus **10 minutos** de conversa terminaram!\n\n"
                        "💫 Esperamos que tenha sido uma boa experiência.\n"
                        "Volte sempre ao RandoChat! 💌"
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

    @discord.ui.button(label="👤 Anônimo", style=discord.ButtonStyle.primary, custom_id="gender_homem")
    async def set_homem(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_genders[interaction.user.id] = "homem"
        
        embed = discord.Embed(
            title="⚙️ Configurar Perfil",
            description="✅ **Você é:** 👤 Anônimo\n\nAgora escolha quem você quer encontrar:",
            color=0x66FF99
        )
        await self.setup_message.edit(embed=embed, view=PreferenceSetupView(self.setup_message))
        await interaction.response.defer()

    @discord.ui.button(label="👩 Anônima", style=discord.ButtonStyle.primary, custom_id="gender_mulher")
    async def set_mulher(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_genders[interaction.user.id] = "mulher"
        
        embed = discord.Embed(
            title="⚙️ Configurar Perfil",
            description="✅ **Você é:** 👩 Anônima\n\nAgora escolha quem você quer encontrar:",
            color=0x66FF99
        )
        await self.setup_message.edit(embed=embed, view=PreferenceSetupView(self.setup_message))
        await interaction.response.defer()

class PreferenceSetupView(discord.ui.View):
    def __init__(self, setup_message):
        super().__init__(timeout=None)
        self.setup_message = setup_message

    @discord.ui.button(label="👤 Anônimos", style=discord.ButtonStyle.primary, custom_id="pref_homem")
    async def pref_homem(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_preferences[interaction.user.id] = "homem"
        await self.finalizar_configuracao(interaction)

    @discord.ui.button(label="👩 Anônimas", style=discord.ButtonStyle.primary, custom_id="pref_mulher")
    async def pref_mulher(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_preferences[interaction.user.id] = "mulher"
        await self.finalizar_configuracao(interaction)

    @discord.ui.button(label="💑 Ambos", style=discord.ButtonStyle.primary, custom_id="pref_ambos")
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
        removed = False
        for entry in list(fila_carentes):
            if entry["user_id"] == self.user_id:
                fila_carentes.remove(entry)
                removed = True
                break
        
        if removed:
            embed = discord.Embed(
                title="💌 RandoChat - Saiu da Fila",
                description=(
                    f"**🚪 Você saiu da fila!**\n\n"
                    f"**Seu perfil:** {get_gender_display(user_genders.get(interaction.user.id, 'homem'))}\n"
                    f"**Procurando:** {get_preference_display(user_preferences.get(interaction.user.id, 'ambos'))}\n\n"
                    "💡 Clique em **💌 Entrar na Fila** para voltar a procurar!"
                ),
                color=0xFF9999
            )
            await interaction.response.edit_message(embed=embed, view=TicketView())
        else:
            await interaction.response.send_message("❌ Você não estava na fila.", ephemeral=True)

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⚙️ Configurar Perfil", style=discord.ButtonStyle.primary, custom_id="config_gender")
    async def config_gender(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Verifica se o usuário já tem perfil configurado
        if interaction.user.id in user_genders and interaction.user.id in user_preferences:
            # Se já tem perfil, mostra a mensagem personalizada
            gender = user_genders[interaction.user.id]
            preference = user_preferences[interaction.user.id]
            gender_display = get_gender_display(gender)
            preference_display = get_preference_display(preference)
            
            embed_personalizado = discord.Embed(
                title="💌 RandoChat - Perfil Configurado!",
                description=(
                    f"**✅ Seu perfil está configurado!**\n\n"
                    f"**Você:** {gender_display}\n"
                    f"**Procurando:** {preference_display}\n\n"
                    "📋 **COMO FUNCIONA:**\n"
                    "• ⏰ **10 minutos** de conversa por par\n"
                    "• ❌ Recusar alguém = **5 minutos** de espera\n"
                    "• 🔍 Encontre pessoas por preferência\n"
                    "• 💬 Chat 100% anônimo\n\n"
                    "💡 **Clique no botão abaixo para entrar na fila!**"
                ),
                color=0xFF6B9E
            )
            await interaction.response.send_message(embed=embed_personalizado, view=TicketView(), ephemeral=True)
            return
        
        # Se não tem perfil, inicia a configuração
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
            # Se não tem perfil, mostra a mensagem de explicação completa
            embed_explicacao = discord.Embed(
                title="💌 RandoChat - Sistema de Chat Anônimo",
                description=(
                    "**Bem-vindo ao RandoChat!** 🌟\n\n"
                    "🔒 **Sistema totalmente anônimo e seguro**\n\n"
                    "📋 **COMO FUNCIONA:**\n"
                    "• ⏰ **10 minutos** de conversa por par\n"
                    "• ❌ Recusar alguém = **5 minutos** de espera\n"
                    "• 🔍 Encontre pessoas por preferência\n"
                    "• 💬 Chat 100% anônimo\n\n"
                    "⚙️ **Configure seu perfil primeiro!**"
                ),
                color=0xFF6B9E
            )
            await interaction.response.send_message(embed=embed_explicacao, view=TicketView(), ephemeral=True)
            return

        if user.id in active_users:
            gender_display = get_gender_display(user_genders[user.id])
            preference_display = get_preference_display(user_preferences[user.id])
            
            embed = discord.Embed(
                title="💌 RandoChat - Perfil Configurado!",
                description=(
                    f"**💬 Você já está em um chat ativo!**\n\n"
                    f"**Seu perfil:** {gender_display}\n"
                    f"**Procurando:** {preference_display}\n\n"
                    "Aguarde o chat atual terminar para entrar na fila novamente."
                ),
                color=0xFF9999
            )
            await interaction.response.send_message(embed=embed, view=TicketView(), ephemeral=True)
            return
        
        for entry in fila_carentes:
            if entry["user_id"] == user.id:
                gender_display = get_gender_display(user_genders[user.id])
                preference_display = get_preference_display(user_preferences[user.id])
                
                embed = discord.Embed(
                    title="💌 RandoChat - Perfil Configurado!",
                    description=(
                        f"**⏳ Você já está na fila!**\n\n"
                        f"**Seu perfil:** {gender_display}\n"
                        f"**Procurando:** {preference_display}\n\n"
                        "Aguarde enquanto encontramos alguém compatível..."
                    ),
                    color=0x66FF99
                )
                await interaction.response.edit_message(embed=embed, view=LeaveQueueView(user.id))
                return

        fila_entry = {
            "user_id": user.id,
            "gender": user_genders[user.id],
            "preference": user_preferences[user.id]
        }
        fila_carentes.append(fila_entry)
        
        gender_display = get_gender_display(user_genders[user.id])
        preference_display = get_preference_display(user_preferences[user.id])
        
        embed = discord.Embed(
            title="💌 RandoChat - Perfil Configurado!",
            description=(
                f"**✅ Entrou na Fila!**\n\n"
                f"**Seu perfil:** {gender_display}\n"
                f"**Procurando:** {preference_display}\n\n"
                "🔍 **Procurando alguém compatível...**\n\n"
                "📝 **Lembretes:**\n"
                "• ⏰ 10 minutos de conversa\n"
                "• ❌ Recusar = 5 minutos de espera\n"
                "• 💬 Chat anônimo"
            ),
            color=0x66FF99
        )
        await interaction.response.edit_message(embed=embed, view=LeaveQueueView(user.id))
        await tentar_formar_dupla(interaction.guild)

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
                title="💌 RandoChat - Confirmação",
                description=(
                    f"{self.u1.mention} {'✅' if self.u1.id in accepted else '❌'}\n"
                    f"{self.u2.mention} {'✅' if self.u2.id in accepted else '❌'}\n\n"
                    "⏰ **Aguardando ambos aceitarem...**\n"
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

    @discord.ui.button(label="🔒 Encerrar Agora", style=discord.ButtonStyle.danger, custom_id="encerrar_agora")
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
                        "💫 Obrigado por usar o RandoChat!\n"
                        "Volte sempre para novas conversas! 💌"
                    ),
                    color=0x9999FF
                )
                await msg.edit(embed=embed, view=None)
        except Exception:
            pass
        
        await encerrar_canal_e_cleanup(self.canal)
        await interaction.response.send_message("✅ Chat encerrado.", ephemeral=True)

@bot.tree.command(name="setupcarente", description="Configura o sistema RandoChat (apenas admin)")
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
        
        categoria = discord.utils.get(interaction.guild.categories, name="RandoChat")
        if not categoria:
            try:
                await interaction.guild.create_category("RandoChat")
            except Exception:
                pass
                
    except Exception:
        await interaction.response.send_message("❌ Erro ao bloquear o canal", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="💌 RandoChat - Sistema de Chat Anônimo",
        description=(
            "**Bem-vindo ao RandoChat!** 🌟\n\n"
            "🔒 **Sistema totalmente anônimo e seguro**\n\n"
            "📋 **COMO FUNCIONA:**\n"
            "• ⏰ **10 minutos** de conversa por par\n"
            "• ❌ Recusar alguém = **5 minutos** de espera para encontrar a mesma pessoa\n"
            "• 🔍 Encontre pessoas por preferência\n"
            "• 💬 Chat 100% anônimo\n\n"
            "⚙️ **PASSO A PASSO:**\n"
            "1. Clique em `⚙️ Configurar Perfil`\n"
            "2. Escolha sua identidade e preferência\n"
            "3. Clique em `💌 Entrar na Fila`\n"
            "4. Aguarde encontrar alguém compatível\n"
            "5. Aceite o chat e converse por 10 minutos!\n\n"
            "⚠️ **ESTE CANAL FOI BLOQUEADO**\n"
            "Apenas os botões abaixo funcionam aqui."
        ),
        color=0xFF6B9E
    )
    embed.set_footer(text="RandoChat - Conectando pessoas anonimamente 💫")
    
    view = TicketView()
    try:
        message = await interaction.channel.send(embed=embed, view=view)
        main_message_id = message.id
        await interaction.response.send_message("✅ Sistema RandoChat configurado com sucesso! Canal bloqueado para mensagens comuns.", ephemeral=True)
    except Exception:
        await interaction.response.send_message("❌ Erro ao enviar mensagem de setup", ephemeral=True)

@bot.event
async def on_guild_channel_delete(channel):
    if not isinstance(channel, discord.TextChannel):
        return
    cid = channel.id
    if cid in active_channels:
        data = active_channels.get(cid, {})
        u1 = data.get("u1")
        u2 = data.get("u2")
        if u1:
            active_users.discard(u1)
        if u2:
            active_users.discard(u2)
        try:
            del active_channels[cid]
        except Exception:
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
    print(f"✅ RandoChat online! Conectado como {bot.user.name}")
    
    guild = discord.Object(id=MINHA_GUILD_ID)
    try:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print("✅ Comandos sincronizados na guild!")
    except Exception as e:
        print(f"⚠️ Erro ao sincronizar comandos: {e}")

if __name__ == "__main__":
    token = os.getenv("TOKEN")
    if not token:
        print("❌ Token não encontrado!")
    else:
        bot.run(token)
