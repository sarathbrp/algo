"""
Local config dashboard: view summary and edit config YAML.

Run: `make dashboard` or `streamlit run dashboard/app.py` from repo root.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import streamlit as st
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "default.yaml"
LOGO_PATH = PROJECT_ROOT / "assets" / "algosphere-logo.svg"


def _resolve_path(s: str) -> Path:
    p = Path(s.strip()).expanduser()
    return (PROJECT_ROOT / p).resolve() if not p.is_absolute() else p.resolve()


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_config(text: str) -> tuple[dict | None, str | None]:
    try:
        data = yaml.safe_load(text)
        if data is None:
            return {}, None
        if not isinstance(data, dict):
            return None, "Root must be a YAML mapping (object)."
        return data, None
    except yaml.YAMLError as e:
        return None, str(e)


def save_with_backup(path: Path, text: str) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bak: Path | None = None
    if path.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = path.parent / f"{path.name}.bak.{ts}"
        shutil.copy2(path, bak)
    path.write_text(text, encoding="utf-8")
    return bak


def main() -> None:
    st.set_page_config(
        page_title="AlgoSphere · Config",
        page_icon="🌐",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    hero = st.columns([1, 5])
    with hero[0]:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=72)
        else:
            st.markdown("### 🌐")
    with hero[1]:
        st.title("AlgoSphere")
        st.caption("Trading configuration · local dashboard")

    st.caption(
        "Runs on **localhost** by default. Do not expose without authentication. "
        "Saving creates a timestamped `.bak.*` copy next to the file."
    )

    if "config_file" not in st.session_state:
        st.session_state.config_file = str(DEFAULT_CONFIG)
    if "yaml_ta" not in st.session_state:
        p = Path(st.session_state.config_file)
        st.session_state.yaml_ta = load_text(p) if p.exists() else ""

    with st.sidebar:
        st.subheader("Config file")
        path_str = st.text_input("Path", value=st.session_state.config_file, key="path_str_field")
        b1, b2 = st.columns(2)
        with b1:
            if st.button("Reload", use_container_width=True):
                p = _resolve_path(path_str)
                st.session_state.config_file = str(p)
                st.session_state.yaml_ta = load_text(p) if p.exists() else ""
                st.session_state.pop("yaml_editor", None)
                st.rerun()
        with b2:
            if st.button("Use default", use_container_width=True):
                st.session_state.config_file = str(DEFAULT_CONFIG)
                st.session_state.yaml_ta = (
                    load_text(DEFAULT_CONFIG) if DEFAULT_CONFIG.exists() else ""
                )
                st.session_state.pop("yaml_editor", None)
                st.rerun()
        st.divider()
        st.markdown("**Shortcuts:** `make dashboard` · `./bin/algo dashboard`")

    cfg_path = _resolve_path(path_str)
    st.session_state.config_file = str(cfg_path)

    tab_overview, tab_edit, tab_dl = st.tabs(["Overview", "Edit YAML", "Download"])

    data, parse_err = parse_config(st.session_state.yaml_ta)

    with tab_overview:
        if parse_err:
            st.error("Fix YAML in the **Edit YAML** tab:\n```\n" + parse_err + "\n```")
        elif data is not None:
            u = data.get("universe") or {}
            br = data.get("broker") or {}
            opt = data.get("options") or {}
            strat = data.get("strategy") or {}
            ps = data.get("position_sizing") or {}
            news = data.get("news_sentiment") or {}

            r1 = st.columns(4)
            r1[0].metric("Paper trading", "Yes" if br.get("paper", True) else "LIVE")
            r1[1].metric("Options", "On" if opt.get("enabled") else "Off")
            r1[2].metric("News sentiment", "On" if news.get("enabled") else "Off")
            r1[3].metric("Universe size", len(u.get("symbols") or []))

            r2 = st.columns(4)
            r2[0].metric(
                "Regime (min % >50D MA)",
                f"{float(u.get('regime_min_pct_above_50d_ma', 0)) * 100:.0f}%",
            )
            r2[1].metric("Risk / trade %", str(ps.get("risk_per_trade_pct", "—")))
            r2[2].metric("Strategy focus", str(strat.get("player_focus", "—")))
            be = u.get("bear_etfs") or {}
            r2[3].metric("Bear ETF cap % equity", str(be.get("max_exposure_pct_equity", "—")))

            with st.expander("Universe symbols"):
                st.code(", ".join(u.get("symbols") or []) or "(none)", language=None)

            with st.expander("Options (parsed)"):
                st.json(opt or {})

            with st.expander("Full config (JSON)"):
                st.json(data)

    with tab_edit:
        edited = st.text_area(
            "YAML",
            value=st.session_state.yaml_ta,
            height=520,
            key="yaml_editor",
            label_visibility="collapsed",
        )
        st.session_state.yaml_ta = edited
        c1, c2, _ = st.columns([1, 1, 2])
        with c1:
            check = st.button("Validate", use_container_width=True)
        with c2:
            save_btn = st.button("Save (backup first)", type="primary", use_container_width=True)

        if check:
            _, e = parse_config(st.session_state.yaml_ta)
            if e:
                st.error("```\n" + e + "\n```")
            else:
                st.success("YAML is valid.")

        if save_btn:
            _, e = parse_config(st.session_state.yaml_ta)
            if e:
                st.error("Fix errors before saving.")
            else:
                try:
                    bak = save_with_backup(cfg_path, st.session_state.yaml_ta)
                    st.success(f"Wrote `{cfg_path}`")
                    if bak is not None:
                        st.info(f"Backup: `{bak.name}`")
                except OSError as ex:
                    st.error(str(ex))

    with tab_dl:
        st.download_button(
            "Download current YAML",
            data=st.session_state.yaml_ta,
            file_name=cfg_path.name or "config.yaml",
            mime="text/yaml",
        )


if __name__ == "__main__":
    main()
