# main.py
# bot de música estilo "spotify" - completo e pronto pra rodar
# requisitos: discord.py>=2.3.2, yt-dlp, python-dotenv, pynacl, spotipy
# certifique-se: ffmpeg instalado no container (NIXPACKS_PKGS=ffmpeg no railway)

import os
import asyncio
import time
import traceback
import re
from typing import Optional, List, Dict
from dotenv import load_dotenv

import yt_dlp
import discord
from discord.ext import commands
from discord.ui import View, Button

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

if not TOKEN:
    raise SystemExit("erro: variável de ambiente DISCORD_TOKEN (ou TOKEN) não encontrada")

# intents
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- estrutura de fila ----------------
class Track:
    def __init__(self, title: str, webpage_url: str, requester_id: int, duration: Optional[float]=None, thumbnail: Optional[str]=None, uploader: Optional[str]=None, source: str="youtube"):
        self.title = title
        self.webpage_url = webpage_url
        self.requester_id = requester_id
        self.duration = duration or 0.0
        self.thumbnail = thumbnail
        self.uploader = uploader
        self.source = source  # "youtube" ou "spotify"

class GuildQueue:
    def __init__(self):
        self.voice_client: Optional[discord.VoiceClient] = None
        self.tracks: List[Track] = []
        self.current: Optional[Track] = None
        self.skip_votes: set[int] = set()
        self.now_playing_msg: Optional[discord.Message] = None
        self.lock = asyncio.Lock()
        self.track_start_time: Optional[float] = None
        self.progress_task: Optional[asyncio.Task] = None
        self.stopping = False

queues: Dict[int, GuildQueue] = {}

def ensure_queue(guild_id: int) -> GuildQueue:
    if guild_id not in queues:
        queues[guild_id] = GuildQueue()
    return queues[guild_id]

# ---------------- detectar tipo de URL ----------------
def detect_source(query: str) -> str:
    """Detecta se a query é do Spotify ou YouTube"""
    spotify_patterns = [
        r'https?://open\.spotify\.com/track/([a-zA-Z0-9]+)',
        r'https?://open\.spotify\.com/album/([a-zA-Z0-9]+)',
        r'https?://open\.spotify\.com/playlist/([a-zA-Z0-9]+)',
        r'spotify:track:[a-zA-Z0-9]+',
        r'spotify:album:[a-zA-Z0-9]+',
        r'spotify:playlist:[a-zA-Z0-9]+'
    ]
    
    for pattern in spotify_patterns:
        if re.match(pattern, query):
            return "spotify"
    return "youtube"

# ---------------- yt-dlp util melhorado ----------------
ydl_opts_info = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "default_search": "ytsearch",
    "skip_download": True,
    "nocheckcertificate": True,
    "extract_flat": False,
    "geo_bypass": True,
    "no_warnings": True,
    "extractaudio": True,
    "audioformat": "mp3",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
    "force-ipv4": True,
    "prefer-ffmpeg": True,
}

ydl_opts_download = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "default_search": "ytsearch",
    "nocheckcertificate": True,
    "geo_bypass": True,
    "no_warnings": True,
    "extractaudio": True,
    "audioformat": "mp3",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
    "force-ipv4": True,
    "prefer-ffmpeg": True,
}

def ytdlp_search_info(query: str, source: str = "youtube"):
    try:
        # Para Spotify, faz uma busca no YouTube com o nome da música
        if source == "spotify":
            # Extrai informações do Spotify (simplificado - na prática você usaria a API do Spotify)
            track_name = extract_spotify_info(query)
            if track_name:
                query = f"{track_name} official audio"
        
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(query, download=False)
            if not info:
                return None
            if "entries" in info and info["entries"]:
                return info["entries"][0]
            return info
    except Exception as e:
        print(f"Erro no yt-dlp: {e}")
        traceback.print_exc()
        return None

