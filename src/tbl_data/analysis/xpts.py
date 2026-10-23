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


def fit(shots: pl.DataFrame, possessions: pl.DataFrame) -> XptsResult:
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    from sklearn.model_selection import GroupKFold

    f = features(shots, possessions)
    X, y, groups = _matrix(f), f["made"].cast(pl.Int8).to_numpy(), f["match_id"].to_numpy()
    k = min(FOLDS, len(set(groups)))
    zone = f["zone"].to_numpy()
    p, pz = np.zeros(len(y)), np.zeros(len(y))  # model, and the zone-FG% baseline it must beat
    if k >= 2:
        for tr, te in GroupKFold(k).split(X, y, groups):
            p[te] = _model().fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
            rate = {z: y[tr][zone[tr] == z].mean() for z in set(zone[tr])}
            pz[te] = [rate.get(z, y[tr].mean()) for z in zone[te]]
    full = _model().fit(X, y)
    if k < 2:  # too few games to hold any out (tests): in-sample
        p = full.predict_proba(X)[:, 1]
        pz = np.array([y[zone == z].mean() for z in zone])

    value = f["value"].to_numpy()
    metrics = {
        "shots": len(y), "games": len(set(groups)), "out_of_fold": k >= 2, "folds": k,
        "log_loss": round(log_loss(y, p), 4), "log_loss_zone_baseline": round(log_loss(y, pz), 4),
        "log_loss_constant": round(log_loss(y, np.full(len(y), y.mean())), 4),
        "brier": round(brier_score_loss(y, p), 4), "auc": round(roc_auc_score(y, p), 3),
        "xpts_per_shot": round(float((p * value).mean()), 4), "pts_per_shot": round(float((y * value).mean()), 4),
    }
    names = ["distance (spline)"] + NUMERIC[1:] + [f"family={k}" for k in FAMILIES[1:]]
    coefs = full[-1].coef_[0]
    n_spline = len(coefs) - (len(names) - 1)
    coefficients = pl.DataFrame({
        "term": [f"distance spline {i}" for i in range(n_spline)] + names[1:],
        "coef_per_sd": np.round(coefs, 4),  # non-spline terms are standardised: log-odds per 1 SD
    })
    out = f.with_columns(xfg=pl.Series(np.round(p, 4)), xpts=pl.Series(np.round(p * value, 4)))
    return XptsResult(out, coefficients, metrics)
