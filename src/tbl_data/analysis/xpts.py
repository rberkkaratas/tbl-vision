"""Expected points per shot (xPTS): how often a shot like this goes in, times its value.

A logistic regression on what the shooter could see before the shot: distance (spline),
angle to the basket, 2 or 3, the last seconds of a period, shot family (dunk, layup,
tip-in, hook, jumper), second chance, transition, off a turnover, seconds into the
possession and the score margin. Who shot it is left out on purpose: xPTS describes the
shot, and points minus xPTS is what the shooter added.

Left out because the feed only knows them after the outcome:
  - the 'fastbreak' qualifier: Genius tags it on fast-break *points*; 580/580 tagged shots
    are makes. `transition` (a shot within 7 s of a defensive rebound, steal or turnover,
    before any offensive rebound) replaces it.
  - the detailed sub_type: scorers pick the more specific type for makes ('pullupjumpshot'
    59% vs 'jumpshot' 35%); only the coarse family is used.
  - assisted, blocked, fouled: outcomes.

Each shot's xPTS comes from a model fitted without its game (5-fold, grouped by match), so
a player's own shots never shape the expectation they are measured against. Free throws
(and-ones) are not part of xPTS.

With last season's shots as well, each fold is fitted on last season plus this season's other
folds ("pooled"): early in a season there are few shots of its own. Two references are scored
on the same shots: this season alone (in-season folds) and last season's model frozen and
applied unchanged - an honest out-of-time test of the model. Calibration: observed vs
predicted FG% in ten bins of predicted FG%, and the slope and intercept of a logistic fit of
the outcome on logit(xFG): 1 and 0 when the model is neither over- nor under-confident.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

FAMILY = {"dunk": "dunk", "alleyoopdunk": "dunk", "tipindunk": "dunk", "tipinlayup": "tipin", "hookshot": "hook",
          "layup": "layup", "drivinglayup": "layup", "reverselayup": "layup", "alleyoop": "layup",
          "eurostep": "layup"}  # everything else is a jumper
FAMILIES = ["dunk", "layup", "tipin", "hook", "jumper"]
TRANSITION_STARTS = ["after_defensive_rebound", "after_steal", "after_turnover"]
TRANSITION_S = 7
NUMERIC = ["distance_m", "angle_deg", "three", "end_of_period", "second_chance", "transition", "from_turnover",
           "possession_s", "margin_before"]
FOLDS = 5


@dataclass
class XptsResult:
    shots: pl.DataFrame  # match_id, action_number, features, xfg, xpts, pts
    coefficients: pl.DataFrame
    metrics: dict
    calibration: pl.DataFrame  # method x bin: shots, mean xFG, FG%


def features(shots: pl.DataFrame, possessions: pl.DataFrame) -> pl.DataFrame:
    start = possessions.select("match_id", "possession_no", "start_type")
    return shots.join(start, on=["match_id", "possession_no"], how="left").with_columns(
        family=pl.col("sub_type").replace_strict(FAMILY, default="jumper"),
        angle_deg=pl.arctan2((pl.col("y_m") - 7.5).abs(), pl.col("x_m") - 1.575).degrees(),
        three=(pl.col("shot_type") == "3pt").cast(pl.Int8),
        end_of_period=(pl.col("clock_s") <= 3).cast(pl.Int8),
        transition=(pl.col("start_type").is_in(TRANSITION_STARTS) & (pl.col("possession_elapsed_s") <= TRANSITION_S)
                    & ~pl.col("second_chance")).fill_null(False),
        possession_s=pl.col("possession_elapsed_s").fill_null(0).clip(0, 30),
        value=pl.when(pl.col("shot_type") == "3pt").then(3).otherwise(2),
        pts=pl.when(pl.col("made")).then(pl.when(pl.col("shot_type") == "3pt").then(3).otherwise(2)).otherwise(0),
    ).with_columns(pl.col("second_chance", "transition", "from_turnover").cast(pl.Int8),
                   pl.col("margin_before").cast(pl.Float64).fill_null(0))


def _matrix(f: pl.DataFrame) -> np.ndarray:
    fam = np.stack([(f["family"] == k).to_numpy() for k in FAMILIES[1:]], axis=1).astype(float)  # dunk = reference
    return np.hstack([f.select(NUMERIC).cast(pl.Float64).to_numpy(), fam])


def _model():
    from sklearn.compose import ColumnTransformer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import SplineTransformer, StandardScaler

    n = len(NUMERIC) + len(FAMILIES) - 1
    return make_pipeline(
        ColumnTransformer([("distance", SplineTransformer(n_knots=6, degree=3), [0]),
                           ("rest", StandardScaler(), list(range(1, n)))]),
        LogisticRegression(C=1.0, max_iter=3000))


def _scores(y: np.ndarray, p: np.ndarray, value: np.ndarray) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

    q = np.clip(p, 1e-6, 1 - 1e-6)
    lr = LogisticRegression(C=np.inf, max_iter=1000).fit(np.log(q / (1 - q))[:, None], y)
    return {"log_loss": round(log_loss(y, p), 4), "brier": round(brier_score_loss(y, p), 4),
            "auc": round(roc_auc_score(y, p), 3), "calibration_slope": round(float(lr.coef_[0][0]), 3),
            "calibration_intercept": round(float(lr.intercept_[0]), 3),
            "xfg": round(float(p.mean()), 4), "fg": round(float(y.mean()), 4),
            "xpts_vs_pts": round(float((p * value).sum() / (y * value).sum()), 4)}


def _bins(method: str, y: np.ndarray, p: np.ndarray, bins: int = 10) -> pl.DataFrame:
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    b = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    return pl.DataFrame({"bin": b, "p": p, "y": y}).group_by("bin").agg(
        shots=pl.len(), xfg=pl.col("p").mean().round(4), fg=pl.col("y").mean().round(4)).sort("bin").with_columns(
        method=pl.lit(method))


def fit(shots: pl.DataFrame, possessions: pl.DataFrame, prior: tuple[pl.DataFrame, pl.DataFrame] | None = None
        ) -> XptsResult:
    """prior: last season's (shots_enriched, possessions), if there is one."""
    from sklearn.metrics import log_loss
    from sklearn.model_selection import GroupKFold

    f = features(shots, possessions)
    X, y, groups = _matrix(f), f["made"].cast(pl.Int8).to_numpy(), f["match_id"].to_numpy()
    if prior is not None:
        fp = features(*prior)
        Xp, yp = _matrix(fp), fp["made"].cast(pl.Int8).to_numpy()
    k = min(FOLDS, len(set(groups)))
    zone = f["zone"].to_numpy()
    p, pz, p_in = np.zeros(len(y)), np.zeros(len(y)), np.zeros(len(y))  # model, zone-FG% baseline, this season only
    if k >= 2:
        for tr, te in GroupKFold(k).split(X, y, groups):
            p_in[te] = _model().fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
            if prior is not None:
                p[te] = _model().fit(np.vstack([Xp, X[tr]]), np.concatenate([yp, y[tr]])).predict_proba(X[te])[:, 1]
            rate = {z: y[tr][zone[tr] == z].mean() for z in set(zone[tr])}
            pz[te] = [rate.get(z, y[tr].mean()) for z in zone[te]]
        if prior is None:
            p = p_in
    full = _model().fit(X, y) if prior is None else _model().fit(np.vstack([Xp, X]), np.concatenate([yp, y]))
    if k < 2:  # too few games to hold any out (tests): in-sample
        p = p_in = full.predict_proba(X)[:, 1]
        pz = np.array([y[zone == z].mean() for z in zone])

    value = f["value"].to_numpy()
    metrics = {
        "shots": len(y), "games": len(set(groups)), "out_of_fold": k >= 2, "folds": k,
        "prior_shots": 0 if prior is None else len(yp), "method": "in_season" if prior is None else "pooled",
        **{x: v for x, v in _scores(y, p, value).items() if x in ("log_loss", "brier", "auc")},
        "log_loss_zone_baseline": round(log_loss(y, pz), 4),
        "log_loss_constant": round(log_loss(y, np.full(len(y), y.mean())), 4),
        "xpts_per_shot": round(float((p * value).mean()), 4), "pts_per_shot": round(float((y * value).mean()), 4),
    }
    methods = {"in_season": p_in}
    if prior is not None:
        methods["prior_frozen"] = _model().fit(Xp, yp).predict_proba(X)[:, 1]
        methods["pooled"] = p
    metrics["compare"] = {m: _scores(y, q, value) for m, q in methods.items()}
    used = "pooled" if prior is not None else "in_season"
    calibration = pl.concat([_bins(m, y, q).with_columns(
        used=pl.lit(m == used), log_loss_constant=pl.lit(metrics["log_loss_constant"]),
        **{x: pl.lit(v) for x, v in metrics["compare"][m].items() if x not in ("xfg", "fg")})  # keep the bins'
        for m, q in methods.items()])
    names = ["distance (spline)"] + NUMERIC[1:] + [f"family={k}" for k in FAMILIES[1:]]
    coefs = full[-1].coef_[0]
    n_spline = len(coefs) - (len(names) - 1)
    coefficients = pl.DataFrame({
        "term": [f"distance spline {i}" for i in range(n_spline)] + names[1:],
        "coef_per_sd": np.round(coefs, 4),  # non-spline terms are standardised: log-odds per 1 SD
    })
    out = f.with_columns(xfg=pl.Series(np.round(p, 4)), xpts=pl.Series(np.round(p * value, 4)))
    return XptsResult(out, coefficients, metrics, calibration)
