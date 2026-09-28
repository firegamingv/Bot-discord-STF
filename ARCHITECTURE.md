# Architecture du bot STF

> Document de référence pour quiconque modifie le projet. Règle d'or : **un fichier = une
> fonctionnalité**. On préfère dix petits fichiers lisibles à un gros fichier fourre-tout.

## Arborescence

```
bot/
├── __main__.py              # python -m bot
├── config.py                # lecture du .env
├── logging_setup.py         # logs console + fichier tournant (logs/bot.log)
├── core/
│   ├── bot.py               # STFBot : services partagés + chargement des fonctionnalités
│   ├── checks.py            # organizer_only(), ensure_organizer()
│   ├── errors.py            # UserFacingError & co (message affiché à l'utilisateur)
│   └── error_reporting.py   # report_error(), interaction_guard(), BaseView, BaseModal
├── db/
│   ├── database.py          # wrapper aiosqlite + migrations automatiques
│   └── migrations.py        # schéma SQL versionné (ajouter à la fin, ne jamais modifier)
├── repositories/            # accès aux données, UN fichier par table/domaine, zéro Discord
├── services/                # API externes & algorithmes purs (testables sans Discord)
├── utils/                   # temps (parsing FR, timestamps Discord), embeds (charte)
└── features/                # UN paquet par domaine, UN fichier par fonctionnalité
    ├── admin/  help/  account/  events/  inhouse/  predictions/
```

## Conventions

### Fonctionnalités (`bot/features/<domaine>/`)
- Chaque paquet est une extension discord.py : son `__init__.py` contient
  `async def setup(bot: STFBot)` qui importe les fichiers de commandes, ajoute le groupe
  de commandes à `bot.tree`, enregistre les boutons persistants (`bot.add_dynamic_items`)
  et ajoute les Cogs de tâches de fond.
- Le **groupe de commandes** slash est défini une seule fois dans `group.py`
  (`app_commands.Group(name=..., description=..., guild_only=True)`).
  Chaque fichier de fonctionnalité y attache ses sous-commandes :
  ```python
  from bot.features.events.group import event_group

  @event_group.command(name="creer", description="Créer un nouvel événement")
  @organizer_only()
  async def create_event(interaction: discord.Interaction, ...): ...
  ```
- Les services partagés s'obtiennent via `bot: STFBot = interaction.client`
  (`bot.db`, `bot.settings`, `bot.riot`, `bot.esports`, `bot.config`, `bot.web`).
  Les dépôts s'instancient à la demande : `EventRepository(bot.db)`.
- Tâches de fond : un Cog par tâche, dans son propre fichier, avec `discord.ext.tasks.loop`,
  `before_loop` qui attend `bot.wait_until_ready()`, et chaque itération protégée par un
  `try/except Exception: log.exception(...)` pour que la boucle ne meure jamais.

### Expérience utilisateur (« sympa à utiliser »)
- Commandes et textes **en français**, noms de commandes sans accents (`creer`, `modifier`).
- Réponses **éphémères** par défaut ; messages publics uniquement pour ce qui est partagé
  (annonces, rappels, équipes, classements, matchs du jour).
- Toujours des **embeds** colorés (`bot.utils.embeds`), des emojis parlants et des
  timestamps Discord (`discord_full(dt)` → date + « dans 2 heures »).
- **Autocomplétion** pour choisir un événement / match / compétition (jamais demander un ID brut).
- **Boutons persistants** (`discord.ui.DynamicItem`, survivent aux redémarrages) sur les
  annonces : S'inscrire / Se désinscrire / Voir les inscrits / …
- Confirmation (boutons Oui/Non) avant toute action destructrice.
- Messages d'erreur clairs qui disent **quoi faire** : lever `UserFacingError("…")`.

### Erreurs & logs
- Erreurs attendues : `raise UserFacingError("message pour l'utilisateur")`.
- Commandes slash : gérées automatiquement par `STFTree.on_error`.
- Boutons/menus/modales : hériter de `BaseView` / `BaseModal`, ou pour un `DynamicItem`
  envelopper le callback dans `async with interaction_guard(interaction): ...`.
- `log = logging.getLogger(__name__)` dans chaque module ; logguer les actions
  importantes en INFO (création/suppression d'événement, génération d'équipes, règlement de paris).

### Données
- Dates en UTC dans la base (`to_db` / `from_db`), saisie utilisateur en heure locale via
  `parse_user_datetime(texte, bot.config.timezone)`.
- Toute évolution du schéma = nouvelle entrée en fin de `bot/db/migrations.py`.

## Contrats entre modules

| Fournisseur | API publique |
|---|---|
| `features/events/kinds.py` | `EventKind`, `register_kind()`, `get_kind()` — hooks pour les types d'événements |
| `features/events/announcement.py` | `build_event_embed(bot, event)`, `build_event_view(bot, event)`, `publish_event_message(bot, event, channel)`, `refresh_event_message(bot, event_id)` |
| `features/events/registration_service.py` | `join_event(bot, interaction, event_id)`, `leave_event(bot, interaction, event_id)` |
| `features/account/linking_service.py` | `link_riot_account(bot, user, game_name, tag_line)` |
| `features/account/role_picker.py` | `send_role_picker(interaction, *, after_save=None)` |
| `features/account/rank_refresh.py` | `refresh_ranks(bot, discord_ids, max_age=timedelta(hours=6))` |
| `services/riot_api.py` | `RiotClient` (`enabled`, `get_account_by_riot_id`, `get_account_by_puuid`, `get_solo_rank`) |
| `services/lolesports_api.py` | `LolEsportsClient` (`get_leagues`, `get_schedule`) |

### Identifiants des boutons persistants
| Préfixe | Domaine |
|---|---|
| `evt:join:<event_id>`, `evt:leave:<event_id>`, `evt:list:<event_id>` | événements |
| `acc:roles`, `acc:link` | compte |
| `ih:teams:<event_id>` | inhouse |
| `bet:<match_id>:<choice>` | pronostics |