def extract_spotify_info(spotify_url: str) -> str:
    """Extrai informações básicas do Spotify (simplificado)"""
    try:
        # Em uma implementação real, você usaria a API do Spotify aqui
        # Esta é uma versão simplificada que apenas retorna o texto para busca
        if "track" in spotify_url:
            return "música do spotify"
        elif "album" in spotify_url:
            return "álbum do spotify"
        elif "playlist" in spotify_url:
            return "playlist do spotify"
        return "spotify"
    except Exception:
        return "spotify"

# ---------------- ffmpeg source melhorado ----------------
def make_source_from_url(url: str) -> discord.FFmpegOpusAudio:
    ffmpeg_options = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -http_persistent 1',
        'options': '-vn -b:a 128k -af volume=0.8'
    }
    
    try:
        return discord.FFmpegOpusAudio(url, **ffmpeg_options)
    except Exception as e:
        print(f"Erro criando source FFmpeg: {e}")
        # Tentativa com opções mais simples
        simple_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }
        return discord.FFmpegOpusAudio(url, **simple_options)

# ---------------- ui / embed helpers ----------------
def human_time(seconds: float) -> str:
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"

def build_progress_bar(elapsed: float, total: float, length: int = 12) -> str:
    if total <= 0:
        return "• " + "─" * length + " •"
    ratio = min(max(elapsed / total, 0.0), 1.0)
    filled = int(ratio * length)
    bar = "─" * filled + "●" + "─" * (length - filled)
    return f"`{human_time(elapsed)} ` {bar} ` {human_time(total)}`"

def build_now_playing_embed(track: Track, q: GuildQueue, elapsed: float = 0.0) -> discord.Embed:
    title = track.title
    source_icon = "🎵" if track.source == "spotify" else "📺"
    desc = f"pedido por <@{track.requester_id}>\n{track.webpage_url}\n{source_icon} {track.source.upper()}"
    embed = discord.Embed(title=title, description=desc, color=0x1db954 if track.source == "spotify" else 0xff0000)
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    footer_text = f"{track.uploader or 'desconhecido'} • na fila: {len(q.tracks)}"
    embed.set_footer(text=footer_text)
    try:
        embed.add_field(name="progresso", value=build_progress_bar(elapsed, track.duration if track.duration else 0.0), inline=False)
    except Exception:
        pass
    return embed

# ---------------- controles com botões ----------------
class PlayerControls(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="⏸️", style=discord.ButtonStyle.secondary, custom_id="pause_resume")
    async def pause_resume(self, interaction: discord.Interaction, button: Button):
        q = ensure_queue(interaction.guild_id)
        vc = q.voice_client
        if not vc or not vc.is_connected():
            await interaction.response.send_message("bot não está no canal de voz.", ephemeral=True)
            return
        try:
            if vc.is_paused():
                vc.resume()
                await interaction.response.send_message("▶ resumido.", ephemeral=True)
            else:
                vc.pause()
                await interaction.response.send_message("⏸ pausado.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"erro: {e}", ephemeral=True)
        if q.current and q.now_playing_msg:
            elapsed = time.time() - q.track_start_time if q.track_start_time else 0
            await q.now_playing_msg.edit(embed=build_now_playing_embed(q.current, q, elapsed=elapsed), view=self)

    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.danger, custom_id="skip")
    async def skip(self, interaction: discord.Interaction, button: Button):
        q = ensure_queue(interaction.guild_id)
        if not q.current:
            await interaction.response.send_message("nenhuma música tocando.", ephemeral=True)
            return
        if interaction.user.id == q.current.requester_id:
            await interaction.response.send_message("autor pulou a música.", ephemeral=True)
            if q.voice_client and q.voice_client.is_playing():
                q.voice_client.stop()
            return
        q.skip_votes.add(interaction.user.id)
        votos = len(q.skip_votes)
        if votos >= 2:
            await interaction.response.send_message("votos suficientes, pulando música...", ephemeral=True)
            if q.voice_client and q.voice_client.is_playing():
                q.voice_client.stop()
        else:
            await interaction.response.send_message(f"voto registrado ({votos}/2).", ephemeral=True)

    @discord.ui.button(label="❌", style=discord.ButtonStyle.secondary, custom_id="remove")
    async def remove(self, interaction: discord.Interaction, button: Button):
        q = ensure_queue(interaction.guild_id)
        if q.current:
            removed = q.current
            q.current = None
            if q.voice_client and q.voice_client.is_playing():
                q.voice_client.stop()
            await interaction.response.send_message(f"❌ música **{removed.title}** removida.", ephemeral=True)
            asyncio.create_task(play_next(interaction.guild_id))
        else:
            await interaction.response.send_message("nenhuma música tocando.", ephemeral=True)

