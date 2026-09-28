"""Migrations SQL, appliquées dans l'ordre. Ne jamais modifier une migration existante :
ajouter une nouvelle entrée à la fin de la liste.

Conventions :
- IDs Discord en INTEGER.
- Dates en TEXT ISO-8601 UTC.
- Booléens en INTEGER (0/1).
"""

MIGRATIONS: list[str] = [
    # ------------------------------------------------------------------ v1 : schéma initial
    """
    -- Paramètres par serveur -------------------------------------------------------------
    CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id                INTEGER PRIMARY KEY,
        announce_channel_id     INTEGER,               -- salon des annonces d'événements
        predictions_channel_id  INTEGER,               -- salon des pronostics
        organizer_role_id       INTEGER,               -- rôle autorisé à gérer les événements
        reminder_offsets        TEXT NOT NULL DEFAULT '[1440, 60, 15]',  -- minutes avant le début (JSON)
        daily_points            INTEGER NOT NULL DEFAULT 100,
        starting_points         INTEGER NOT NULL DEFAULT 500,
        odds_winner             REAL NOT NULL DEFAULT 2.0,
        odds_exact_score        REAL NOT NULL DEFAULT 3.5
    );

    -- Utilisateurs ------------------------------------------------------------------------
    CREATE TABLE IF NOT EXISTS users (
        discord_id    INTEGER PRIMARY KEY,
        display_name  TEXT,
        created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
    );

    CREATE TABLE IF NOT EXISTS riot_accounts (
        discord_id       INTEGER PRIMARY KEY REFERENCES users(discord_id) ON DELETE CASCADE,
        puuid            TEXT UNIQUE,                  -- identifiant Riot pérenne (NULL si non vérifié)
        game_name        TEXT NOT NULL,
        tag_line         TEXT NOT NULL,
        platform         TEXT,
        rank_tier        TEXT,                         -- ex. GOLD (NULL = non classé / inconnu)
        rank_division    TEXT,                         -- ex. II
        league_points    INTEGER,
        rank_updated_at  TEXT,
        linked_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
    );

    -- Rôles de jeu choisis (priorité 1 = rôle principal)
    CREATE TABLE IF NOT EXISTS player_roles (
        discord_id  INTEGER NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
        game        TEXT NOT NULL DEFAULT 'lol',
        role        TEXT NOT NULL,                     -- top | jungle | mid | adc | support | fill
        priority    INTEGER NOT NULL,
        PRIMARY KEY (discord_id, game, role)
    );

    -- Événements génériques ---------------------------------------------------------------
    CREATE TABLE IF NOT EXISTS events (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id           INTEGER NOT NULL,
        type               TEXT NOT NULL DEFAULT 'generic',   -- generic | inhouse | ...
        title              TEXT NOT NULL,
        description        TEXT,
        starts_at          TEXT NOT NULL,
        max_participants   INTEGER,                           -- NULL = illimité
        registration_open  INTEGER NOT NULL DEFAULT 1,
        status             TEXT NOT NULL DEFAULT 'scheduled', -- scheduled | ongoing | finished | cancelled
        channel_id         INTEGER,                           -- message d'annonce (mis à jour en direct)
        message_id         INTEGER,
        created_by         INTEGER NOT NULL,
        created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
        updated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
    );
    CREATE INDEX IF NOT EXISTS idx_events_guild_start ON events(guild_id, starts_at);

    CREATE TABLE IF NOT EXISTS event_participants (
        event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        discord_id  INTEGER NOT NULL,
        status      TEXT NOT NULL DEFAULT 'registered',       -- registered | waitlist
        joined_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
        PRIMARY KEY (event_id, discord_id)
    );

    CREATE TABLE IF NOT EXISTS event_reminders (
        event_id        INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        offset_minutes  INTEGER NOT NULL,
        sent_at         TEXT NOT NULL,
        PRIMARY KEY (event_id, offset_minutes)
    );

    -- Inhouses League of Legends (extension d'un événement) ------------------------------
    CREATE TABLE IF NOT EXISTS inhouse_sessions (
        event_id          INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
        game_mode         TEXT NOT NULL,                      -- sr | aram | arena
        teams_channel_id  INTEGER,
        teams_message_id  INTEGER,
        teams_generated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS inhouse_team_members (
        event_id       INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        team_index     INTEGER NOT NULL,                      -- 0, 1, 2 ...
        discord_id     INTEGER NOT NULL,
        assigned_role  TEXT,                                  -- rôle attribué (Faille)
        PRIMARY KEY (event_id, discord_id)
    );

    -- Prédictions esport ------------------------------------------------------------------
    CREATE TABLE IF NOT EXISTS competitions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id     INTEGER NOT NULL,
        source       TEXT NOT NULL DEFAULT 'lolesports',       -- lolesports | manual
        external_id  TEXT NOT NULL,
        name         TEXT NOT NULL,
        slug         TEXT,
        image_url    TEXT,
        followed     INTEGER NOT NULL DEFAULT 1,
        UNIQUE (guild_id, source, external_id)
    );

    CREATE TABLE IF NOT EXISTS matches (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id         INTEGER NOT NULL,
        competition_id   INTEGER NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
        external_id      TEXT NOT NULL,
        tournament_name  TEXT,                                 -- "événement" (ex. Worlds 2026, Split 2)
        block_name       TEXT,                                 -- ex. "Semaine 3", "Demi-finale"
        team1_name       TEXT NOT NULL,
        team1_code       TEXT,
        team2_name       TEXT NOT NULL,
        team2_code       TEXT,
        best_of          INTEGER NOT NULL DEFAULT 1,
        starts_at        TEXT NOT NULL,
        state            TEXT NOT NULL DEFAULT 'upcoming',     -- upcoming | live | completed | cancelled
        team1_score      INTEGER,
        team2_score      INTEGER,
        winner           INTEGER,                              -- 1 | 2 | NULL
        settled          INTEGER NOT NULL DEFAULT 0,
        UNIQUE (guild_id, external_id)
    );
    CREATE INDEX IF NOT EXISTS idx_matches_guild_start ON matches(guild_id, starts_at);

    CREATE TABLE IF NOT EXISTS predictions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id    INTEGER NOT NULL,
        match_id    INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
        discord_id  INTEGER NOT NULL,
        bet_type    TEXT NOT NULL,                             -- winner | exact_score
        choice      TEXT NOT NULL,                             -- "1" / "2" ou score "2-1"
        stake       INTEGER NOT NULL,
        odds        REAL NOT NULL,
        status      TEXT NOT NULL DEFAULT 'pending',           -- pending | won | lost | refunded
        payout      INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
        settled_at  TEXT,
        UNIQUE (match_id, discord_id, bet_type)
    );

    -- Journal de points : le solde = somme des montants. Permet des classements
    -- segmentés (période, compétition, événement, type de pari).
    CREATE TABLE IF NOT EXISTS point_transactions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id        INTEGER NOT NULL,
        discord_id      INTEGER NOT NULL,
        amount          INTEGER NOT NULL,
        kind            TEXT NOT NULL,          -- starting | daily | stake | payout | refund | admin
        prediction_id   INTEGER REFERENCES predictions(id) ON DELETE SET NULL,
        competition_id  INTEGER,
        tournament_name TEXT,
        bet_type        TEXT,
        created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
    );
    CREATE INDEX IF NOT EXISTS idx_points_user ON point_transactions(guild_id, discord_id);
    CREATE INDEX IF NOT EXISTS idx_points_created ON point_transactions(guild_id, created_at);

    -- Classements affichés automatiquement
    CREATE TABLE IF NOT EXISTS leaderboard_schedules (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id        INTEGER NOT NULL,
        channel_id      INTEGER NOT NULL,
        period          TEXT NOT NULL DEFAULT 'all',     -- day | week | month | all
        competition_id  INTEGER REFERENCES competitions(id) ON DELETE CASCADE,
        tournament_name TEXT,
        bet_type        TEXT,
        frequency       TEXT NOT NULL DEFAULT 'weekly',  -- daily | weekly
        weekday         INTEGER NOT NULL DEFAULT 0,      -- 0 = lundi (si weekly)
        hour            INTEGER NOT NULL DEFAULT 20,     -- heure locale
        last_posted_at  TEXT
    );
    """,
]
