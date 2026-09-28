# 🤖 Bot STF

Bot Discord de la communauté STF : **événements**, **inhouses League of Legends** et
**pronostics esport**. Écrit en Python (discord.py), données persistées en SQLite.

- 📅 **Événements** : création, inscriptions en un clic (avec liste d'attente), annonces, rappels automatiques.
- ⚔️ **Inhouses LoL** (Faille / ARAM / Arena) : liaison du compte Riot, choix des rôles, rangs,
  équipes équilibrées générées automatiquement, liens op.gg multisearch.
- 🎯 **Pronostics** : suivi des compétitions LoL Esports (LEC, LCK, Worlds…), paris en points fictifs,
  bonus quotidien, statistiques et classements filtrables publiés automatiquement.

Tout se fait par **commandes slash** et **boutons**. Les réponses personnelles sont éphémères
(visibles uniquement par toi) pour ne pas encombrer les salons. Tape **`/aide`** sur le serveur
pour un guide interactif.

---

## 🚀 Installation

### 1. Créer l'application Discord
1. Va sur <https://discord.com/developers/applications> → **New Application**.
2. Onglet **Bot** → **Reset Token** → copie le token (tu en auras besoin ci-dessous).
   Aucun *Privileged Gateway Intent* n'est nécessaire.
3. Onglet **OAuth2 → URL Generator** : coche les scopes `bot` et `applications.commands`, puis les
   permissions **View Channels**, **Send Messages**, **Embed Links**, **Read Message History**,
   **Mention Everyone** (pour mentionner un rôle dans les annonces). Ouvre l'URL générée pour inviter le bot.

### 2. Clé API Riot (recommandé)
Sur <https://developer.riotgames.com>, récupère une clé (la *Development Key* expire toutes les
24 h ; demande une *Personal API Key* pour un usage permanent). Sans clé, la liaison des comptes
fonctionne en mode dégradé : pas de vérification du Riot ID ni de rang.

### 3. Lancer le bot

**Avec Python (3.11+) — Windows (PowerShell)**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1         # si refusé : Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
copy .env.example .env
notepad .env                       # remplis DISCORD_TOKEN, GUILD_ID, RIOT_API_KEY puis enregistre
python -m bot
```

**Avec Python (3.11+) — Linux / macOS**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # puis remplis DISCORD_TOKEN, GUILD_ID, RIOT_API_KEY
python -m bot
```

> ⚠️ Si tu télécharges le ZIP depuis GitHub, vérifie que tu prends la branche qui contient tout
> le bot (le dossier `bot/features/` doit contenir `events`, `inhouse`, `predictions`…).

**Avec Docker**
```bash
cp .env.example .env               # puis remplis-le
docker compose up -d --build
docker compose logs -f
```

**En service Linux** : voir `deploy/stf-bot.service` (redémarrage automatique).

> 💡 Renseigne `GUILD_ID` (clic droit sur le serveur → *Copier l'identifiant*, mode développeur
> activé) : les commandes apparaissent instantanément sur ce serveur.

### 4. Premiers réglages sur le serveur
```
/config voir                     → récapitulatif + ce qu'il reste à configurer
/config salon-annonces #annonces → annonces, rappels et équipes d'inhouse
/config salon-pronos #pronos     → matchs du jour et classements
/config role-organisateur @Orga  → (optionnel) rôle autorisé à gérer le bot
/pronos-admin competitions       → cocher les compétitions à suivre
```

---

## 📖 Commandes

🔒 = réservé aux organisateurs (permission *Gérer le serveur* ou rôle organisateur).

### 📅 Événements — `/evenement`
| Commande | Description |
|---|---|
| `creer` 🔒 | Créer un événement (`date` : `28/09 21h`, `demain 20h30`, `samedi 18h`…) et le publier |
| `modifier` 🔒 | Changer titre, date, description ou nombre de places |
| `supprimer` 🔒 | Annuler un événement (avec confirmation, inscrits prévenus) |
| `inscriptions` 🔒 | Ouvrir / fermer les inscriptions |
| `annoncer` 🔒 | Annonce manuelle (message, mention d'un rôle, republication) |
| `retirer` 🔒 | Retirer un participant |
| `terminer` 🔒 | Clôturer un événement |
| `liste` | Événements à venir |
| `voir` / `participants` | Détail d'un événement / liste des inscrits |
| `rejoindre` / `quitter` | S'inscrire / se désinscrire (ou via les boutons de l'annonce) |

