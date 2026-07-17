import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import sys
import queue
import base64
import threading
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask

# ── Dossier de base (utile pour le mode local) ─────────────────────────────
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "config.txt")
PSEUDOS_FILE = os.path.join(BASE_DIR, "pseudos.json")
EVENTS_FILE = os.path.join(BASE_DIR, "events.json")

# ── Token Discord : variable d'environnement (Render) ou config.txt (local) ─
def load_token_from_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    with open(CONFIG_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("TOKEN="):
                token = line.split("=", 1)[1].strip()
                if token:
                    return token
    return None

TOKEN = os.environ.get("DISCORD_TOKEN") or load_token_from_config()

if not TOKEN:
    print("=" * 50)
    print("ERREUR : aucun token Discord trouvé.")
    print("Sur Render : ajoute la variable d'environnement DISCORD_TOKEN.")
    print("En local   : mets TOKEN=ton_token dans config.txt")
    print("=" * 50)
    sys.exit(1)

# ── Stockage : GitHub (sur Render) ou fichiers locaux (en local) ───────────
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")            # ex : "Gaurage/Bot-EVA-data"
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
USE_GITHUB = bool(GITHUB_TOKEN and GITHUB_REPO)

def _gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

def _gh_url(filename):
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"

def github_load(filename):
    try:
        r = requests.get(_gh_url(filename), headers=_gh_headers(),
                         params={"ref": GITHUB_BRANCH}, timeout=15)
        if r.status_code == 200:
            decoded = base64.b64decode(r.json()["content"]).decode("utf-8")
            return json.loads(decoded) if decoded.strip() else {}
        if r.status_code == 404:
            return {}
        print(f"⚠️ GitHub load {filename} : {r.status_code} {r.text}")
    except Exception as e:
        print(f"⚠️ Erreur github_load {filename} : {e}")
    return {}

def _github_write(filename, json_str):
    try:
        sha = None
        r = requests.get(_gh_url(filename), headers=_gh_headers(),
                         params={"ref": GITHUB_BRANCH}, timeout=15)
        if r.status_code == 200:
            sha = r.json()["sha"]
        payload = {
            "message": f"update {filename}",
            "content": base64.b64encode(json_str.encode("utf-8")).decode("utf-8"),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha
        r = requests.put(_gh_url(filename), headers=_gh_headers(),
                         json=payload, timeout=15)
        if r.status_code not in (200, 201):
            print(f"⚠️ GitHub save {filename} : {r.status_code} {r.text}")
    except Exception as e:
        print(f"⚠️ Erreur github_write {filename} : {e}")

# File d'attente : écrit sur GitHub dans l'ordre, sans bloquer le bot
_save_queue = queue.Queue()

def _save_worker():
    while True:
        filename, json_str = _save_queue.get()
        _github_write(filename, json_str)
        _save_queue.task_done()

if USE_GITHUB:
    threading.Thread(target=_save_worker, daemon=True).start()

def _save(filename, local_path, data):
    if USE_GITHUB:
        _save_queue.put((filename, json.dumps(data, ensure_ascii=False)))
    else:
        with open(local_path, "w") as f:
            json.dump(data, f)

def _load(filename, local_path):
    if USE_GITHUB:
        return github_load(filename)
    if os.path.exists(local_path):
        with open(local_path, "r") as f:
            return json.load(f)
    return {}

# ── Bot ────────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

def load_pseudos():
    return _load("pseudos.json", PSEUDOS_FILE)

def save_pseudos(pseudos):
    _save("pseudos.json", PSEUDOS_FILE, pseudos)

def load_events():
    return _load("events.json", EVENTS_FILE)

def save_events():
    _save("events.json", EVENTS_FILE, events)

def purge_old_events():
    limite = datetime.now(timezone.utc) - timedelta(days=30)
    to_delete = [
        eid for eid, e in events.items()
        if datetime.fromisoformat(e.get("created_at", "2000-01-01T00:00:00+00:00")) < limite
    ]
    for eid in to_delete:
        del events[eid]
    if to_delete:
        print(f"🗑️ {len(to_delete)} event(s) purgé(s) (> 30 jours)")
        save_events()

pseudos_eva = load_pseudos()
events = load_events()

POSITIONS = ["⚡ Rusher", "🛡️ Teneur de ligne"]

def build_embed(titre, date, equipe1, equipe2, absents, annules):
    embed = discord.Embed(
        title=f"⚔️ {titre}",
        description=(
            f"📅 {date}\n\n"
            f"**Pour réserver ta session :**\n"
            f"https://app.eva.gg/fr-FR/booking?locationId=52&gameIds=1&seatCount=1&isCompetitiveMode=true"
        ),
        color=0x9B59B6
    )

    def format_team(players):
        lines = [f"• {p['pseudo']} — {p['position']}" for p in players]
        while len(lines) < 4:
            lines.append("_(vide)_")
        return "\n".join(lines)

    embed.add_field(name="🔵 Équipe 1", value=format_team(equipe1), inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="🔴 Équipe 2", value=format_team(equipe2), inline=True)

    if absents:
        embed.add_field(
            name="😴 Pas dispo cette fois",
            value="\n".join([f"• {a[1]}" for a in absents]),
            inline=False
        )

    if annules:
        embed.add_field(
            name="❌ Ne vient plus",
            value="\n".join([f"• {a[1]}" for a in annules]),
            inline=False
        )

    embed.set_footer(text="Clique sur un bouton pour t'inscrire")
    return embed

def retirer_joueur(event, user_id):
    event["equipe1"] = [p for p in event["equipe1"] if p["id"] != user_id]
    event["equipe2"] = [p for p in event["equipe2"] if p["id"] != user_id]
    if user_id in event["absents_ids"]:
        event["absents_ids"].remove(user_id)
        event["absents"] = [a for a in event["absents"] if a[0] != user_id]
    if user_id in event["annules_ids"]:
        event["annules_ids"].remove(user_id)
        event["annules"] = [a for a in event["annules"] if a[0] != user_id]

class PseudoModal(discord.ui.Modal, title="Ton pseudo EVA Arena"):
    pseudo = discord.ui.TextInput(
        label="Pseudo EVA",
        placeholder="Entre ton pseudo EVA Arena...",
        max_length=32
    )

    def __init__(self, event_id, equipe, position, default_pseudo):
        super().__init__()
        self.event_id = event_id
        self.equipe = equipe
        self.position = position
        self.pseudo.default = default_pseudo

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        pseudo = self.pseudo.value
        pseudos_eva[user_id] = pseudo
        save_pseudos(pseudos_eva)
        await inscrire_joueur(interaction, self.event_id, pseudo, self.position, self.equipe)

class PseudoModalAbsent(discord.ui.Modal, title="Ton pseudo EVA Arena"):
    pseudo = discord.ui.TextInput(
        label="Pseudo EVA",
        placeholder="Entre ton pseudo EVA Arena...",
        max_length=32
    )

    def __init__(self, event_id, default_pseudo):
        super().__init__()
        self.event_id = event_id
        self.pseudo.default = default_pseudo

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        pseudo = self.pseudo.value
        pseudos_eva[user_id] = pseudo
        save_pseudos(pseudos_eva)
        await marquer_absent(interaction, self.event_id, pseudo)

class ChoixView(discord.ui.View):
    def __init__(self, event_id):
        super().__init__(timeout=60)
        self.event_id = event_id
        for equipe in ["🔵 Équipe 1", "🔴 Équipe 2"]:
            for pos in POSITIONS:
                self.add_item(ChoixButton(equipe, pos, event_id))

class ChoixButton(discord.ui.Button):
    def __init__(self, equipe, position, event_id):
        style = discord.ButtonStyle.primary if "🔵" in equipe else discord.ButtonStyle.danger
        super().__init__(label=f"{equipe} — {position}", style=style)
        self.equipe = equipe
        self.position = position
        self.event_id = event_id

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        if user_id in pseudos_eva:
            await inscrire_joueur(interaction, self.event_id, pseudos_eva[user_id], self.position, self.equipe)
        else:
            default = interaction.user.display_name
            modal = PseudoModal(self.event_id, self.equipe, self.position, default)
            await interaction.response.send_modal(modal)

async def inscrire_joueur(interaction, event_id, pseudo, position, equipe):
    event = events[event_id]
    user_id = str(interaction.user.id)

    retirer_joueur(event, user_id)

    joueur = {"id": user_id, "pseudo": pseudo, "position": position}
    cible = event["equipe1"] if "1" in equipe else event["equipe2"]

    if len(cible) < 4:
        cible.append(joueur)
    else:
        await interaction.response.send_message("Cette équipe est complète !", ephemeral=True)
        return

    embed = build_embed(event["titre"], event["date"], event["equipe1"], event["equipe2"], event["absents"], event["annules"])
    msg = await interaction.channel.fetch_message(event["message_id"])
    await msg.edit(embed=embed)
    save_events()
    await interaction.response.defer()

async def marquer_absent(interaction, event_id, pseudo):
    event = events[event_id]
    user_id = str(interaction.user.id)

    retirer_joueur(event, user_id)

    event["absents_ids"].append(user_id)
    event["absents"].append((user_id, pseudo))

    embed = build_embed(event["titre"], event["date"], event["equipe1"], event["equipe2"], event["absents"], event["annules"])
    msg = await interaction.channel.fetch_message(event["message_id"])
    await msg.edit(embed=embed)
    save_events()
    await interaction.response.defer()

class MatchView(discord.ui.View):
    def __init__(self, event_id):
        super().__init__(timeout=None)
        self.event_id = event_id

    @discord.ui.button(label="✅ Je viens", style=discord.ButtonStyle.success, custom_id="join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ChoixView(self.event_id)
        await interaction.response.send_message("Choisis ton équipe et ta position :", view=view, ephemeral=True)

    @discord.ui.button(label="😴 Pas dispo cette fois", style=discord.ButtonStyle.secondary, custom_id="nodispo")
    async def nodispo(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        if user_id in pseudos_eva:
            await marquer_absent(interaction, self.event_id, pseudos_eva[user_id])
        else:
            modal = PseudoModalAbsent(self.event_id, interaction.user.display_name)
            await interaction.response.send_modal(modal)

    @discord.ui.button(label="❌ Je ne viens plus", style=discord.ButtonStyle.danger, custom_id="leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        event = events[self.event_id]
        user_id = str(interaction.user.id)
        pseudo = pseudos_eva.get(user_id, interaction.user.display_name)
        retirer_joueur(event, user_id)
        if user_id not in event["annules_ids"]:
            event["annules_ids"].append(user_id)
            event["annules"].append((user_id, pseudo))
        embed = build_embed(event["titre"], event["date"], event["equipe1"], event["equipe2"], event["absents"], event["annules"])
        await interaction.message.edit(embed=embed)
        save_events()
        await interaction.response.defer()

@bot.event
async def on_ready():
    await bot.tree.sync()
    purge_old_events()
    for event_id, event in events.items():
        bot.add_view(MatchView(event_id))
    print(f"✅ Bot EVA connecté : {bot.user}")
    print(f"💾 Stockage : {'GitHub (' + GITHUB_REPO + ')' if USE_GITHUB else 'fichiers locaux'}")
    print(f"📋 Pseudos chargés : {len(pseudos_eva)} joueur(s)")
    print(f"📅 Events actifs : {len(events)}")

@bot.tree.command(name="match", description="Créer un event 4v4 EVA")
async def match(interaction: discord.Interaction, titre: str, date: str):
    event_id = str(interaction.id)
    events[event_id] = {
        "titre": titre,
        "date": date,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "equipe1": [],
        "equipe2": [],
        "absents": [],
        "absents_ids": [],
        "annules": [],
        "annules_ids": [],
        "message_id": None
    }
    embed = build_embed(titre, date, [], [], [], [])
    view = MatchView(event_id)
    await interaction.response.send_message(embed=embed, view=view)
    msg = await interaction.original_response()
    events[event_id]["message_id"] = msg.id
    save_events()

# ── Mini serveur web : garde le service éveillé sur Render ─────────────────
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot EVA en ligne ✅"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    threading.Thread(target=run_web, daemon=True).start()

keep_alive()
bot.run(TOKEN)
