"""Constitution d'équipes équilibrées pour les inhouses (algorithme pur, sans Discord).

Modes gérés
-----------
- ``sr``    (Faille de l'invocateur) : 5 contre 5, chaque joueur reçoit un rôle
  (top / jungle / mid / adc / support). On maximise la satisfaction des rôles
  (principal > secondaire > autre rôle choisi > « fill » > rôle non demandé) ET on
  équilibre le niveau moyen des deux équipes de chaque partie.
- ``aram`` : 5 contre 5 sans rôles, on équilibre uniquement les rangs.
- ``arena`` : duos ; jusqu'à 8 duos par partie, on rend les duos homogènes entre eux
  (un joueur fort avec un plus faible).

Principe
--------
Chaque joueur retenu occupe un « emplacement » (équipe, rôle). Le coût d'une répartition :

    coût = POIDS_RÔLE × Σ pénalités de rôle
         + POIDS_RANG × Σ_parties (Σ_équipes |somme_équipe − moyenne| / taille_équipe)

Pour deux équipes, le second terme vaut exactement l'écart de rang moyen entre elles.

La recherche est une *recherche locale itérée* :
1. solution de départ gloutonne (rôles attribués par coût croissant, puis « snake draft »
   par niveau entre les équipes) ou aléatoire ;
2. amélioration par échanges de deux joueurs tant que le coût baisse ;
3. perturbations aléatoires + nouvelle amélioration, plusieurs fois.

Toutes les solutions localement optimales rencontrées sont gardées ; on tire ensuite au
hasard (graine ``seed``) parmi celles dont le coût est quasi optimal. Ainsi « Regénérer »
propose une autre composition, mais toujours de qualité. Même graine => même résultat.
Le coût d'un échange est calculé de façon incrémentale : ~20 joueurs se traitent en
quelques dizaines de millisecondes.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------- constantes
MODE_SR = "sr"
MODE_ARAM = "aram"
MODE_ARENA = "arena"
MODES = (MODE_SR, MODE_ARAM, MODE_ARENA)

LANE_ROLES: tuple[str, ...] = ("top", "jungle", "mid", "adc", "support")
FILL = "fill"

TEAM_SIZES: dict[str, int] = {MODE_SR: 5, MODE_ARAM: 5, MODE_ARENA: 2}
MAX_TEAMS_PER_MATCH: dict[str, int] = {MODE_SR: 2, MODE_ARAM: 2, MODE_ARENA: 8}

#: Rang utilisé quand personne n'a de rang connu : Or IV (voir ``RiotAccount.rank_score``).
DEFAULT_RANK_SCORE = 1200.0

#: Une « marche » de préférence (principal -> secondaire) coûte autant que
#: 100 points d'écart de rang moyen (= une division).
ROLE_WEIGHT = 10.0
RANK_WEIGHT = 0.1
#: Pénalités de rôle (multipliées par ROLE_WEIGHT)
FILL_EXTRA = 0.5            # « fill » en position k => k + 0.5
NO_PREFERENCE_PENALTY = 2.0  # aucun rôle renseigné
OFFROLE_PENALTY = 6.0       # rôle ni demandé ni couvert par « fill »

#: Marge de coût pour considérer une solution comme « quasi optimale » (tirage au sort).
NEAR_OPTIMAL_MARGIN = 3.0


class NotEnoughPlayersError(ValueError):
    """Pas assez de joueurs pour former au moins une partie."""

    def __init__(self, required: int, got: int) -> None:
        super().__init__(f"Il faut au moins {required} joueurs (reçu : {got}).")
        self.required = required
        self.got = got


# ---------------------------------------------------------------------------- modèles
@dataclass(frozen=True, slots=True)
class PlayerInfo:
    """Joueur à répartir. ``roles`` : rôles par ordre de préférence (le 1er = principal)."""

    discord_id: int
    roles: Sequence[str] = ()
    rank_score: float | None = None


@dataclass(slots=True)
class Team:
    index: int
    players: list[tuple[int, str | None]]  # (discord_id, rôle attribué ou None)
    total_score: float
    match_index: int = 0

    @property
    def size(self) -> int:
        return len(self.players)

    @property
    def average_score(self) -> float:
        return self.total_score / len(self.players) if self.players else 0.0

    @property
    def member_ids(self) -> list[int]:
        return [pid for pid, _ in self.players]


@dataclass(slots=True)
class TeamsResult:
    teams: list[Team]
    substitutes: list[int]
    mode: str = MODE_SR
    team_size: int = 5
    seed: int | None = None
    cost: float = 0.0
    effective_scores: dict[int, float] = field(default_factory=dict)

    def matches(self) -> list[list[Team]]:
        """Équipes regroupées par partie (Faille/ARAM : 2 équipes ; Arena : jusqu'à 8 duos)."""
        grouped: dict[int, list[Team]] = {}
        for team in self.teams:
            grouped.setdefault(team.match_index, []).append(team)
        return [grouped[k] for k in sorted(grouped)]

    @property
    def average_gap(self) -> float:
        return average_gap(self.teams)

    def assignment_of(self, discord_id: int) -> tuple[int, str | None] | None:
        """(index d'équipe, rôle) du joueur, ou None s'il est remplaçant / absent."""
        for team in self.teams:
            for pid, role in team.players:
                if pid == discord_id:
                    return team.index, role
        return None


# ---------------------------------------------------------------------------- fonctions utilitaires
def _clean_prefs(roles: Sequence[str]) -> list[str]:
    prefs: list[str] = []
    for r in roles:
        r = (r or "").lower()
        if (r in LANE_ROLES or r == FILL) and r not in prefs:
            prefs.append(r)
    return prefs


def role_penalty(roles: Sequence[str], role: str | None) -> float:
    """Insatisfaction d'un joueur placé sur ``role`` (0 = rôle principal)."""
    if role is None:
        return 0.0
    prefs = _clean_prefs(roles)
    if not prefs:
        return NO_PREFERENCE_PENALTY
    if role in prefs:
        return float(prefs.index(role))
    if FILL in prefs:
        return prefs.index(FILL) + FILL_EXTRA
    return OFFROLE_PENALTY


def role_category(roles: Sequence[str], role: str | None) -> str:
    """Catégorie lisible : main | secondary | other | fill | offrole | none."""
    if role is None:
        return "none"
    prefs = _clean_prefs(roles)
    if not prefs:
        return "fill"
    if role in prefs:
        idx = prefs.index(role)
        return "main" if idx == 0 else "secondary" if idx == 1 else "other"
    if FILL in prefs:
        return "main" if prefs[0] == FILL else "fill"
    return "offrole"


def effective_scores(players: Sequence[PlayerInfo], default: float = DEFAULT_RANK_SCORE) -> dict[int, float]:
    """Rang de chaque joueur ; un rang inconnu vaut la moyenne des rangs connus (ou ``default``)."""
    known = [p.rank_score for p in players if p.rank_score is not None]
    fallback = sum(known) / len(known) if known else default
    return {p.discord_id: float(p.rank_score) if p.rank_score is not None else fallback for p in players}


def match_layout(mode: str, n_teams: int) -> list[int]:
    """Index de partie de chaque équipe (équipes consécutives dans une même partie)."""
    per_match = MAX_TEAMS_PER_MATCH.get(mode, 2)
    if n_teams <= 0:
        return []
    if mode != MODE_ARENA:
        return [t // per_match for t in range(n_teams)]
    n_matches = math.ceil(n_teams / per_match)
    base, extra = divmod(n_teams, n_matches)
    layout: list[int] = []
    for m in range(n_matches):
        layout.extend([m] * (base + (1 if m < extra else 0)))
    return layout


def plan_teams(mode: str, n_players: int, team_size: int | None = None) -> tuple[int, int]:
    """(nombre d'équipes, nombre de remplaçants) pour ``n_players`` joueurs.

    Faille / ARAM : nombre d'équipes pair (une partie = 2 équipes).
    Lève ``NotEnoughPlayersError`` s'il n'y a pas de quoi former une partie.
    """
    size = team_size or TEAM_SIZES.get(mode, 5)
    n_teams = n_players // size
    if mode != MODE_ARENA:
        n_teams -= n_teams % 2
    if n_teams < 2:
        raise NotEnoughPlayersError(2 * size, n_players)
    return n_teams, n_players - n_teams * size


def minimum_players(mode: str, team_size: int | None = None) -> int:
    return 2 * (team_size or TEAM_SIZES.get(mode, 5))


def average_gap(teams: Sequence[Team]) -> float:
    """Qualité d'équilibrage : moyenne, sur les parties, de l'écart entre la meilleure et la
    moins bonne équipe (en points de rang moyen par joueur ; 100 = une division)."""
    grouped: dict[int, list[Team]] = {}
    for t in teams:
        if t.players:
            grouped.setdefault(t.match_index, []).append(t)
    gaps = [
        max(t.average_score for t in group) - min(t.average_score for t in group)
        for group in grouped.values()
        if len(group) >= 2
    ]
    return sum(gaps) / len(gaps) if gaps else 0.0


def role_stats(result: TeamsResult, players: Sequence[PlayerInfo]) -> dict[str, int]:
    """Nombre de joueurs par catégorie de satisfaction (main, secondary, other, fill, offrole, none)."""
    by_id = {p.discord_id: p for p in players}
    stats = {"main": 0, "secondary": 0, "other": 0, "fill": 0, "offrole": 0, "none": 0}
    for team in result.teams:
        for pid, role in team.players:
            roles = by_id[pid].roles if pid in by_id else ()
            stats[role_category(roles, role)] += 1
    return stats


# ---------------------------------------------------------------------------- moteur de recherche
class _Search:
    """Recherche locale itérée sur les emplacements (équipe, rôle)."""

    def __init__(
        self,
        players: list[PlayerInfo],
        scores: list[float],
        n_teams: int,
        team_size: int,
        slot_roles: list[str | None],
        team_match: list[int],
        rng: random.Random,
    ) -> None:
        self.players = players
        self.scores = scores
        self.n = len(players)
        self.n_teams = n_teams
        self.team_size = team_size
        self.rng = rng
        self.slot_team = [s // team_size for s in range(self.n)]
        self.slot_roles = slot_roles
        self.use_roles = any(r is not None for r in slot_roles)
        self.team_match = team_match
        self.n_matches = max(team_match) + 1
        self.match_teams: list[list[int]] = [[] for _ in range(self.n_matches)]
        for t, m in enumerate(team_match):
            self.match_teams[m].append(t)
        # pen[p][s] : pénalité (pondérée) du joueur p sur l'emplacement s
        role_cache: dict[tuple[int, str | None], float] = {}
        self.pen: list[list[float]] = []
        for p in players:
            row = []
            for r in slot_roles:
                key = (p.discord_id, r)
                if key not in role_cache:
                    role_cache[key] = ROLE_WEIGHT * role_penalty(p.roles, r)
                row.append(role_cache[key])
            self.pen.append(row)
        # paires d'emplacements utiles à échanger
        self.pairs: list[tuple[int, int]] = []
        for a in range(self.n):
            for b in range(a + 1, self.n):
                same_team = self.slot_team[a] == self.slot_team[b]
                if same_team and (not self.use_roles or slot_roles[a] == slot_roles[b]):
                    continue  # échange sans effet
                self.pairs.append((a, b))

    # -- coût -------------------------------------------------------------------------
    def _match_cost(self, sums: list[float], m: int) -> float:
        teams = self.match_teams[m]
        if len(teams) < 2:
            return 0.0
        mean = sum(sums[t] for t in teams) / len(teams)
        return RANK_WEIGHT * sum(abs(sums[t] - mean) for t in teams) / self.team_size

    def team_sums(self, assign: list[int]) -> list[float]:
        sums = [0.0] * self.n_teams
        for s, p in enumerate(assign):
            sums[self.slot_team[s]] += self.scores[p]
        return sums

    def cost(self, assign: list[int]) -> float:
        sums = self.team_sums(assign)
        role = sum(self.pen[p][s] for s, p in enumerate(assign))
        return role + sum(self._match_cost(sums, m) for m in range(self.n_matches))

    # -- solutions de départ ---------------------------------------------------------------
    def _snake(self, ordered_players: list[int], teams_order: list[int], reverse_first: bool) -> list[list[int]]:
        """Distribue des joueurs (du plus fort au plus faible) en serpentin entre les équipes."""
        buckets: list[list[int]] = [[] for _ in range(self.n_teams)]
        k = len(teams_order)
        for i, p in enumerate(ordered_players):
            rnd, pos = divmod(i, k)
            forward = (rnd % 2 == 0) != reverse_first
            buckets[teams_order[pos if forward else k - 1 - pos]].append(p)
        return buckets

    def greedy_start(self, noise: float) -> list[int]:
        rng = self.rng
        noisy = [s + rng.uniform(-noise, noise) for s in self.scores]
        teams_order = list(range(self.n_teams))
        assign = [-1] * self.n
        if not self.use_roles:
            order = sorted(range(self.n), key=lambda p: -noisy[p])
            buckets = self._snake(order, teams_order, reverse_first=False)
            for t, members in enumerate(buckets):
                for i, p in enumerate(members):
                    assign[t * self.team_size + i] = p
            return assign
        # 1) rôles : on remplit par pénalité croissante (bruit léger pour varier)
        distinct_roles = [r for r in LANE_ROLES if r in self.slot_roles]
        capacity = {r: self.slot_roles.count(r) for r in distinct_roles}
        entries = []
        for p, info in enumerate(self.players):
            for r in distinct_roles:
                entries.append((role_penalty(info.roles, r) + rng.random() * 0.3, rng.random(), p, r))
        entries.sort()
        chosen: dict[int, str] = {}
        by_role: dict[str, list[int]] = {r: [] for r in distinct_roles}
        for _, _, p, r in entries:
            if p in chosen or capacity[r] == 0:
                continue
            chosen[p] = r
            capacity[r] -= 1
            by_role[r].append(p)
        # 2) répartition de chaque rôle entre les équipes en serpentin (sens alterné par rôle)
        for k, r in enumerate(distinct_roles):
            order = sorted(by_role[r], key=lambda p: -noisy[p])
            buckets = self._snake(order, teams_order, reverse_first=bool(k % 2))
            for t, members in enumerate(buckets):
                slots = [t * self.team_size + i for i in range(self.team_size) if self.slot_roles[t * self.team_size + i] == r]
                for s, p in zip(slots, members):
                    assign[s] = p
        return assign

    def random_start(self) -> list[int]:
        assign = list(range(self.n))
        self.rng.shuffle(assign)
        return assign

    # -- amélioration locale -------------------------------------------------------------
    def climb(self, assign: list[int]) -> tuple[list[int], float]:
        assign = list(assign)
        sums = self.team_sums(assign)
        mcost = [self._match_cost(sums, m) for m in range(self.n_matches)]
        pen, scores, slot_team, team_match = self.pen, self.scores, self.slot_team, self.team_match
        pairs = list(self.pairs)
        for _ in range(200):
            self.rng.shuffle(pairs)
            improved = False
            for a, b in pairs:
                pa, pb = assign[a], assign[b]
                delta = pen[pa][b] + pen[pb][a] - pen[pa][a] - pen[pb][b]
                ta, tb = slot_team[a], slot_team[b]
                if ta != tb:
                    diff = scores[pb] - scores[pa]
                    if diff == 0 and delta >= -1e-9:
                        continue
                    ma, mb = team_match[ta], team_match[tb]
                    sums[ta] += diff
                    sums[tb] -= diff
                    new_a = self._match_cost(sums, ma)
                    if ma == mb:
                        delta += new_a - mcost[ma]
                        new_b = new_a
                    else:
                        new_b = self._match_cost(sums, mb)
                        delta += new_a - mcost[ma] + new_b - mcost[mb]
                    if delta < -1e-9:
                        assign[a], assign[b] = pb, pa
                        mcost[ma] = new_a
                        mcost[mb] = new_b
                        improved = True
                    else:
                        sums[ta] -= diff
                        sums[tb] += diff
                elif delta < -1e-9:
                    assign[a], assign[b] = pb, pa
                    improved = True
            if not improved:
                break
        return assign, self.cost(assign)

    def perturb(self, assign: list[int], swaps: int) -> list[int]:
        assign = list(assign)
        for _ in range(swaps):
            a, b = self.pairs[self.rng.randrange(len(self.pairs))]
            assign[a], assign[b] = assign[b], assign[a]
        return assign

    def key(self, assign: list[int]) -> tuple:
        """Clé canonique (indépendante de l'ordre des équipes dans une partie)."""
        teams: list[list[tuple[int, str]]] = [[] for _ in range(self.n_teams)]
        for s, p in enumerate(assign):
            teams[self.slot_team[s]].append((self.players[p].discord_id, self.slot_roles[s] or ""))
        per_match = []
        for m in range(self.n_matches):
            per_match.append(tuple(sorted(tuple(sorted(teams[t])) for t in self.match_teams[m])))
        return tuple(sorted(per_match))

    def run(self) -> tuple[list[int], float]:
        if not self.pairs:
            assign = self.greedy_start(0.0)
            return assign, self.cost(assign)
        n_pairs = len(self.pairs)
        # budget d'améliorations : assez pour explorer, borné pour rester rapide
        climbs = max(8, min(40, 6000 // n_pairs))
        found: dict[tuple, tuple[float, list[int]]] = {}

        def record(assign: list[int], cost: float) -> None:
            k = self.key(assign)
            if k not in found or cost < found[k][0]:
                found[k] = (cost, assign)

        starts = max(2, climbs // 3)
        best_assign: list[int] | None = None
        best_cost = math.inf
        done = 0
        for i in range(starts):
            start = self.greedy_start(noise=0.0 if i == 0 else 150.0) if i % 3 != 2 else self.random_start()
            assign, cost = self.climb(start)
            record(assign, cost)
            done += 1
            if cost < best_cost:
                best_assign, best_cost = assign, cost
        while done < climbs and best_assign is not None:
            candidate, cost = self.climb(self.perturb(best_assign, swaps=2 + self.rng.randrange(3)))
            record(candidate, cost)
            done += 1
            if cost < best_cost - 1e-9:
                best_assign, best_cost = candidate, cost

        # tirage parmi les solutions quasi optimales (déterministe pour une graine donnée)
        near = sorted(
            (k for k, (c, _) in found.items() if c <= best_cost + NEAR_OPTIMAL_MARGIN),
        )
        chosen = found[self.rng.choice(near)]
        return chosen[1], chosen[0]


# ---------------------------------------------------------------------------- API publique
def build_teams(
    players: Sequence[PlayerInfo],
    mode: str,
    *,
    team_size: int | None = None,
    seed: int | None = None,
) -> TeamsResult:
    """Constitue les équipes.

    ``players`` doit être dans l'ordre d'inscription : s'il y a trop de joueurs pour former
    des équipes complètes, les **derniers inscrits** deviennent remplaçants.
    """
    if mode not in MODES:
        raise ValueError(f"Mode inconnu : {mode!r}")
    # dédoublonnage en conservant l'ordre
    seen: set[int] = set()
    unique: list[PlayerInfo] = []
    for p in players:
        if p.discord_id not in seen:
            seen.add(p.discord_id)
            unique.append(p)

    size = team_size or TEAM_SIZES[mode]
    n_teams, _ = plan_teams(mode, len(unique), size)
    playing = unique[: n_teams * size]
    substitutes = [p.discord_id for p in unique[n_teams * size :]]

    rng = random.Random(seed)
    eff = effective_scores(unique)
    scores = [eff[p.discord_id] for p in playing]
    use_roles = mode == MODE_SR and size == len(LANE_ROLES)
    slot_roles: list[str | None] = [
        LANE_ROLES[s % size] if use_roles else None for s in range(n_teams * size)
    ]
    layout = match_layout(mode, n_teams)

    search = _Search(playing, scores, n_teams, size, slot_roles, layout, rng)
    assign, cost = search.run()

    # construction du résultat
    raw_teams: list[list[tuple[int, str | None]]] = [[] for _ in range(n_teams)]
    for s, p in enumerate(assign):
        raw_teams[s // size].append((playing[p].discord_id, slot_roles[s]))

    # côté bleu / rouge (ou ordre des duos) tiré au sort dans chaque partie
    order: list[int] = []
    for m in range(max(layout) + 1):
        in_match = [t for t in range(n_teams) if layout[t] == m]
        rng.shuffle(in_match)
        order.extend(in_match)

    role_rank = {r: i for i, r in enumerate(LANE_ROLES)}
    teams: list[Team] = []
    for new_index, old_index in enumerate(order):
        members = raw_teams[old_index]
        if use_roles:
            members.sort(key=lambda m: role_rank.get(m[1] or "", 99))
        else:
            members.sort(key=lambda m: -eff[m[0]])
        teams.append(
            Team(
                index=new_index,
                players=members,
                total_score=sum(eff[pid] for pid, _ in members),
                match_index=layout[new_index],
            )
        )
    return TeamsResult(
        teams=teams,
        substitutes=substitutes,
        mode=mode,
        team_size=size,
        seed=seed,
        cost=cost,
        effective_scores=eff,
    )