Rappels automatiques aux inscrits (par défaut 1 jour, 1 h et 15 min avant ; `/config rappels`).
Quand l'événement est complet, les nouveaux inscrits passent en **liste d'attente** et sont
promus automatiquement (et prévenus) si une place se libère.

### ⚔️ Inhouses LoL — `/inhouse`
| Commande | Description |
|---|---|
| `creer` 🔒 | Nouvelle session : mode (Faille / ARAM / Arena), date, places |
| `modifier` 🔒 | Modifier la session (mode, date, places…) |
| `inscriptions` 🔒 | Ouvrir / fermer les inscriptions |
| `inscrits` | Détail des inscrits : Riot ID, rang, rôles, couverture des rôles, MultiGG |
| `equipes-generer` 🔒 | Générer des équipes équilibrées (aperçu → Publier / Regénérer / Annuler) |
| `equipes-echanger` 🔒 | Échanger deux joueurs |
| `equipes-deplacer` 🔒 | Déplacer un joueur (équipe, rôle) |
| `equipes-publier` 🔒 | Publier / republier les équipes |
| `annoncer` 🔒 | Annonce manuelle |

Pour s'inscrire à un inhouse, un joueur doit avoir **lié son compte LoL** (et, en Faille,
**choisi ses rôles**) : les boutons *🔗 Lier mon compte LoL* et *🎮 Choisir mes rôles* sont
directement sous l'annonce.

**Génération des équipes** : le bot attribue les rôles en privilégiant le rôle principal de
chacun, puis équilibre le niveau (rang classé) entre les deux équipes de chaque partie. S'il y a
trop de joueurs pour faire des parties complètes, les derniers inscrits sont remplaçants.

### 🎮 Compte LoL — `/compte`
`lier` (Riot ID `Pseudo#TAG`) · `roles` · `profil [membre]` · `actualiser` · `delier`

### 🎯 Pronostics — `/pronos` et `/pronos-admin`
| Commande | Description |
|---|---|
| `/pronos matchs` | Matchs à venir, avec boutons pour parier |
| `/pronos parier` / `parier-score` | Parier sur le vainqueur / le score exact (Bo3, Bo5) |
| `/pronos mes-paris` · `solde` · `stats` | Tes paris, ton solde, tes statistiques |
| `/pronos classement` | Classement par période, compétition, tournoi, type de pari |
| `/pronos classement-general` · `regles` | Classement par solde · règles du jeu |
| `/pronos-admin competitions` 🔒 | Choisir les compétitions suivies |
| `/pronos-admin synchroniser` 🔒 | Forcer la récupération des matchs |
| `/pronos-admin competition-creer` · `match-ajouter` · `resultat` · `annuler-match` 🔒 | Gestion manuelle |
| `/pronos-admin classement-auto` 🔒 | Publication automatique de classements (quotidienne / hebdo) |
| `/pronos-admin points` 🔒 | Ajuster les points d'un membre |

**Règles par défaut** (modifiables avec `/config bareme-pronos`) : 500 points de départ,
+100 points par jour, cote ×2 pour le vainqueur, ×3,5 pour le score exact. Les matchs des
compétitions suivies sont synchronisés toutes les 10 minutes, publiés chaque jour à 10 h dans le
salon pronos, et les paris sont réglés automatiquement à la fin du match.

### ⚙️ Configuration — `/config` 🔒
`voir` · `salon-annonces` · `salon-pronos` · `role-organisateur` · `rappels` · `bareme-pronos`

---

## 🛠️ Exploitation

- **Logs** : console + `logs/bot.log` (rotation 5 × 5 Mo). Niveau via `LOG_LEVEL`.
- **Données** : `data/bot.db` (SQLite). Sauvegarde : copier ce fichier bot arrêté, ou
  `sqlite3 data/bot.db ".backup backup.db"` à chaud.
- **Redémarrage** : tout est persistant (événements, inscriptions, équipes, paris, rappels déjà
  envoyés) et les boutons des anciens messages continuent de fonctionner.
- **Mises à jour du schéma** : appliquées automatiquement au démarrage (`bot/db/migrations.py`).

## 🧪 Développement

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

L'organisation du code (un fichier par fonctionnalité, contrats entre modules, conventions) est
décrite dans **[ARCHITECTURE.md](ARCHITECTURE.md)**.