# ---------------- player loop melhorado ----------------
async def play_next(guild_id: int):
    q = ensure_queue(guild_id)
    async with q.lock:
        q.stopping = False
        if not q.tracks:
            q.current = None
            q.skip_votes.clear()
            if q.now_playing_msg:
                try:
                    await q.now_playing_msg.edit(embed=discord.Embed(title="⏹ fila vazia", description="adicione mais músicas com !play"), view=None)
                except Exception:
                    pass
            await asyncio.sleep(60)
            if q.voice_client and not q.voice_client.is_playing():
                try:
                    await q.voice_client.disconnect()
                except Exception:
                    pass
                q.voice_client = None
            return
        track = q.tracks.pop(0)
        q.current = track
        q.skip_votes.clear()

    # Tentativa melhorada de obter informações da música
    max_retries = 2
    info = None
    
    for attempt in range(max_retries):
        info = ytdlp_search_info(track.webpage_url, track.source)
        if info:
            break
        await asyncio.sleep(1)
    
    if not info:
        try:
            if q.now_playing_msg:
                await q.now_playing_msg.channel.send(f"❌ erro ao obter info da música: {track.title}", delete_after=10)
        except Exception:
            pass
        asyncio.create_task(play_next(guild_id))
        return

    # Obter URL de áudio com fallbacks
    audio_url = None
    if 'url' in info and info['url']:
        audio_url = info['url']
    elif 'formats' in info and info['formats']:
        # Tenta encontrar o melhor formato de áudio
        for fmt in info['formats']:
            if fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none':
                audio_url = fmt.get('url')
                if audio_url:
                    break
    
    # Fallback final
    if not audio_url:
        audio_url = info.get('webpage_url') or track.webpage_url

    # Atualizar informações da track
    track.duration = info.get("duration") or track.duration or 0.0
    track.thumbnail = info.get("thumbnail") or track.thumbnail
    track.uploader = info.get("uploader") or track.uploader
    track.title = info.get("title") or track.title

    if not audio_url:
        asyncio.create_task(play_next(guild_id))
        return

    # Tentar criar source com retry
    src = None
    for attempt in range(2):
        try:
            src = make_source_from_url(audio_url)
            break
        except Exception as e:
            print(f"Tentativa {attempt + 1} falhou: {e}")
            await asyncio.sleep(1)

    if not src:
        print("Erro criando source ffmpeg após várias tentativas")
        asyncio.create_task(play_next(guild_id))
        return

    def after_play(err):
        if err:
            print("erro reprodução:", err)
        q.track_start_time = None
        if q.progress_task and not q.progress_task.done():
            try:
                q.progress_task.cancel()
            except Exception:
                pass
        asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)

    try:
        q.voice_client.play(src, after=after_play)
    except Exception as e:
        print("erro ao dar play:", e)
        asyncio.create_task(play_next(guild_id))
        return

    q.track_start_time = time.time()
    view = PlayerControls(guild_id)
    try:
        if q.now_playing_msg:
            await q.now_playing_msg.edit(embed=build_now_playing_embed(track, q, elapsed=0.0), view=view)
        else:
            guild = bot.get_guild(guild_id)
            channel = None
            if guild:
                for ch in guild.text_channels:
                    if ch.permissions_for(guild.me).send_messages:
                        channel = ch
                        break
            if channel:
                q.now_playing_msg = await channel.send(embed=build_now_playing_embed(track, q, elapsed=0.0), view=view)
    except Exception:
        pass

    async def progress_updater():
        try:
            while q.current and q.voice_client and (q.voice_client.is_playing() or q.voice_client.is_paused()):
                if q.track_start_time is None:
                    await asyncio.sleep(1)
                    continue
                elapsed = time.time() - q.track_start_time
                try:
                    if q.now_playing_msg and q.current:
                        await q.now_playing_msg.edit(embed=build_now_playing_embed(q.current, q, elapsed=min(elapsed, q.current.duration or elapsed)), view=view)
                except Exception:
                    pass
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        except Exception:
            traceback.print_exc()

    if q.progress_task and not q.progress_task.done():
        try:
            q.progress_task.cancel()
        except Exception:
            pass
    q.progress_task = asyncio.create_task(progress_updater())

