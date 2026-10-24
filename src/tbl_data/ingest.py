"""Sync: fixture -> due resources -> fetch -> raw store + manifest.

Re-running is cheap: a resource is only requested when it is new, past its next_due
time, or explicitly refreshed. Finished matches become final 48 h after tip-off
(late stat corrections land in that window) and are never requested again.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from . import sources
from .config import Season
from .fetch import ChallengeBlocked, Fetcher, FetchResult, Resource
from .store import Manifest, RawStore

log = structlog.get_logger()

TR = ZoneInfo("Europe/Istanbul")
SEASON_TTL = timedelta(hours=6)
FIXTURE_TTL = timedelta(hours=6)
LIVE_TTL = timedelta(hours=2)  # match fetched before it settled
MATCH_SETTLE = timedelta(hours=48)
MISSING_TTL = timedelta(hours=24)
ERROR_TTL = timedelta(hours=1)

BOX_LISTS = ("homeTopFive", "homeBenchPlayers", "awayTopFive", "awayBenchPlayers")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class BudgetExhausted(Exception):
    pass


@dataclass
class RunStats:
    run_id: str
    requests: int = 0
    new_versions: int = 0
    not_modified: int = 0
    challenges: int = 0
    errors: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    outcome: str = "running"


@dataclass
class Policy:
    """How long a successful fetch stays fresh; final=True means never again."""

    final: bool = False
    ttl: timedelta | None = None


class Syncer:
    def __init__(
        self,
        season: Season,
        store: RawStore,
        manifest: Manifest,
        tbf: Fetcher,
        genius: Fetcher,
        *,
        now: Callable[[], datetime] = utcnow,
        max_requests: int | None = None,
        refresh_season: bool = False,
        refresh_players: bool = False,
        with_players: bool = True,
        player_games: bool = False,
        only: str | None = None,  # "tbf" | "genius"
    ):
        self.s = season
        self.scope = f"{season.league}/{season.key}"
        self.store, self.m = store, manifest
        self.fetchers = {"tbf": tbf, "genius": genius}
        self.now = now
        self.max_requests = max_requests
        self.refresh_season = refresh_season
        self.refresh_players = refresh_players
        self.with_players = with_players
        self.player_games = player_games
        self.only = only
        self.stats = RunStats(run_id=uuid.uuid4().hex[:12])

    # -- public --------------------------------------------------------------------
    def run(self) -> RunStats:
        args = {"scope": self.scope, "max_requests": self.max_requests, "only": self.only,
                "refresh_season": self.refresh_season, "refresh_players": self.refresh_players}
        self.m.start_run(self.stats.run_id, self.now(), args)
        if self.refresh_players:
            for kind in sources.PLAYER_KINDS:
                self.m.unfinalize("tbf", kind)
        try:
            self._season_level()
            self._fixtures()
            self._matches()
            if self.with_players:
                self._players()
            self.stats.outcome = "ok"
        except BudgetExhausted:
            self.stats.outcome = "budget"
        except ChallengeBlocked as e:
            self.stats.outcome = "blocked"
            log.error("sync.blocked", reason=str(e))
        finally:
            tbf = self.fetchers["tbf"]
            self.stats.challenges = getattr(tbf, "challenges", 0)
            self.m.finish_run(self.stats.run_id, self.now(), **{
                k: v for k, v in asdict(self.stats).items()
                if k in ("requests", "new_versions", "challenges", "errors", "outcome")})
        return self.stats

    # -- stages --------------------------------------------------------------------
    def _season_level(self) -> None:
        for res in sources.season_resources(self.s):
            self._get(res, Policy(ttl=SEASON_TTL), force=self.refresh_season)

    def _fixtures(self) -> None:
        weeks = self._json(sources.league_weeks(self.s)) or {}
        for w in weeks.get("data") or []:
            res = sources.fixture_week(self.s, w["sezon_Hafta"], w.get("devre_Deger"))
            prev = self._json(res)
            self._get(res, self._fixture_policy(prev), force=self.refresh_season)
            doc = self._json(res)
            for m in (doc or {}).get("data") or []:
                self.m.upsert_match(self._match_row(m, w.get("devre_Deger")))

    def _fixture_policy(self, doc: dict | None) -> Policy:
        # a week whose matches all settled will not change any more
        ms = (doc or {}).get("data") or []
        now = self.now()
        settled = ms and all(self._played(m) and self._start(m) + MATCH_SETTLE <= now for m in ms)
        return Policy(final=bool(settled), ttl=FIXTURE_TTL)

    def _matches(self) -> None:
        for row in self.m.matches(self.scope, played_only=True):
            pol = self._match_policy(datetime.fromisoformat(row["match_start"]))
            if row["genius_id"]:
                self._get(sources.genius_match(row["genius_id"]), pol)
            for res in sources.tbf_match(row["match_id"]):
                self._get(res, pol)

    def _match_policy(self, start: datetime) -> Policy:
        if self.now() >= start + MATCH_SETTLE:
            return Policy(final=True)
        return Policy(ttl=LIVE_TTL)

    def _players(self) -> None:
        for pid in self._box_player_ids():
            for res in sources.player(self.s, pid):
                # a player's per-season game log repeats our own box scores: only on request
                if res.kind == "player_season_games" and not self.player_games:
                    continue
                self._get(res, Policy(final=True))

    def _box_player_ids(self) -> list[int]:
        ids: set[int] = set()
        for row in self.m.matches(self.scope, played_only=True):
            doc = self._json(sources.tbf_match(row["match_id"])[2])  # match_boxscore
            data = (doc or {}).get("data") or {}
            for key in BOX_LISTS:
                ids.update(p["playerId"] for p in data.get(key) or [] if p.get("playerId"))
        return sorted(ids)

    # -- fetch one resource ----------------------------------------------------------
    def _due(self, res: Resource, force: bool) -> bool:
        if self.only and res.source != self.only:
            return False
        st = self.m.state(*res.key)
        if st is None or force:
            return True
        if st.final:
            return False
        return st.next_due is None or datetime.fromisoformat(st.next_due) <= self.now()

    def _get(self, res: Resource, pol: Policy, force: bool = False) -> None:
        if not self._due(res, force):
            return
        if self.max_requests is not None and self.stats.requests >= self.max_requests:
            raise BudgetExhausted
        prev = self.m.state(*res.key)
        r = self.fetchers[res.source].fetch(res, etag=prev.etag if prev and prev.sha256 else None)
        self.stats.requests += 1
        self.stats.by_kind[res.kind] = self.stats.by_kind.get(res.kind, 0) + 1
        self._record(res, r, pol, prev.sha256 if prev else None)

    def _record(self, res: Resource, r: FetchResult, pol: Policy, prev_sha: str | None) -> None:
        now = self.now()
        sha, changed, nbytes = None, False, len(r.body)
        if r.not_modified:
            status, sha = "ok", prev_sha
            self.stats.not_modified += 1
        elif r.ok:
            status = _classify(r.body, res.source)
            if status in ("ok", "empty"):
                sha = self.store.put(*res.key, r.body)
                changed = sha != prev_sha
                self.stats.new_versions += int(changed)
        elif r.status == 404:
            status = "missing"
        else:
            status = "error"
        if status == "ok":
            final, due = pol.final, (None if pol.final else now + (pol.ttl or LIVE_TTL))
        else:
            final, due = False, now + (MISSING_TTL if status in ("missing", "empty") else ERROR_TTL)
        if status in ("error", "missing"):
            self.stats.errors += 1
            log.warning("fetch.failed", kind=res.kind, rid=res.rid, http=r.status, status=status, error=r.error)
        else:
            log.info("fetch", kind=res.kind, rid=res.rid, http=r.status, changed=changed, ms=r.elapsed_ms)
        self.m.record(
            run_id=self.stats.run_id, source=res.source, kind=res.kind, rid=res.rid, url=res.url,
            method=res.method, scope=self.scope, now=now, http_status=r.status, status=status, sha=sha,
            etag=r.headers.get("etag") if status == "ok" else None, changed=changed, final=final,
            next_due=due, nbytes=nbytes, elapsed_ms=r.elapsed_ms, challenge=r.challenge, error=r.error,
        )

    # -- helpers -----------------------------------------------------------------------
    def _json(self, res: Resource) -> dict | None:
        sha = self.m.latest_sha(*res.key)
        return json.loads(self.store.get(*res.key, sha)) if sha else None

    @staticmethod
    def _start(m: dict) -> datetime:
        return datetime.fromisoformat(m["matchDate"]).replace(tzinfo=TR).astimezone(UTC)

    def _played(self, m: dict) -> bool:
        scores = [_int(m["homeTeam"].get("score")), _int(m["awayTeam"].get("score"))]
        return self._start(m) <= self.now() and any(scores)

    def _match_row(self, m: dict, half_value: str | None) -> dict:
        h, a = m["homeTeam"], m["awayTeam"]
        return {
            "match_id": m["matchId"], "scope": self.scope, "week": m.get("week"), "half_value": half_value,
            "match_start": self._start(m).isoformat(),
            "home_team_id": h.get("id"), "home": h.get("name"), "away_team_id": a.get("id"), "away": a.get("name"),
            "home_score": _int(h.get("score")), "away_score": _int(a.get("score")),
            "genius_id": (m.get("genuisId") or "").strip() or None,  # (sic) API spelling
            "youtube": m.get("youtubeLink") or None, "broadcast": m.get("broadcastChannel"),
            "status_id": m.get("matchStatusId"), "played": int(self._played(m)),
            "updated_at": self.now().isoformat(),
        }


def _int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _classify(body: bytes, source: str) -> str:
    """ok | empty (valid JSON, no payload) | error (not JSON)."""
    try:
        doc = json.loads(body)
    except ValueError:
        return "error"
    if source == "tbf" and isinstance(doc, dict) and doc.get("data") in (None, [], {}):
        return "empty"
    return "ok"


def summarize(manifest: Manifest, scope: str) -> dict[str, Iterable]:
    db = manifest.db
    return {
        "resources": db.execute(
            "SELECT source, kind, status, SUM(final) AS final, COUNT(*) AS n FROM resources"
            " WHERE scope=? GROUP BY source, kind, status ORDER BY source, kind, status", (scope,)).fetchall(),
        "matches": db.execute(
            "SELECT COUNT(*) AS n, SUM(played) AS played, SUM(genius_id IS NOT NULL AND played=1) AS genius,"
            " SUM(youtube IS NOT NULL AND played=1) AS youtube FROM matches WHERE scope=?", (scope,)).fetchone(),
        "runs": db.execute(
            "SELECT run_id, started_at, finished_at, requests, new_versions, challenges, errors, outcome"
            " FROM runs ORDER BY started_at DESC LIMIT 5").fetchall(),
    }
