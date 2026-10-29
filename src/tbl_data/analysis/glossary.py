"""What every number on the dashboards means: abbreviation -> Turkish name and a one-line definition.

Tableau shows no tooltip on a header, so the long tables get the definition of the metric in each
row (term_tr, term_desc; col_desc where the column is a second kind of thing) and every view's
tooltip shows it. The same wording as the TBL Veri Sözlüğü page.
"""

from __future__ import annotations

import polars as pl

# key -> (Turkish name with the abbreviation, definition)
GLOSSARY: dict[str, tuple[str, str]] = {
    # team ratings
    "ortg": ("ORtg · Hücum reytingi", "100 hücumda atılan sayı. Hücumlar olay olay sayıldı; hücum ribaundu aynı hücumu sürdürür."),
    "drtg": ("DRtg · Savunma reytingi", "100 rakip hücumunda yenen sayı. Düşük daha iyi."),
    "net": ("Net · Net reyting", "ORtg − DRtg: 100 hücumda atılan ile yenen sayının farkı."),
    "pace": ("Tempo", "40 dakikada ortalama hücum sayısı (iki takımın ortalaması)."),
    "poss": ("Hücum · Hücum sayısı", "Sezonda sayılan hücumlar (lig satırında takım başına ortalama). Hücum isabetli şut, son serbest atış, savunma ribaundu, top kaybı ya da periyot sonuyla biter."),
    "adj_net": ("Düzeltilmiş net reyting", "Rakiplerin gücüne ve sahaya göre düzeltilmiş net reyting (ridge regresyon, hücum sayısıyla ağırlıklı). Az maçta lig ortalamasına doğru çekilir."),
    "adj_o": ("Düzeltilmiş ORtg", "Karşılaştığı savunmaların gücüne ve sahaya göre düzeltilmiş hücum reytingi."),
    "adj_d": ("Düzeltilmiş DRtg", "Karşılaştığı hücumların gücüne ve sahaya göre düzeltilmiş savunma reytingi. Düşük daha iyi."),
    # shooting
    "efg": ("eFG% · Efektif isabet yüzdesi", "(FGM + 0,5 × 3PM) / FGA: üçlüğün fazladan değerini sayan saha içi isabet."),
    "opp_efg": ("Rakip eFG%", "Rakiplerin bu takıma karşı efektif isabeti. Düşük daha iyi; 8 maçta büyük ölçüde şans."),
    "ts": ("TS% · Gerçek şut yüzdesi", "Sayı / (2 × (FGA + 0,44 × FTA)): serbest atışları da sayan şut verimi."),
    "fg3_pct": ("3P% · Üçlük isabeti", "3PM / 3PA. Çok gürültülü: yarı beceri yarı şans olması için ~270 üçlük gerekir."),
    "fg2_pct": ("2P% · İkilik isabeti", "2PM / 2PA."),
    "ft_pct": ("FT% · Serbest atış isabeti", "FTM / FTA."),
    "xpts_per_shot": ("xPTS/şut · Şut kalitesi", "Şut başına beklenen sayı: şutun mesafesi, açısı, türü ve bağlamına göre ligde ortalama bir atıcının getireceği sayı. Atan oyuncu modele girmez."),
    "poe_per_shot": ("POE/şut · Şut başarısı", "(Sayı − xPTS) / şut: şutları beklenenden ne kadar iyi (+) ya da kötü (−) atıyor."),
    "fg3a_rate": ("3PA oranı · Üçlük deneme oranı", "3PA / FGA: şutların ne kadarı üçlük. İyi/kötü değil, oyun tarzı."),
    "ftr_team": ("FT oranı · Serbest atış oranı (takım)", "FTM / FGA: şut başına isabetli serbest atış (Dean Oliver'ın dördüncü faktörü)."),
    "opp_ftr": ("Rakip FT oranı", "Rakiplerin bu takıma karşı FTM / FGA'sı: ne kadar faul ederek serbest atış verdiği. Düşük daha iyi."),
    "ftr_player": ("FT oranı · Serbest atış oranı (oyuncu)", "FTA / FGA: şut başına serbest atış denemesi; faul alma becerisi."),
    # turnovers and rebounds
    "tov_team": ("TOV% · Top kaybı oranı (takım)", "Top kaybı / hücum. Düşük daha iyi."),
    "opp_tov": ("Rakip TOV% · Yaptırılan top kaybı", "Rakiplerin bu takıma karşı top kaybı / hücum. Yüksek daha iyi."),
    "tov_player": ("TOV% · Top kaybı oranı (oyuncu)", "Top kaybı / oyuncunun bitirdiği hücum (FGA + 0,44 × FTA + TOV). Düşük daha iyi."),
    "orb_team": ("ORB% · Hücum ribaundu oranı", "Hücum ribaundu / (hücum ribaundu + rakip savunma ribaundu): kaçan şutların ne kadarı geri alındı."),
    "drb_team": ("DRB% · Savunma ribaundu oranı", "Savunma ribaundu / (savunma ribaundu + rakip hücum ribaundu): rakibin kaçan şutlarının ne kadarı alındı."),
    "orb_player": ("ORB% · Hücum ribaundu oranı (oyuncu)", "Sahadayken alınabilecek hücum ribaundlarının ne kadarını aldı."),
    "drb_player": ("DRB% · Savunma ribaundu oranı (oyuncu)", "Sahadayken alınabilecek savunma ribaundlarının ne kadarını aldı."),
    # player roles
    "usg": ("USG% · Kullanım oranı", "Oyuncu sahadayken takımın oyunlarının (FGA + 0,44 × FTA + TOV) ne kadarını kendisi bitirdi. Ortalama %20."),
    "ast_pct": ("AST% · Asist oranı", "Sahadayken takım arkadaşlarının basketlerinin ne kadarına asist yaptı."),
    "stl_pct": ("STL% · Top çalma oranı", "Top çalma / sahadayken rakip hücum sayısı."),
    "blk_pct": ("BLK% · Blok oranı", "Blok / sahadayken rakibin ikilik denemesi."),
    "on_off": ("On/off · Sahada–kenarda farkı", "Oyuncu sahadayken takımın net reytingi eksi kenardayken. Takım arkadaşlarına bağlıdır; tek oyuncu için çok gürültülü."),
    "mpg": ("Dk/maç · Maç başına dakika", "Oynadığı maçlarda ortalama süre."),
    "ppg": ("Sayı/maç", "Oynadığı maçlarda ortalama sayı."),
    "pts40": ("Sayı/40 dk", "40 dakikaya ölçeklenmiş sayı: süreden bağımsız skor üretimi."),
    "rpg": ("Rib./maç · Maç başına ribaund", "Oynadığı maçlarda ortalama ribaund."),
    "apg": ("Asist/maç", "Oynadığı maçlarda ortalama asist."),
    "games_starts": ("Maç (ilk 5)", "Oynadığı maç sayısı; parantezde ilk beşte başladığı maçlar."),
    "rim_share": ("Pota dibi payı", "Şutlarının ne kadarı potaya 1,25 m yakından."),
    "ts_stable": ("TS% dengeli", "Bu sezonun TS%'si, lig ortalaması ve geçen sezonla harmanlanmış tahmin (ampirik Bayes). ± %90 aralığın yarısı, puan."),
    "fg3_stable": ("3P% dengeli", "Üçlük yüzdesinin lig ortalaması ve geçen sezonla harmanlanmış tahmini. ± %90 aralığın yarısı, puan."),
    # style and game shape
    "trans": ("Geçiş payı · Hızlı hücum şutları", "Savunma ribaundu, top çalma ya da top kaybından sonraki ilk 7 saniyede atılan şutların payı."),
    "rim": ("Rim payı · Pota dibi şut payı", "Şutların ne kadarı potaya 1,25 m yakından."),
    "mid": ("Orta mesafe payı", "Üçlük çizgisi içinde, boyalı alan dışından atılan şutların payı."),
    "xps": ("xPTS/şut (hücum) · Aldığı şut kalitesi", "Takımın kendi şutlarının ortalama beklenen sayısı."),
    "o_xps": ("xPTS/şut (verdiği) · Verdiği şut kalitesi", "Rakiplerin bu takıma karşı aldığı şutların beklenen sayısı. Düşük daha iyi: rakibe kötü şut aldırıyor."),
    "q1net": ("1. çeyrek farkı", "İlk çeyrekte maç başına atılan − yenen sayı. Oyuna nasıl başladığı."),
    "clutch": ("Clutch net", "4. çeyreğin ya da uzatmanın son 5 dakikası, fark 5 ya da daha az iken net reyting. Çok az hücum: yönüne bakın."),
    "pts_scored": ("Attığı", "Maç başına bu çeyrekte attığı sayı."),
    "pts_allowed": ("Yediği", "Maç başına bu çeyrekte yediği sayı."),
    "pts_diff": ("Fark", "Maç başına bu çeyrekte atılan − yenen."),
    # match summary
    "ms_pts": ("Sayı", "Maçta attığı sayı."),
    "ms_lead": ("En büyük fark", "Maç içinde önde olduğu en büyük sayı farkı."),
    "ms_run": ("En uzun seri", "Rakip sayı atmadan üst üste attığı en çok sayı."),
    "ms_leadmin": ("Önde (dk)", "Skorda önde geçirdiği dakika."),
    "ms_tied": ("Eşitlik", "Skorun kaç kez eşitlendiği (0-0 hariç; çeyrek beraber başlarsa bir daha sayılır)."),
    "ms_lc": ("Liderlik değişimi", "Geride olan takımın öne geçme sayısı (iki takım toplam)."),
    "ms_deficit": ("En çok geride", "Maç içinde en çok kaç sayı geride kaldığı."),
    # possession starts
    "start_after_defensive_rebound": ("Savunma ribaundu sonrası", "Rakibin kaçan şutunu alarak başlayan hücum."),
    "start_after_made_fg": ("Sayı sonrası", "Rakibin basketinden sonra oyun dışından başlayan hücum."),
    "start_after_steal": ("Top çalma sonrası", "Top çalmayla başlayan hücum; çoğu zaman hızlı hücum."),
    "start_after_turnover": ("Ölü top kaybı sonrası", "Rakibin çalınmadan topu kaybetmesi (dışarı, adım, 24 saniye…) sonrası başlayan hücum."),
    "start_after_made_ft": ("Serbest atış sonrası", "Rakibin son serbest atışı girdikten sonra başlayan hücum."),
    "start_period_start": ("Periyot başı", "Periyodun ilk hücumu."),
    "start_jumpball": ("Hava atışı", "Hava atışı ya da alternatif okla gelen hücum."),
    "start_unsportsmanlike_foul": ("Sportmenlik dışı faul", "Faulden sonra top hücumdaki takımda kaldı."),
    "start_inferred": ("Belirsiz (veri boşluğu)", "Kayıtta bitiş olayı olmadan el değiştiren hücum."),
    # reliability and padding (Güven)
    "col_season": ("Bu sezon", "Bu sezonun ham değeri."),
    "col_units": ("Deneme", "Değerin dayandığı şut, hücum ya da fırsat sayısı."),
    "col_prior": ("Geçen sezon", "Geçen sezonun değeri; * başka bir ligde."),
    "col_stable": ("Dengeli", "Ham değerin lig ortalaması ve geçen sezonla harmanlanmış tahmini: az denemede lig ortalamasına daha çok yaslanır."),
    "col_interval": ("%90 aralık", "Gerçek düzeyin %90 olasılıkla bulunduğu aralık."),
    # Rakip Analizi: projection
    "pr_pace": ("Tempo (beklenen)", "İki takımın temposu çarpımı / lig temposu: bu maçta beklenen 40 dakikalık hücum sayısı."),
    "pr_ortg_a": ("Ana takım · 100 hücumda", "Ana takımın bu rakibe ve sahaya karşı beklenen hücum reytingi (düzeltilmiş reytinglerden)."),
    "pr_ortg_b": ("Rakip · 100 hücumda", "Rakibin ana takıma ve sahaya karşı beklenen hücum reytingi."),
    "pr_score": ("Beklenen skor", "Beklenen hücum reytingi × beklenen tempo / 100, iki takım için."),
    "pr_margin": ("Fark (%80 aralık)", "Beklenen sayı farkı; parantezde maçların %80'inin düşeceği aralık (tek maç ±12 sayı oynar)."),
    "pr_win": ("Kazanma olasılığı", "Beklenen fark / maç-maç sapma (σ) üzerinden normal dağılım. Bu sezon tahmin isabeti henüz zayıf."),
    # Rakip Analizi: four-factor columns
    "fx_att": ("Hücum eden", "Hücum eden takımın sezon değeri; yanında lig sırası (1 en iyi)."),
    "fx_def": ("Savunmanın izni", "Savunma yapan takımın rakiplerine verdiği değer; yanında lig sırası (1 en iyi savunma)."),
    "fx_lg": ("Lig", "Lig ortalaması."),
    "fx_exp": ("Beklenen", "Hücum eden × savunmanın izni / lig: bu eşleşmede beklenen değer."),
    "fx_imp": ("Etki (100 hücum)", "Beklenenin ligden farkı × faktörün ağırlığı: 100 hücumda kaç sayı; + ana takım yönünde, − rakip yönünde. Sezon eğilimidir, tek maç tahmini değil."),
    "fx_rel": ("Güven", "● 8 maçta oturmuş, ◐ kısmen, ○ büyük ölçüde şans (iki takımın değerinden zayıf olanı)."),
    "fx_r3": ("3PA oranı (stil)", "3PA / FGA. Sayı etkisi hesaplanmaz: ne kadar üçlük atıldığı tarzdır."),
    # Rakip Analizi: rotation and fives
    "rot_q": ("Çeyrek dakikası", "Takım maçı başına bu çeyrekte sahada geçirdiği dakika (çeyrek 10 dakika)."),
    "rot_close": ("Ç4 son 5 dk", "4. çeyreğin son 5 dakikasında maç başına dakika: maçı kim kapatıyor."),
    "ln_min": ("Dakika", "Beşlinin sezonda birlikte oynadığı dakika."),
    "ln_games": ("Maç", "Beşlinin birlikte sahaya çıktığı maç sayısı."),
    "ln_poss": ("Hücum", "Beşli sahadayken oynanan hücum + savunma sayısı."),
    "ln_net": ("Net (±1 SE)", "Beşli sahadayken net reyting; ± bir standart hata. Az hücumda çok oynar."),
    "hm_wl": ("G–M · Galibiyet–mağlubiyet", "Oynanan maçlarda galibiyet ve mağlubiyet. Sıralama önce galibiyete, sonra düzeltilmiş net reytinge göre."),
    "hm_team": ("Takım", "Sıra: önce galibiyet, sonra düzeltilmiş net reyting."),
    "hm_power": ("Güç sırası", "Düzeltilmiş net reytinge göre lig sırası: fikstürün zorluğunu ve sahayı hesaba katar; projeksiyon bunu kullanır."),
    "zone_share": ("Şut payı", "Bölgeden atılan şutların tüm şutlara oranı. İzin verilen: rakiplerin bu takıma karşı attığı şutlarda."),
    "form": ("Fark − beklenen", "Maçtaki skor farkı eksi sezon reytinglerinin o rakibe ve sahaya göre beklediği fark: + beklentinin üstünde."),
}