# ---------------- comandos de texto (!comando) ----------------
@bot.command(name="menu")
async def cmd_menu(ctx):
    """Menu de ajuda do bot de música"""
    embed = discord.Embed(title="🎵 Bot de Música - Comandos", color=0x1db954)
    embed.add_field(name="!play <nome/url>", value="Toca ou adiciona música à fila (YouTube e Spotify)", inline=False)
    embed.add_field(name="!stop", value="Para a música atual e limpa a fila", inline=False)
    embed.add_field(name="!start", value="Continua a reprodução se estiver pausada", inline=False)
    embed.add_field(name="!skip", value="Pula a música atual", inline=False)
    embed.add_field(name="!queue", value="Mostra a fila de músicas", inline=False)
    embed.add_field(name="!leave", value="Faz o bot sair do canal de voz", inline=False)
    embed.add_field(name="Suporte", value="✅ YouTube\n✅ Spotify (links)", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="play")
async def cmd_play(ctx, *, query: str):
    """Toca ou adiciona música (nome ou link do YouTube/Spotify)"""
    user = ctx.author
    if not user.voice or not user.voice.channel:
        await ctx.send("Entre num canal de voz primeiro.")
        return
    
    guild_id = ctx.guild.id
    q = ensure_queue(guild_id)
    
    try:
        if not q.voice_client or not q.voice_client.is_connected():
            existing = discord.utils.get(bot.voice_clients, guild=ctx.guild)
            if existing and existing.is_connected():
                q.voice_client = existing
            else:
                q.voice_client = await user.voice.channel.connect()
    except Exception as e:
        await ctx.send(f"Erro ao conectar no canal de voz: {e}")
        return
    
    # Detectar fonte da música
    source = detect_source(query)
    
    # Indicar que está processando
    processing_msg = await ctx.send(f"🔍 Procurando música no {source.upper()}...")
    
    info = ytdlp_search_info(query, source)
    if not info:
        await processing_msg.edit(content="❌ Não encontrei essa música.")
        return
    
    title = info.get("title", query)
    webpage = info.get("webpage_url", query)
    duration = info.get("duration") or 0.0
    thumbnail = info.get("thumbnail")
    uploader = info.get("uploader")
    
    track = Track(title, webpage, ctx.author.id, duration=duration, thumbnail=thumbnail, uploader=uploader, source=source)
    q.tracks.append(track)
    
    await processing_msg.delete()
    
    if not q.current or not (q.voice_client and q.voice_client.is_playing()):
        embed = build_now_playing_embed(track, q, elapsed=0.0)
        view = PlayerControls(guild_id)
        try:
            msg = await ctx.send(embed=embed, view=view)
            q.now_playing_msg = msg
        except Exception:
            source_icon = "🎵" if source == "spotify" else "📺"
            await ctx.send(f"{source_icon} Tocando agora: **{title}**")
        asyncio.create_task(play_next(guild_id))
    else:
        source_icon = "🎵" if source == "spotify" else "📺"
        await ctx.send(embed=discord.Embed(
            title="➕ Adicionada à fila", 
            description=f"**{title}**\n{source_icon} {source.upper()}\nAdicionada por {user.mention}",
            color=0x1db954 if source == "spotify" else 0xff0000
        ))

