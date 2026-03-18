import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Mini serveur HTTP pour Render ──────────────────────────────────────────
class KeepAlive(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot EVA is running")
    def log_message(self, format, *args):
        pass

def run_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), KeepAlive)
    server.serve_forever()

Thread(target=run_server, daemon=True).start()
# ───────────────────────────────────────────────────────────────────────────

TOKEN = os.getenv("TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

PSEUDOS_FILE = "pseudos.json"

def load_pseudos():
    if os.path.exists(PSEUDOS_FILE):
        with open(PSEUDOS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_pseudos(pseudos):
    with open(PSEUDOS_FILE, "w") as f:
        json.dump(pseudos, f)

pseudos_eva = load_pseudos()
events = {}

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
        await interaction.response.defer()

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Bot connecté : {bot.user}")

@bot.tree.command(name="match", description="Créer un event 4v4 EVA")
async def match(interaction: discord.Interaction, titre: str, date: str):
    event_id = str(interaction.id)
    events[event_id] = {
        "titre": titre,
        "date": date,
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

bot.run(TOKEN)