FACTOR_KEYS = {("eFG%", "Hücum"): "efg", ("eFG%", "Savunma"): "opp_efg", ("Kayıp %", "Hücum"): "tov_team",
               ("Kayıp %", "Savunma"): "opp_tov", ("Ribaund %", "Hücum"): "orb_team", ("Ribaund %", "Savunma"): "drb_team",
               ("FT oranı", "Hücum"): "ftr_team", ("FT oranı", "Savunma"): "opp_ftr"}
PLAYER_STAT = {"tov_pct": "tov_player", "ft_rate": "ftr_player", "orb_pct": "orb_player", "drb_pct": "drb_player"}
# per file: (column whose value names the metric, value -> glossary key; a missing value maps to itself)
SPEC: dict[str, tuple[str, dict[str, str]]] = {
    "team_kpis": ("metric", {"ORtg": "ortg", "DRtg": "drtg", "Net": "net", "Tempo": "pace", "eFG%": "efg", "TS%": "ts",
                             "xPTS/şut": "xpts_per_shot", "Hücum": "poss"}),
    "player_card": ("metric", {"Dk/maç": "mpg", "Sayı/maç": "ppg", "Rib./maç": "rpg", "Asist/maç": "apg", "TS%": "ts",
                               "USG%": "usg"}),
    "player_percentiles": ("metric", PLAYER_STAT),
    "player_stable": ("stat", PLAYER_STAT),
    "stat_reliability": ("stat", PLAYER_STAT),
    "prior_backtest": ("stat", PLAYER_STAT),
    "match_summary": ("metric", {"Sayı": "ms_pts", "En büyük fark": "ms_lead", "En uzun seri": "ms_run",
                                 "Önde (dk)": "ms_leadmin", "Eşitlik": "ms_tied", "Liderlik değişimi": "ms_lc",
                                 "En çok geride": "ms_deficit"}),
    "possession_starts": ("start_type", {}),  # start_<type>
    "mx_projection": ("col", {"Tempo": "pr_pace", "Ana takım ORtg": "pr_ortg_a", "Rakip ORtg": "pr_ortg_b",
                              "Beklenen skor": "pr_score", "Fark": "pr_margin", "Kazanma": "pr_win"}),
    "mx_factors": ("factor", {"efg": "efg", "tov": "tov_team", "orb": "orb_team", "ftr": "ftr_team", "r3": "fx_r3"}),
    "mx_identity": ("metric_key", {"r3": "fg3a_rate", "ftr": "ftr_team"}),
    "mx_periods": ("metric", {"Attığı": "pts_scored", "Yediği": "pts_allowed", "Fark": "pts_diff"}),
    "mx_players": ("col", {"Dk/maç": "mpg", "Maç (ilk 5)": "games_starts", "USG%": "usg", "TS% dengeli": "ts_stable",
                           "3PA oranı": "fg3a_rate", "FT oranı": "ftr_player", "AST%": "ast_pct", "TOV%": "tov_player",
                           "ORB%": "orb_player", "Pota dibi payı": "rim_share", "3P% dengeli": "fg3_stable"}),
    "mx_rotation": ("col", {"Ç1": "rot_q", "Ç2": "rot_q", "Ç3": "rot_q", "Ç4": "rot_q", "Ç4 son 5 dk": "rot_close"}),
    "mx_lineups": ("col", {"Dakika": "ln_min", "Maç": "ln_games", "Hücum": "ln_poss", "Net (±1 SE)": "ln_net"}),
    "mx_style": ("metric_key", {"r3": "fg3a_rate", "ftr": "ftr_team"}),
    "home_table": ("col", {"Takım": "hm_team", "G–M": "hm_wl", "Net": "net", "Düz. net": "adj_net", "Güç": "hm_power"}),
}
# a second dimension with its own definition: (file, column, value -> key)
COL_SPEC = {
    "player_stable": ("col", {"Bu sezon": "col_season", "Deneme": "col_units", "Geçen sezon": "col_prior",
                              "Dengeli": "col_stable", "%90 aralık": "col_interval"}),
    "mx_factors": ("col", {"Hücum eden": "fx_att", "Savunmanın izni": "fx_def", "Lig": "fx_lg", "Beklenen": "fx_exp",
                           "Etki (100 hücum)": "fx_imp", "Güven": "fx_rel"}),
}