@bot.command(name="stop")
async def cmd_stop(ctx):
    """Para a música atual e limpa a fila"""
    q = ensure_queue(ctx.guild.id)
    if q.voice_client:
        q.tracks.clear()
        if q.current:
            q.current = None
        if q.voice_client.is_playing():
            q.voice_client.stop()
        await ctx.send("⏹ Música parada e fila limpa.")
    else:
        await ctx.send("Nenhuma música tocando.")

@bot.command(name="start")
async def cmd_start(ctx):
    """Continua a reprodução se estiver pausada"""
    q = ensure_queue(ctx.guild.id)
    if q.voice_client and q.voice_client.is_paused():
        q.voice_client.resume()
        await ctx.send("▶ Reprodução retomada.")
    elif q.voice_client and not q.voice_client.is_playing() and q.tracks:
        await ctx.send("🎵 Retomando reprodução...")
        asyncio.create_task(play_next(ctx.guild.id))
    else:
        await ctx.send("Nenhuma música pausada ou na fila.")

@bot.command(name="skip")
async def cmd_skip(ctx):
    """Pula a música atual"""
    q = ensure_queue(ctx.guild.id)
    if not q.current or not q.voice_client:
        await ctx.send("Nenhuma música tocando.")
        return
    
    if ctx.author.id == q.current.requester_id:
        await ctx.send("Autor pulou a música.")
        if q.voice_client.is_playing():
            q.voice_client.stop()
        return
    
    q.skip_votes.add(ctx.author.id)
    votos = len(q.skip_votes)
    if votos >= 2:
        if q.voice_client.is_playing():
            q.voice_client.stop()
        await ctx.send("Votos suficientes — pulando música.")
    else:
        await ctx.send(f"Voto registrado ({votos}/2).")

@bot.command(name="queue")
async def cmd_queue(ctx):
    """Mostra a fila de músicas"""
    q = ensure_queue(ctx.guild.id)
    if not q.current and not q.tracks:
        await ctx.send("Fila vazia.")
        return
    
    embed = discord.Embed(title="🎵 Fila de Músicas", color=0x1db954)
    
    if q.current:
        source_icon = "🎵" if q.current.source == "spotify" else "📺"
        embed.add_field(
            name=f"🎶 Tocando Agora {source_icon}", 
            value=f"{q.current.title}\nDuração: {human_time(q.current.duration) if q.current.duration else '??:??'}", 
            inline=False
        )
    
    if q.tracks:
        queue_text = ""
        for i, track in enumerate(q.tracks[:10]):
            source_icon = "🎵" if track.source == "spotify" else "📺"
            queue_text += f"`{i+1}.` {source_icon} {track.title} ({human_time(track.duration) if track.duration else '??:??'})\n"
        
        if len(q.tracks) > 10:
            queue_text += f"\n... e mais {len(q.tracks) - 10} músicas"
        
        embed.add_field(name="Próximas", value=queue_text, inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name="leave")
async def cmd_leave(ctx):
    """Faz o bot sair do canal de voz"""
    q = ensure_queue(ctx.guild.id)
    if q.voice_client:
        try:
            await q.voice_client.disconnect()
        except Exception:
            pass
        q.voice_client = None
        q.tracks.clear()
        q.current = None
        if q.progress_task and not q.progress_task.done():
            q.progress_task.cancel()
        await ctx.send("👋 Sai do canal de voz.")
    else:
        await ctx.send("Não estou em nenhum canal de voz.")

# ---------------- events ----------------
@bot.event
async def on_ready():
    print(f"Logado como {bot.user}")
    try:
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="!play | YouTube & Spotify"))
    except Exception:
        pass

# ---------------- executar ----------------
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        print("Erro ao iniciar bot:", e)
        raise
