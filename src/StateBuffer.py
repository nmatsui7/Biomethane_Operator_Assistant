"""
StateBuffer: SQLite-backed data management for biogas plant operations.

Purpose
-------
- Accept pre-mapped sensor readings (internal variable names only)
- Filter anomalous sensor data using CUSUM (Cumulative Sum) control charts
- Maintain a rolling buffer of plant state history (default 48-hour retention)
- Provide clean data for EnKF S2 and AD4 simulator via get_model_dataframe()

Design
------
- Mapping responsibility removed: scada_mapper.py handles all SCADA tag ->
  internal variable name translation BEFORE data reaches this class.
  insert_live_data() accepts {internal_var: value} dicts only.
- CUSUM parameters configurable via site_config.json (per-site tuning).
- Single-site: one buffer per installation.
- WAL mode: supports concurrent reads from MCP tool calls.
- Three tables: raw_telemetry (audit), plant_state (processed), cusum_state.

Integration
-----------
Typical call sequence:
    mapping = auto_map_dataframe(df, vendor)          # scada_mapper.py
    buf = StateBuffer.from_config("site_config.json") # or StateBuffer()
    buf.insert_live_data(mapped_row)                  # already-mapped dict
    df = buf.get_model_dataframe()                    # clean data for MCP/EnKF

Site config (site_config.json)
------------------------------
{
    "db_path": "data/plant_state.sqlite",
    "retention_hours": 48,
    "cusum_params": {
        "digester_temp_c":    {"K": 0.5,  "H": 2.0},
        "digester_ph":        {"K": 0.1,  "H": 0.5},
        "biogas_flow_nm3h":   {"K": 10.0, "H": 50.0},
        "ch4_pct":            {"K": 1.0,  "H": 5.0},
        "h2s_ppm":            {"K": 20.0, "H": 100.0}
    }
}
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, Optional

import pandas as pd


# ── Default CUSUM parameters (overridden by site_config.json) ─────────────────
# K = allowance / slack: how much deviation is tolerated before accumulating
# H = decision threshold: CUSUM score above this → reading discarded as spike
#
# These defaults are tuned for a farm-scale mesophilic digester (R1-FLEX).
# Adjust H upward for noisier sensors; downward for tighter anomaly detection.

# Industrial-scale CUSUM params (digester ~6.0 m³, biogas ~142 Nm³/h)
DEFAULT_CUSUM_PARAMS: Dict[str, Dict[str, float]] = {
    "digester_temp_c":  {"K": 0.5,  "H": 2.0},   # ±2°C from rolling mean
    "digester_ph":      {"K": 0.1,  "H": 0.5},   # ±0.5 pH units
    "biogas_flow_nm3h": {"K": 10.0, "H": 50.0},  # ±50 Nm³/h
    "ch4_pct":          {"K": 1.0,  "H": 5.0},   # ±5% CH4
    "h2s_ppm":          {"K": 20.0, "H": 100.0}, # ±100 ppm H2S
}

# Lab-scale CUSUM params (digester ~1.0 m³, biogas ~0.00003 Nm³/h = 30 mL/h)
# Scaled down by ~1,000,000x from industrial (mL/day → Nm³/h conversion)
LAB_SCALE_CUSUM_PARAMS: Dict[str, Dict[str, float]] = {
    "digester_temp_c":  {"K": 0.5,  "H": 2.0},   # Same temp tolerance
    "digester_ph":      {"K": 0.1,  "H": 0.5},   # Same pH tolerance
    "biogas_flow_nm3h": {"K": 0.00001, "H": 0.00005},  # Scaled for ~0.00003 Nm³/h
    "ch4_pct":          {"K": 1.0,  "H": 5.0},   # Same CH4 tolerance
    "h2s_ppm":          {"K": 20.0, "H": 100.0}, # Same H2S tolerance
}

# Columns present in the plant_state SQLite table
PLANT_STATE_COLS = [
    "digester_temp_c",
    "digester_ph",
    "vfa_mmol_l",
    "biogas_flow_nm3h",
    "ch4_pct",
    "h2s_ppm",
    "biomethane_purity_pct",
    "organic_load_kg_vs_d",
    "hydraulic_retention_days",
    "fos_mg_per_l",
]


class StateBuffer:
    """
    SQLite-backed rolling buffer for plant state data.

    Accepts already-mapped data (internal variable names) from scada_mapper.
    Does NOT perform any SCADA tag translation itself.

    Tables
    ------
    raw_telemetry  Full payloads with UTC timestamps (audit trail).
    plant_state    Processed, CUSUM-filtered values for MCP / EnKF / AD4.
    cusum_state    Per-sensor CUSUM accumulators and rolling means.

    Args
    ----
    db_path          : SQLite file path, or ":memory:" for tests.
    retention_hours  : Records older than this are purged (default 48 h).
    cusum_params     : Per-variable CUSUM {K, H} dict. Falls back to
                       DEFAULT_CUSUM_PARAMS (or LAB_SCALE_CUSUM_PARAMS if lab_scale).
    lab_scale        : If True, use LAB_SCALE_CUSUM_PARAMS (scaled for ~1.0 m³ digester).
    """

    def __init__(
        self,
        db_path: str = "plant_state.sqlite",
        retention_hours: int = 48,
        cusum_params: Optional[Dict[str, Dict[str, float]]] = None,
        lab_scale: bool = False,
    ):
        self.db_path = db_path
        self.retention_seconds = retention_hours * 3600
        self.lab_scale = lab_scale
        if cusum_params:
            self.cusum_params = {**DEFAULT_CUSUM_PARAMS, **cusum_params}
        elif lab_scale:
            self.cusum_params = {**LAB_SCALE_CUSUM_PARAMS}
        else:
            self.cusum_params = {**DEFAULT_CUSUM_PARAMS}
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._initialize_db()

    # ── Alternative constructor ────────────────────────────────────────────────
    @classmethod
    def from_config(cls, config_path: str, lab_scale: bool = False) -> "StateBuffer":
        """
        Instantiate from a site_config.json file.

        Expected keys (all optional — falls back to defaults if absent):
            db_path          : path to SQLite file
            retention_hours  : integer
            cusum_params     : dict of {variable: {K, H}}
            lab_scale        : boolean (or use lab_scale param)

        Example
        -------
            buf = StateBuffer.from_config("site_config.json", lab_scale=True)
        """
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(
                f"site_config.json not found at {path}. "
                "Create one or use StateBuffer() with explicit arguments."
            )
        cfg = json.loads(path.read_text())
        use_lab_scale = lab_scale or cfg.get("lab_scale", False)
        return cls(
            db_path=cfg.get("db_path", "plant_state.sqlite"),
            retention_hours=cfg.get("retention_hours", 48),
            cusum_params=cfg.get("cusum_params"),
            lab_scale=use_lab_scale,
        )

    # ── Schema initialisation ──────────────────────────────────────────────────
    def _initialize_db(self):
        """Create tables and seed CUSUM state rows for all monitored variables."""
        cur = self.conn.cursor()

        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS raw_telemetry (
                ts_utc  INTEGER PRIMARY KEY,
                payload TEXT
            )
        """)

        col_defs = ",\n    ".join(f"{c} REAL" for c in PLANT_STATE_COLS)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS plant_state (
                ts_utc  INTEGER PRIMARY KEY,
                {col_defs}
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS cusum_state (
                model_tag    TEXT PRIMARY KEY,
                s_pos        REAL DEFAULT 0.0,
                s_neg        REAL DEFAULT 0.0,
                rolling_mean REAL
            )
        """)
        self.conn.commit()

        for tag in self.cusum_params:
            cur.execute("""
                INSERT OR IGNORE INTO cusum_state
                    (model_tag, s_pos, s_neg, rolling_mean)
                VALUES (?, 0.0, 0.0, NULL)
            """, (tag,))
        self.conn.commit()

    # ── CUSUM anomaly detection ────────────────────────────────────────────────
    def _apply_cusum(
        self,
        model_tag: str,
        current_val: float,
        cursor: sqlite3.Cursor,
    ) -> Optional[float]:
        """
        Apply one-sided CUSUM control chart to a single sensor reading.

        Returns current_val if within tolerance, None if a spike is detected.
        Variables not listed in cusum_params pass through unchanged
        (e.g. manual lab results like fos_mg_per_l).

        Algorithm
        ---------
        S+ = max(0, S+ + x - (mu + K))   # detects upward shifts
        S- = max(0, S- + (mu - K) - x)   # detects downward shifts
        If S+ > H or S- > H: anomaly → reset accumulators, discard reading.
        Rolling mean updated with EMA (alpha=0.05) on accepted readings.
        """
        if model_tag not in self.cusum_params:
            return current_val

        params = self.cusum_params[model_tag]
        K, H = params["K"], params["H"]

        cursor.execute(
            "SELECT s_pos, s_neg, rolling_mean FROM cusum_state WHERE model_tag = ?",
            (model_tag,),
        )
        row = cursor.fetchone()
        if row is None:
            # Tag appeared after init — seed it now
            cursor.execute(
                "INSERT INTO cusum_state (model_tag, s_pos, s_neg, rolling_mean) "
                "VALUES (?, 0.0, 0.0, ?)",
                (model_tag, current_val),
            )
            return current_val

        s_pos, s_neg, rolling_mean = row

        # First reading — initialise rolling mean
        if rolling_mean is None:
            cursor.execute(
                "UPDATE cusum_state SET rolling_mean = ? WHERE model_tag = ?",
                (current_val, model_tag),
            )
            return current_val

        s_pos = max(0.0, s_pos + current_val - (rolling_mean + K))
        s_neg = max(0.0, s_neg + (rolling_mean - K) - current_val)

        if s_pos > H or s_neg > H:
            # Spike detected — reset accumulators, discard reading
            cursor.execute(
                "UPDATE cusum_state SET s_pos = 0.0, s_neg = 0.0 WHERE model_tag = ?",
                (model_tag,),
            )
            return None

        # Accepted — update EMA and accumulators
        new_mean = 0.05 * current_val + 0.95 * rolling_mean
        cursor.execute(
            "UPDATE cusum_state SET s_pos = ?, s_neg = ?, rolling_mean = ? "
            "WHERE model_tag = ?",
            (s_pos, s_neg, new_mean, model_tag),
        )
        return current_val

    # ── Public insert methods ──────────────────────────────────────────────────
    def insert_live_data(self, mapped_data: Dict[str, float], timestamp: Optional[float] = None):
        """
        Ingest a pre-mapped sensor reading into the buffer.

        mapped_data must use internal variable names (the output of
        scada_mapper.auto_map_dataframe), NOT raw SCADA tags.

        Args
        ----
        mapped_data : Dict[str, float]
            Sensor readings with internal variable names as keys.
        timestamp : Optional[float]
            Unix timestamp for this reading. If None, uses current time.
            For replay scenarios, pass the historical date as a Unix timestamp
            so that the retention window works correctly.

        Example
        -------
            mapping = auto_map_dataframe(df, vendor)   # scada_mapper
            buf.insert_live_data({
                "digester_temp_c": 36.8,
                "ch4_pct": 62.1,
                "biogas_flow_nm3h": 45.2,
            }, timestamp=pd.Timestamp("2024-01-15").timestamp())

        Flow
        ----
        1. Store raw payload in raw_telemetry (audit trail).
        2. Apply CUSUM per variable — spikes are discarded (None).
        3. Write accepted values to plant_state (NULL for rejected/absent).
        4. Purge records older than retention window.
        """
        if timestamp is None:
            timestamp = int(time.time())
        current_ts = int(timestamp)
        cursor = self.conn.cursor()

        cursor.execute(
            "INSERT OR REPLACE INTO raw_telemetry (ts_utc, payload) VALUES (?, ?)",
            (current_ts, json.dumps(mapped_data)),
        )

        validated: Dict[str, Optional[float]] = {}
        for model_tag, val in mapped_data.items():
            if model_tag in PLANT_STATE_COLS:
                validated[model_tag] = self._apply_cusum(model_tag, val, cursor)

        row_vals = [validated.get(c) for c in PLANT_STATE_COLS]
        cursor.execute(
            f"INSERT OR REPLACE INTO plant_state "
            f"(ts_utc, {', '.join(PLANT_STATE_COLS)}) "
            f"VALUES (?, {', '.join(['?'] * len(PLANT_STATE_COLS))})",
            [current_ts] + row_vals,
        )

        self._purge_old_records(current_ts, cursor)
        self.conn.commit()

    def insert_manual_fostac(self, fos_mg_per_l: float):
        """
        Record a manual FOS/TAC titration result.

        Called when weekly lab results arrive. Stored as a sparse row
        (all other columns NULL). No CUSUM applied — lab values are
        assumed accurate and are used directly by the EnKF to tighten
        the S2 estimate.

        Args
        ----
        fos_mg_per_l : FOS/TAC result in mg CaCO3/L
        """
        current_ts = int(time.time())
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO plant_state (ts_utc, fos_mg_per_l) VALUES (?, ?)",
            (current_ts, fos_mg_per_l),
        )
        self.conn.commit()

    # ── Query methods ──────────────────────────────────────────────────────────
    def get_model_dataframe(self) -> pd.DataFrame:
        """
        Return the full plant state history as a DataFrame for EnKF / AD4.

        Index   : UTC datetime
        Columns : all PLANT_STATE_COLS
        Gaps    : forward-filled (EnKF tolerates missing readings)

        Returns an empty DataFrame (not None) if no rows exist yet.
        """
        df = pd.read_sql_query(
            "SELECT * FROM plant_state ORDER BY ts_utc ASC",
            self.conn,
            index_col="ts_utc",
        )
        if df.empty:
            return df
        df.index = pd.to_datetime(df.index, unit="s", utc=True)
        df.ffill(inplace=True)
        return df

    def get_latest(self) -> Optional[Dict[str, Optional[float]]]:
        """
        Return the most recent plant state row as a dict, or None if empty.

        Useful for MCP get_plant_state to read the current clean state.
        """
        df = self.get_model_dataframe()
        if df.empty:
            return None
        row = df.iloc[-1]
        return {col: (None if pd.isna(row[col]) else float(row[col]))
                for col in row.index}

    def get_cusum_status(self) -> Dict[str, Dict]:
        """
        Return current CUSUM accumulator state for all monitored variables.

        Useful for diagnostics: high s_pos or s_neg values indicate a
        sensor that is drifting toward the anomaly threshold.
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT model_tag, s_pos, s_neg, rolling_mean FROM cusum_state")
        return {
            row[0]: {
                "s_pos": round(row[1], 4),
                "s_neg": round(row[2], 4),
                "rolling_mean": round(row[3], 4) if row[3] is not None else None,
                "H": self.cusum_params.get(row[0], {}).get("H"),
            }
            for row in cursor.fetchall()
        }

    # ── Housekeeping ──────────────────────────────────────────────────────────
    def _purge_old_records(self, current_ts: int, cursor: sqlite3.Cursor):
        """Delete records older than the retention window."""
        cutoff = current_ts - self.retention_seconds
        cursor.execute("DELETE FROM raw_telemetry WHERE ts_utc < ?", (cutoff,))
        cursor.execute("DELETE FROM plant_state   WHERE ts_utc < ?", (cutoff,))

    def close(self):
        """Close the SQLite connection."""
        self.conn.close()