def term(key: str) -> str:
    """'ORtg · Hücum reytingi: 100 hücumda atılan sayı …' for a static tooltip line."""
    name, desc = GLOSSARY[key]
    return f"{name}: {desc}"


def _lookup(values: pl.Series, mapping: dict[str, str], prefix: str = "") -> tuple[list, list]:
    keys = [mapping.get(v, prefix + v if v is not None else None) for v in values]
    return [GLOSSARY.get(k, (None, None))[0] for k in keys], [GLOSSARY.get(k, (None, None))[1] for k in keys]


def annotate(files: dict[str, pl.DataFrame]) -> dict[str, pl.DataFrame]:
    """Adds term_tr / term_desc (and col_tr / col_desc) to every long table the dashboards read."""
    out = dict(files)
    for name, (col, mapping) in SPEC.items():
        if name not in out or col not in out[name].columns:
            continue
        df = out[name]
        tr, desc = _lookup(df[col], mapping, "start_" if name == "possession_starts" else "")
        out[name] = df.with_columns(term_tr=pl.Series(tr, dtype=pl.String), term_desc=pl.Series(desc, dtype=pl.String))
    ff = out.get("four_factors")
    if ff is not None:
        keys = [FACTOR_KEYS.get((f, s)) for f, s in ff.select("factor_short", "side").iter_rows()]
        out["four_factors"] = ff.with_columns(term_tr=pl.Series([GLOSSARY[k][0] if k else None for k in keys], dtype=pl.String),
                                              term_desc=pl.Series([GLOSSARY[k][1] if k else None for k in keys], dtype=pl.String))
    for name, (col, mapping) in COL_SPEC.items():
        if name in out and col in out[name].columns:
            tr, desc = _lookup(out[name][col], mapping)
            out[name] = out[name].with_columns(col_tr=pl.Series(tr, dtype=pl.String), col_desc=pl.Series(desc, dtype=pl.String))
    return out
