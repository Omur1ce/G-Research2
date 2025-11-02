import numpy as np
import pandas as pd

# MVP weights (tune later with Poisson regression)
W = {
    "cape": 0.50,
    "asr":  0.35,
    "t_2m": 0.10,
    "cloud": -0.35,  # penalty
    "lapse": 0.20,   # proxy from t_2m - t_850
}

def compute_lapse_norm(df: pd.DataFrame) -> pd.Series:
    # crude lapse proxy: warmer surface minus cool aloft ⇒ more instability
    if "t_2m:C" in df.columns and "t_850hPa:C" in df.columns:
        raw = df["t_2m:C"] - df["t_850hPa:C"]
        return (raw - raw.min()) / (raw.max() - raw.min() + 1e-9)
    return pd.Series(0.0, index=df.index)

def tpi_from_live_and_prior(live_df: pd.DataFrame, prior_0_1: np.ndarray) -> pd.Series:
    # bring norms
    feats = {}
    for base, col in [("cape","cape:Jkg_norm"),
                      ("asr","asr:W_norm"),
                      ("t_2m","t_2m:C_norm"),
                      ("cloud","total_cloud_cover:octas_norm")]:
        feats[base] = live_df[col] if col in live_df.columns else 0.0

    feats["lapse"] = compute_lapse_norm(live_df)

    z = (
        np.log(prior_0_1 + 1e-3) +
        W["cape"]  * feats["cape"] +
        W["asr"]   * feats["asr"]  +
        W["t_2m"]  * feats["t_2m"] +
        W["cloud"] * feats["cloud"]+
        W["lapse"] * feats["lapse"]
    )
    # logistic → 0..1
    tpi = 1.0 / (1.0 + np.exp(-z))
    return pd.Series(tpi, index=live_df.index)

def climb_from_tpi_and_flux(live_df: pd.DataFrame, tpi: pd.Series) -> pd.Series:
    """
    MVP climb estimate [m/s]: scale by TPI and air density.
    Later: use convective velocity scale w*.
    """
    rho = live_df.get("air_density_000m:kgm3", pd.Series(1.2, index=live_df.index))
    asr = live_df.get("asr:W", pd.Series(200.0, index=live_df.index))
    base = 0.3 + 1.2 * (asr / (asr.max() + 1e-9))  # 0.3..~1.5 m/s depending on sun
    climb = tpi * base * (rho / rho.mean())
    return pd.Series(climb, index=live_df.index)
