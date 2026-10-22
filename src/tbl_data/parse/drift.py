"""Schema drift: report source fields we have never seen, so new data is not silently dropped."""

from __future__ import annotations

from collections import defaultdict

# Every key observed per raw object type (2026-27, 56 matches); parsers consume a subset.
KNOWN: dict[str, frozenset[str]] = {
    "genius.root": frozenset(['attendance', 'clock', 'disableMatch', 'inOT', 'leaddata', 'officials', 'othermatches', 'pbp', 'period', 'periodLength', 'periodLengthOVERTIME', 'periodLengthREGULAR', 'periodType', 'periodsMax', 'scorers', 'timeline', 'tm', 'totalTimeAdded', 'totallds']),
    "genius.team": frozenset(['assistcoach1', 'assistcoach1Details', 'assistcoach2', 'assistcoach2Details', 'asstSep', 'coach', 'coachDetails', 'code', 'codeInternational', 'fouls', 'full_score', 'lds', 'logo', 'logoS', 'logoT', 'name', 'nameInternational', 'ot_score', 'p1_score', 'p2_score', 'p3_score', 'p4_score', 'pl', 'score', 'scoring', 'shortName', 'shortNameInternational', 'shot', 'timeouts', 'tot_eff_1', 'tot_eff_2', 'tot_eff_3', 'tot_eff_4', 'tot_eff_5', 'tot_eff_6', 'tot_eff_7', 'tot_sAssists', 'tot_sBenchPoints', 'tot_sBiggestLead', 'tot_sBiggestScoringRun', 'tot_sBlocks', 'tot_sBlocksReceived', 'tot_sFieldGoalsAttempted', 'tot_sFieldGoalsMade', 'tot_sFieldGoalsPercentage', 'tot_sFoulsOn', 'tot_sFoulsPersonal', 'tot_sFoulsTeam', 'tot_sFoulsTotal', 'tot_sFreeThrowsAttempted', 'tot_sFreeThrowsMade', 'tot_sFreeThrowsPercentage', 'tot_sLeadChanges', 'tot_sMinutes', 'tot_sPoints', 'tot_sPointsFastBreak', 'tot_sPointsFromTurnovers', 'tot_sPointsInThePaint', 'tot_sPointsSecondChance', 'tot_sReboundsDefensive', 'tot_sReboundsOffensive', 'tot_sReboundsTeam', 'tot_sReboundsTeamDefensive', 'tot_sReboundsTeamOffensive', 'tot_sReboundsTotal', 'tot_sSteals', 'tot_sThreePointersAttempted', 'tot_sThreePointersMade', 'tot_sThreePointersPercentage', 'tot_sTimeLeading', 'tot_sTimesScoresLevel', 'tot_sTurnovers', 'tot_sTurnoversTeam', 'tot_sTwoPointersAttempted', 'tot_sTwoPointersMade', 'tot_sTwoPointersPercentage']),
    "genius.player": frozenset(['active', 'captain', 'comp', 'eff_1', 'eff_2', 'eff_3', 'eff_4', 'eff_5', 'eff_6', 'eff_7', 'familyName', 'familyNameInitial', 'firstName', 'firstNameInitial', 'internationalFamilyName', 'internationalFamilyNameInitial', 'internationalFirstName', 'internationalFirstNameInitial', 'name', 'photoS', 'photoT', 'playingPosition', 'sAssists', 'sBlocks', 'sBlocksReceived', 'sFieldGoalsAttempted', 'sFieldGoalsMade', 'sFieldGoalsPercentage', 'sFoulsOn', 'sFoulsPersonal', 'sFreeThrowsAttempted', 'sFreeThrowsMade', 'sFreeThrowsPercentage', 'sMinutes', 'sPlusMinusPoints', 'sPoints', 'sPointsFastBreak', 'sPointsInThePaint', 'sPointsSecondChance', 'sReboundsDefensive', 'sReboundsOffensive', 'sReboundsTotal', 'sSteals', 'sThreePointersAttempted', 'sThreePointersMade', 'sThreePointersPercentage', 'sTurnovers', 'sTwoPointersAttempted', 'sTwoPointersMade', 'sTwoPointersPercentage', 'scoreboardName', 'shirtNumber', 'starter']),
    "genius.shot": frozenset(['actionNumber', 'actionType', 'p', 'per', 'perType', 'player', 'pno', 'previousAction', 'r', 'shirtNumber', 'subType', 'tno', 'x', 'y']),
    "genius.event": frozenset(['actionNumber', 'actionType', 'clock', 'familyName', 'familyNameInitial', 'firstName', 'firstNameInitial', 'gt', 'internationalFamilyName', 'internationalFamilyNameInitial', 'internationalFirstName', 'internationalFirstNameInitial', 'lead', 'period', 'periodType', 'player', 'pno', 'previousAction', 'qualifier', 's1', 's2', 'scoreboardName', 'scoring', 'shirtNumber', 'subType', 'success', 'tno']),
    "tbf.fixture": frozenset(['activityCode', 'activityDisplayName', 'activityId', 'activityName', 'awayTeam', 'broadcastChannel', 'cinsiyet', 'genuisId', 'halfValue', 'hasUzatma', 'homeTeam', 'il', 'isFavorite', 'matchCode', 'matchDate', 'matchDateOnly', 'matchId', 'matchStatusId', 'matchTime', 'salonAdi', 'season', 'seasonId', 'week', 'youtubeLink']),
    "tbf.header": frozenset(['awayTeam', 'awayTeamProcessId', 'awayTeamScore', 'faaliyetAdi', 'faaliyetId', 'firstAssistantReferee', 'firstAssistantRefereeImage', 'genuisId', 'hasUzatma', 'homeTeam', 'homeTeamProcessId', 'homeTeamScore', 'isAwayTeamFavorite', 'isHomeTeamFavorite', 'isMatchFavorite', 'isNewDataAvailable', 'leagueName', 'liveVideoLink', 'location', 'mainReferee', 'mainRefereeImage', 'matchBroadcastChannel', 'matchCode', 'matchDateTime', 'matchSpectatorCount', 'matchStatus', 'matchStatusId', 'players', 'seasonId', 'seasonName', 'secondAssistantReferee', 'secondAssistantRefereeImage', 'temsilciAdi', 'temsilciResmi']),
    "tbf.header_team": frozenset(['coachImage', 'coachName', 'hexCode1', 'hexCode2', 'logo', 'teamId', 'teamName']),
    "tbf.box": frozenset(['awayBenchPlayers', 'awayTeamStats', 'awayTeamTotal', 'awayTopFive', 'homeBenchPlayers', 'homeTeamStats', 'homeTeamTotal', 'homeTopFive', 'matchId']),
    "tbf.box_player": frozenset(['assists', 'blocks', 'blocksReceived', 'caption', 'defensiveRebounds', 'efficiency', 'fouls', 'freeThrowAttempts', 'freeThrowPercentage', 'freeThrows', 'image', 'isActive', 'isHome', 'isStarter', 'jerseyNumber', 'minutesPlayed', 'offensiveRebounds', 'playerId', 'playerName', 'plusMinus', 'pno', 'points', 'rivalFoul', 'shotAttempts', 'shotPercentage', 'steals', 'successfulShots', 'teamName', 'threePointPercentage', 'threePointShotAttempts', 'threePointShots', 'totalRebounds', 'turnovers', 'twoPointPercentage', 'twoPointShotAttempts', 'twoPointShots']),
    "tbf.gameflow_event": frozenset(['action', 'actionNumber', 'caption', 'image', 'isActive', 'isHome', 'isStarter', 'jerseyNumber', 'periodType', 'playerId', 'playerName', 'pno', 'quarterNumber', 'score', 'teamName', 'time']),
    "tbf.shot": frozenset(['caption', 'image', 'isActive', 'isHome', 'isStarter', 'isSucceed', 'jerseyNumber', 'period', 'periodType', 'playerId', 'playerName', 'pno', 'teamName', 'x', 'y']),
    "tbf.team": frozenset(['hexRenkKodu1', 'hexRenkKodu2', 'season', 'seasonId', 'teamCityName', 'teamCode', 'teamId', 'teamLogoUrl', 'teamName', 'teamProcessId']),
    "tbf.player_list": frozenset(['adiSoyadi', 'dogumTarihi', 'formaNo', 'fotograf', 'isFavorite', 'kisiID', 'prefix', 'seasonId', 'tabiyet', 'tabiyetKısa', 'takimAdi', 'takimHexRenkKod1', 'takimHexRenkKod2', 'takimID', 'takimLogo']),
    "tbf.profile": frozenset(['age', 'birthDate', 'country', 'formaNo', 'fullName', 'height', 'playerPic', 'teamLogo', 'teamName', 'weight']),
    "tbf.career": frozenset(['activityName', 'assists', 'blocks', 'defensiveRebounds', 'faaliyetAdi', 'faaliyetAlani', 'faaliyetAlaniId', 'faaliyetAltAlaniId', 'faaliyetTuruId', 'fauls', 'fieldGoalsAttempted', 'fieldGoalsMade', 'formattedTotalSeconds', 'freeThowsAttempted', 'freeThowsMade', 'matchCount', 'offensiveRebounds', 'playerEfficiency', 'processDate', 'rebounds', 'seasonCode', 'steals', 'teamName', 'threePointsAttempted', 'threePointsMade', 'totalPoints', 'tournovers', 'yapilanFaul', 'yenilenBlok']),
}


class Drift:
    """Collects unseen keys per object type: {(object, key): (count, first context)}."""

    def __init__(self) -> None:
        self.unknown: dict[tuple[str, str], list] = defaultdict(lambda: [0, None])

    def check(self, obj: str, d: dict, ctx: str) -> dict:
        for k in d.keys() - KNOWN[obj]:
            e = self.unknown[(obj, k)]
            e[0] += 1
            e[1] = e[1] or ctx
        return d

    def rows(self) -> list[dict]:
        return [{"object": o, "key": k, "n": n, "first_seen": c} for (o, k), (n, c) in sorted(self.unknown.items())]
