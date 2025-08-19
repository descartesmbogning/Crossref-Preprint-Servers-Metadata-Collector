# streamlit_crossref_preprints_app.py
"""
Crossref Preprint Servers — Metadata Collector (no trends)
==========================================================

What this app does
------------------
1) Lets you provide preprint server names (CSV upload or paste).
2) Resolves each name to Crossref candidates using multiple strategies:
   ISSN → DOI prefix → Member ID → Title.
3) You choose the correct match(es) for each server.
4) (NEW) Preview a single sample preprint (raw JSON) for any selected server/candidate.
5) Fetches presence counts and (optionally) samples N preprints (raw JSON).
6) Downloads a ZIP with:
   - servers.csv (presence + chosen IDs + short summary)
   - json/selection_summary.json
   - json/<server_slug>/journal.json (when ISSN resolution is used)
   - json/<server_slug>/sample_preprints/*.json

Notes
-----
- Crossref appreciates a contact email in requests (we append `mailto=`).
- We filter preprints as `type=posted-content`.
- No yearly/monthly trend files in this Crossref app.
"""

import io
import json
import time
import zipfile
import random
from datetime import datetime
from typing import Dict, List, Optional, Any, Set, Tuple
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

# ──────────────────────────────────────────────────────────────────────────────
# App config & constants
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Crossref Preprint Servers — Metadata Collector",
    page_icon="🗂️",
    layout="wide",
)
CROSSREF_BASE = "https://api.crossref.org"

# ──────────────────────────────────────────────────────────────────────────────
# THEME: runtime Light/Dark/Auto + Accent color (simple CSS)
# ──────────────────────────────────────────────────────────────────────────────
def apply_runtime_theme(mode: str, accent: str):
    is_dark = (mode == "Dark") or (mode == "Auto" and st.get_option("theme.base") == "dark")
    bg = "#0E1117" if is_dark else "#FFFFFF"
    text = "#FAFAFA" if is_dark else "#111111"
    subtle = "#161b22" if is_dark else "#f6f8fa"
    st.markdown(
        f"""
        <style>
        :root {{
          --acc: {accent};
          --bg: {bg};
          --text: {text};
          --subtle: {subtle};
        }}
        .stApp {{ background: var(--bg); color: var(--text); }}
        .stButton>button, .stDownloadButton>button {{
          border-radius: 10px; border: 1px solid var(--acc); color: var(--text); background: transparent;
        }}
        .stButton>button:hover, .stDownloadButton>button:hover {{ background: var(--acc); color: #fff; }}
        .stProgress > div > div > div > div {{ background-color: var(--acc) !important; }}
        .stExpander > div > div {{ border-bottom: 1px solid var(--acc); }}
        .stMetric label, .stMetric small {{ color: var(--text) !important; }}
        </style>
        """,
        unsafe_allow_html=True,
    )

# ──────────────────────────────────────────────────────────────────────────────
# Helpers (HTTP + small utils)
# ──────────────────────────────────────────────────────────────────────────────
def api_get(url: str, sleep_s: float, max_retries: int = 5, mailto: Optional[str] = None) -> requests.Response:
    """GET with polite retry/backoff. Adds mailto param (Crossref-friendly)."""
    headers = {"User-Agent": f"CrossrefStreamlitApp/1.0 (+{mailto or 'mailto:unknown@example.com'})"}
    if mailto:
        url += ("&" if "?" in url else "?") + f"mailto={quote(mailto)}"
    backoff = sleep_s
    for _ in range(max_retries):
        r = requests.get(url, headers=headers, timeout=60)
        if r.status_code == 200:
            if sleep_s > 0:
                time.sleep(sleep_s)
            return r
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(backoff); backoff *= 1.6; continue
        r.raise_for_status()
    r.raise_for_status()
    return r

def norm(s: str) -> str:
    return " ".join((s or "").strip().split())

def to_slug(s: str) -> str:
    return "-".join(norm(s).lower().split()) or "server"

def safe_list(s: str) -> List[str]:
    if not s:
        return []
    parts = [norm(x) for x in str(s).split(";")]
    return [p for p in parts if p]

# ──────────────────────────────────────────────────────────────────────────────
# Crossref resolution strategies
# ──────────────────────────────────────────────────────────────────────────────
def cr_total_from_works(filters: List[str], mailto: Optional[str], sleep_s: float) -> int:
    qs = []
    if filters:
        qs.append("filter=" + quote(",".join(filters)))
    qs.append("rows=1")  # we just need total-results
    url = f"{CROSSREF_BASE}/works?" + "&".join(qs)
    r = api_get(url, sleep_s=sleep_s, mailto=mailto)
    return int(r.json().get("message", {}).get("total-results", 0))

def resolve_by_issn(issn: str, mailto: Optional[str], sleep_s: float) -> Optional[Dict[str, Any]]:
    """Return a candidate dict via ISSN + preprint total-results using works."""
    try:
        jr = api_get(f"{CROSSREF_BASE}/journals/{quote(issn)}", sleep_s=sleep_s, mailto=mailto).json()
        jmsg = jr.get("message", {}) or {}
    except Exception:
        return None
    total = cr_total_from_works([f"issn:{issn}", "type:posted-content"], mailto, sleep_s)
    title = jmsg.get("title") or issn
    return {
        "strategy": "issn",
        "id": issn,
        "label": f"{title} — ISSN:{issn}",
        "estimate_total": total,
        "journal_meta": jmsg
    }

def resolve_by_prefix(prefix: str, mailto: Optional[str], sleep_s: float) -> Optional[Dict[str, Any]]:
    """Return a candidate via DOI prefix (preprint total)."""
    total = cr_total_from_works([f"prefix:{prefix}", "type:posted-content"], mailto, sleep_s)
    label = f"DOI prefix {prefix}"
    return {"strategy": "prefix", "id": prefix, "label": label, "estimate_total": total}

def resolve_by_member(member_id: str, mailto: Optional[str], sleep_s: float) -> Optional[Dict[str, Any]]:
    try:
        mr = api_get(f"{CROSSREF_BASE}/members/{quote(member_id)}", sleep_s=sleep_s, mailto=mailto).json()
        mmsg = mr.get("message", {}) or {}
        name = mmsg.get("primary-name") or mmsg.get("id") or member_id
    except Exception:
        name = member_id
        mmsg = {}
    total = cr_total_from_works([f"member:{member_id}", "type:posted-content"], mailto, sleep_s)
    return {"strategy": "member", "id": member_id, "label": f"Member {name}", "estimate_total": total, "member_meta": mmsg}

def resolve_by_title(title: str, mailto: Optional[str], sleep_s: float) -> List[Dict[str, Any]]:
    """Try journals?query=title → turn into ISSN candidates. Also fallback to container-title works search."""
    out: List[Dict[str, Any]] = []
    # Journals lookup
    try:
        jr = api_get(f"{CROSSREF_BASE}/journals?query={quote(title)}&rows=5", sleep_s=sleep_s, mailto=mailto).json()
        items = jr.get("message", {}).get("items", []) or []
        for j in items:
            t = j.get("title") or "(no title)"
            issns = j.get("ISSN", []) or []
            for issn in issns[:2]:  # keep it tight
                total = cr_total_from_works([f"issn:{issn}", "type:posted-content"], mailto, sleep_s)
                out.append({"strategy": "title→issn", "id": issn, "label": f"{t} — ISSN:{issn}", "estimate_total": total, "journal_meta": j})
    except Exception:
        pass
    # Fallback: works by container-title
    try:
        wr = api_get(
            f"{CROSSREF_BASE}/works?query.container-title={quote(title)}&filter=type:posted-content&rows=1",
            sleep_s=sleep_s, mailto=mailto
        ).json()
        total = int(wr.get("message", {}).get("total-results", 0))
        if total > 0:
            out.append({"strategy": "container-title", "id": title, "label": f'Container-title match: "{title}"', "estimate_total": total})
    except Exception:
        pass
    return out

def resolve_candidates_for_server(server_row: Dict[str, Any], per_title: int, mailto: Optional[str], sleep_s: float) -> List[Dict[str, Any]]:
    """
    Try: ISSNs → DOI prefixes → member → title_exact → title_variants.
    Return a de-duplicated candidate list.
    """
    seen = set()
    candidates: List[Dict[str, Any]] = []

    def add_cand(c: Optional[Dict[str, Any]]):
        if not c: return
        key = (c.get("strategy"), c.get("id"))
        if key in seen: return
        seen.add(key); candidates.append(c)

    # ISSNs
    for issn in (safe_list(server_row.get("issn_l")) +
                 safe_list(server_row.get("issn_print")) +
                 safe_list(server_row.get("issn_electronic"))):
        cand = resolve_by_issn(issn, mailto, sleep_s)
        add_cand(cand)

    # DOI prefixes
    for pref in safe_list(server_row.get("doi_prefixes")):
        add_cand(resolve_by_prefix(pref, mailto, sleep_s))

    # Member
    member_id = norm(server_row.get("crossref_member_id", ""))
    if member_id:
        add_cand(resolve_by_member(member_id, mailto, sleep_s))

    # Titles (exact then variants)
    title_exact = norm(server_row.get("title_exact", "")) or norm(server_row.get("server_name", ""))
    if title_exact:
        for c in resolve_by_title(title_exact, mailto, sleep_s)[:per_title]:
            add_cand(c)
    for tv in safe_list(server_row.get("title_variants"))[:per_title]:
        for c in resolve_by_title(tv, mailto, sleep_s)[:per_title]:
            add_cand(c)

    return candidates

# ──────────────────────────────────────────────────────────────────────────────
# Fetch sample preprints (raw JSON)
# ──────────────────────────────────────────────────────────────────────────────
def sample_preprints(candidate: Dict[str, Any], n: int, sort_mode: str,
                     date_from: Optional[str], date_to: Optional[str],
                     mailto: Optional[str], sleep_s: float) -> List[Dict[str, Any]]:
    """
    Fetch up to N works of type=posted-content for the chosen candidate.
    sort_mode: "latest" (published desc), "most-cited" (desc), "random" (random from first few pages)
    """
    if n <= 0:
        return []

    filters = ["type:posted-content"]
    strat, cid = candidate.get("strategy"), candidate.get("id")
    if strat in ("issn", "title→issn"):
        filters.append(f"issn:{cid}")
    elif strat == "prefix":
        filters.append(f"prefix:{cid}")
    elif strat == "member":
        filters.append(f"member:{cid}")
    elif strat == "container-title":
        # can't filter container-title in filters; use query parameter
        pass

    if date_from:
        filters.append(f"from-pub-date:{date_from}")
    if date_to:
        filters.append(f"until-pub-date:{date_to}")

    base = f"{CROSSREF_BASE}/works?"
    q = []
    if strat == "container-title":
        q.append("query.container-title=" + quote(cid))
    if filters:
        q.append("filter=" + quote(",".join(filters)))

    # Sorting
    sort_param = ""
    if sort_mode == "latest":
        sort_param = "sort=published&order=desc"
    elif sort_mode == "most-cited":
        sort_param = "sort=is-referenced-by-count&order=desc"

    # We’ll fetch a bit more for random
    rows = n if sort_mode != "random" else min(50, max(10, n * 5))
    if rows < 1:
        rows = 1
    q.append(f"rows={rows}")
    if sort_param:
        q.append(sort_param)

    url = base + "&".join(q)
    r = api_get(url, sleep_s=sleep_s, mailto=mailto)
    items = r.json().get("message", {}).get("items", []) or []

    if sort_mode == "random" and items:
        random.shuffle(items)
        items = items[:n]
    else:
        items = items[:n]

    return items

# ──────────────────────────────────────────────────────────────────────────────
# UI — Header & Sidebar
# ──────────────────────────────────────────────────────────────────────────────
st.title("🗂️ Crossref Preprint Servers — Metadata Collector")
st.caption("Resolve servers to Crossref, confirm presence, and (optionally) sample preprint records as raw JSON. No trend files.")

with st.expander("👋 Quick Start (click to open)", expanded=True):
    st.markdown(
        """
**Step 1.** Provide your server names (CSV or paste). You may include helpful IDs like ISSN, DOI prefixes, etc.  
**Step 2.** Click **Resolve** to get Crossref candidates.  
**Step 3.** Pick the correct match(es) per server.  
**Step 4.** *(Optional)* Open **Preview a sample preprint** to quickly inspect raw JSON from Crossref.  
**Step 5.** Click **Build ZIP** to save `servers.csv` and raw JSON samples.  

**Tips**
- Keep **Sample N preprints** small at first (e.g., 1–3).  
- Add a **contact email** to use Crossref politely and help with rate limits.  
- We filter by `type=posted-content` to target preprints.
        """
    )

with st.sidebar:
    st.header("🎨 Theme")
    theme_mode = st.radio("Mode", options=["Auto", "Light", "Dark"], index=0, horizontal=True)
    accent_color = st.color_picker("Accent color", value="#6C63FF")
    apply_runtime_theme(theme_mode, accent_color)

with st.sidebar:
    st.header("⚙️ Options")
    mailto = st.text_input("Contact email (recommended)", value="", help="Appended as mailto=… in Crossref calls.")
    sleep_s = st.number_input("Sleep between API calls (sec)", min_value=0.0, max_value=3.0, value=0.5, step=0.1)
    per_title = st.slider("Max title-based candidates per title variant", 0, 5, 2)
    sample_n = st.number_input("Sample N preprints per selected server", min_value=0, max_value=50, value=1, step=1)
    sort_mode = st.selectbox("Sampling mode", ["latest", "most-cited", "random"], index=0,
                             help="How we choose the sample preprints for each server.")
    date_from = st.text_input("from-pub-date (YYYY-MM-DD, optional)", value="")
    date_to = st.text_input("until-pub-date (YYYY-MM-DD, optional)", value="")
    show_logs = st.checkbox("Show verbose logs", value=False, help="Uncheck to keep the UI clean.")
    st.markdown("---")
    st.markdown("**Input methods**")
    uploaded_csv = st.file_uploader("Upload CSV (first column is server_name)", type=["csv"])
    manual_input = st.text_area("Or paste names (one per line)")

    # Template CSV
    template_csv = (
        "server_name,issn_l,issn_print,issn_electronic,doi_prefixes,crossref_member_id,title_exact,title_variants,notes\n"
        "bioRxiv,,,,10.1101,,bioRxiv,,\n"
        "medRxiv,,,,10.1101,,medRxiv,,\n"
        "Research Square,,,,10.21203,,,Research Square;\n"
    )
    st.download_button("Download CSV template", template_csv.encode("utf-8"), "crossref_servers_template.csv", "text/csv")

# ──────────────────────────────────────────────────────────────────────────────
# STEP 1: Parse inputs
# ──────────────────────────────────────────────────────────────────────────────
input_rows: List[Dict[str, Any]] = []

if uploaded_csv is not None:
    try:
        df = pd.read_csv(uploaded_csv, dtype=str).fillna("")
        if df.empty:
            st.warning("Uploaded CSV looks empty.")
        else:
            expected = ["server_name","issn_l","issn_print","issn_electronic","doi_prefixes",
                        "crossref_member_id","title_exact","title_variants","notes"]
            for col in expected:
                if col not in df.columns:
                    df[col] = ""
            for _, r in df[expected].iterrows():
                input_rows.append({k: str(r[k]) for k in expected})
    except Exception as e:
        st.error(f"Failed to read CSV: {e}")

if manual_input.strip():
    for line in manual_input.splitlines():
        name = norm(line)
        if name:
            input_rows.append({
                "server_name": name,
                "issn_l": "", "issn_print": "", "issn_electronic": "",
                "doi_prefixes": "", "crossref_member_id": "",
                "title_exact": name, "title_variants": "", "notes": ""
            })

# de-dup by server_name while preserving order
seen_names = set()
dedup_rows = []
for r in input_rows:
    nm = r.get("server_name", "")
    if nm and nm not in seen_names:
        seen_names.add(nm); dedup_rows.append(r)
input_rows = dedup_rows

st.subheader("1) Servers detected")
if input_rows:
    st.success(f"Loaded **{len(input_rows)}** server(s).")
    st.code("\n".join([r["server_name"] for r in input_rows[:50]]) + ("\n..." if len(input_rows) > 50 else ""))
else:
    st.info("Upload a CSV or paste names to begin.")

# ──────────────────────────────────────────────────────────────────────────────
# STEP 2: Resolve candidates (with chooser)
# ──────────────────────────────────────────────────────────────────────────────
if "cr_candidates" not in st.session_state:
    st.session_state.cr_candidates = {}     # server_name -> [candidate dicts]
if "cr_selected" not in st.session_state:
    st.session_state.cr_selected = {}       # server_name -> [chosen (strategy,id) pairs]
if "log_lines" not in st.session_state:
    st.session_state.log_lines = []

def log_line(msg: str, box: Optional[st.delta_generator.DeltaGenerator] = None):
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.log_lines.append(f"[{ts}] {msg}")
    st.session_state.log_lines = st.session_state.log_lines[-400:]
    if box is not None:
        box.code("\n".join(st.session_state.log_lines), language=None)

st.markdown("### 2) Resolve servers to Crossref candidates")
st.write("We’ll try ISSN → DOI prefix → Member → Title. You can then pick the correct one(s).")

resolve_btn = st.button("🔍 Resolve", disabled=not input_rows)
if resolve_btn:
    st.session_state.cr_candidates = {}
    st.session_state.cr_selected = {}
    progress = st.progress(0)
    log_box = st.empty() if show_logs else None
    for i, row in enumerate(input_rows, start=1):
        name = row.get("server_name", "")
        try:
            cands = resolve_candidates_for_server(row, per_title=per_title, mailto=mailto or None, sleep_s=float(sleep_s))
            st.session_state.cr_candidates[name] = cands
            st.session_state.cr_selected[name] = []
            log_line(f"Resolved '{name}' → {len(cands)} candidate(s).", log_box)
        except Exception as e:
            st.warning(f"Resolution failed for '{name}': {e}")
            st.session_state.cr_candidates[name] = []
            st.session_state.cr_selected[name] = []
            log_line(f"Resolution failed for '{name}': {e}", log_box)
        progress.progress(i / len(input_rows))
    st.success("Resolution complete. Review candidates below.")

# Candidate chooser UI
if st.session_state.cr_candidates:
    st.subheader("2) Review matches and select")
    with st.expander("Open candidate lists", expanded=True):
        # Global select-all
        if st.button("Select ALL candidates for ALL servers", key="select_all_global"):
            for nm, cands in st.session_state.cr_candidates.items():
                st.session_state.cr_selected[nm] = [(c["strategy"], c["id"]) for c in cands]
            st.toast("All candidates selected.")

        for row in input_rows:
            name = row["server_name"]
            cands = st.session_state.cr_candidates.get(name, [])
            if not cands:
                st.warning(f"No candidates for: {name}")
                continue

            # Build human labels & preselect current picks
            labels = []
            value_map: Dict[str, Tuple[str, str]] = {}
            for c in cands:
                lab = f"{c.get('label','(no label)')} — total≈{c.get('estimate_total',0)} — [{c.get('strategy')}]"
                labels.append(lab)
                value_map[lab] = (c.get("strategy",""), c.get("id",""))

            current_pairs = st.session_state.cr_selected.get(name, [])
            current_labels = [lbl for lbl, pair in ((lab, value_map[lab]) for lab in labels) if pair in current_pairs]

            cols = st.columns([4,1])
            with cols[0]:
                chosen_labels = st.multiselect(
                    f"**{name}** — select candidate(s)",
                    options=labels,
                    default=current_labels,
                    key=f"sel_{name}",
                )
            with cols[1]:
                if st.button("Select all", key=f"selectall_btn_{name}"):
                    st.session_state.cr_selected[name] = [value_map[lbl] for lbl in labels]
                    st.rerun()

            st.session_state.cr_selected[name] = [value_map[lbl] for lbl in chosen_labels if lbl in value_map]

# ──────────────────────────────────────────────────────────────────────────────
# (NEW) STEP 2.5: Preview a sample preprint (raw JSON, on demand)
# ──────────────────────────────────────────────────────────────────────────────
if st.session_state.cr_candidates:
    with st.expander("🔎 Preview a sample preprint (raw JSON)", expanded=False):
        st.write("Pick a server and one of its candidates, then fetch a single `posted-content` item to inspect the raw Crossref JSON.")

        # Servers that have candidates
        servers_with_cands = [r["server_name"] for r in input_rows if st.session_state.cr_candidates.get(r["server_name"])]
        if not servers_with_cands:
            st.info("Resolve servers first to enable preview.")
        else:
            sel_server = st.selectbox("Server", servers_with_cands, key="preview_server")

            # Build label → candidate mapping for this server
            cands = st.session_state.cr_candidates.get(sel_server, [])
            cand_labels = []
            cand_by_label: Dict[str, Dict[str,Any]] = {}
            for c in cands:
                lab = f"{c.get('label','(no label)')} — total≈{c.get('estimate_total',0)} — [{c.get('strategy')}]"
                cand_labels.append(lab)
                cand_by_label[lab] = c

            # Default to first selected candidate (if any), otherwise first candidate
            default_label = None
            chosen_pairs = st.session_state.cr_selected.get(sel_server, [])
            if chosen_pairs:
                # find label matching first chosen pair
                strat0, id0 = chosen_pairs[0]
                for lab, cand in cand_by_label.items():
                    if cand.get("strategy")==strat0 and cand.get("id")==id0:
                        default_label = lab; break
            if default_label is None and cand_labels:
                default_label = cand_labels[0]

            sel_label = st.selectbox("Candidate", cand_labels, index=cand_labels.index(default_label) if default_label in cand_labels else 0, key="preview_candidate")

            # Allow overriding preview-specific params (optional)
            colA, colB, colC = st.columns([1,1,1])
            with colA:
                preview_sort = st.selectbox("Sort", ["latest","most-cited","random"], index=["latest","most-cited","random"].index(sort_mode), key="preview_sort")
            with colB:
                preview_from = st.text_input("from-pub-date (optional)", value=date_from, key="preview_from")
            with colC:
                preview_to = st.text_input("until-pub-date (optional)", value=date_to, key="preview_to")

            if st.button("Fetch 1 sample", key="btn_preview_fetch"):
                cand = cand_by_label.get(sel_label)
                try:
                    items = sample_preprints(
                        candidate=cand, n=1, sort_mode=preview_sort,
                        date_from=norm(preview_from) or None, date_to=norm(preview_to) or None,
                        mailto=mailto or None, sleep_s=float(sleep_s)
                    )
                    if not items:
                        st.warning("No preprint found for this selection with current filters.")
                    else:
                        st.success("Sample fetched.")
                        st.json(items[0])
                        # Offer quick download of this one JSON
                        pretty = json.dumps(items[0], ensure_ascii=False, indent=2)
                        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
                        st.download_button(
                            "⬇️ Download this JSON",
                            data=pretty.encode("utf-8"),
                            file_name=f"crossref_sample_{to_slug(sel_server)}_{ts}.json",
                            mime="application/json"
                        )
                except Exception as e:
                    st.error(f"Preview failed: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# STEP 3: Build ZIP (servers.csv + raw JSON samples)
# ──────────────────────────────────────────────────────────────────────────────
def build_zip_crossref(cr_selected: Dict[str, List[Tuple[str,str]]],
                       cr_candidates: Dict[str, List[Dict[str,Any]]],
                       original_rows: List[Dict[str,Any]],
                       sample_n: int, sort_mode: str,
                       date_from: Optional[str], date_to: Optional[str],
                       mailto: Optional[str], sleep_s: float,
                       show_logs_flag: bool = False) -> Tuple[bytes, pd.DataFrame]:
    """
    Build outputs & return (zip_bytes, servers_df).
    servers.csv columns:
      server_name, presence_in_crossref, matched_strategy, matched_ids, total_results_estimate,
      sample_count_saved, notes
    """
    # Quick maps
    cand_lookup: Dict[Tuple[str,str], Dict[str,Any]] = {}
    for c_list in cr_candidates.values():
        for c in c_list:
            cand_lookup[(c.get("strategy"), c.get("id"))] = c

    # Prepare servers.csv rows
    servers_rows = []
    total_servers = len(original_rows)
    processed = 0
    overall_prog = st.progress(0)
    compact_status = st.empty()

    # Build ZIP in memory
    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED)

    # selection summary
    zf.writestr("json/selection_summary.json", json.dumps({
        "date": datetime.now().isoformat(),
        "sample_n": sample_n, "sort_mode": sort_mode,
        "date_from": date_from, "date_to": date_to,
    }, ensure_ascii=False, indent=2))

    for row in original_rows:
        name = row["server_name"]
        slug = to_slug(name)
        chosen_pairs = cr_selected.get(name, [])
        if not chosen_pairs:
            # nothing selected → mark not found
            servers_rows.append({
                "server_name": name,
                "presence_in_crossref": "no",
                "matched_strategy": "",
                "matched_ids": "",
                "total_results_estimate": 0,
                "sample_count_saved": 0,
                "notes": row.get("notes","")
            })
            processed += 1
            overall_prog.progress(processed/total_servers)
            if show_logs_flag:
                compact_status.text(f"Processed {processed}/{total_servers}")
            continue

        # Combine totals & run sampling
        strategies = []
        ids = []
        total_estimate = 0
        sample_saved = 0

        for strat, cid in chosen_pairs:
            strategies.append(strat); ids.append(cid)
            cand = cand_lookup.get((strat, cid))
            if not cand:
                continue
            total_estimate += int(cand.get("estimate_total", 0))

            # Save journal.json if present
            if cand.get("journal_meta"):
                zf.writestr(f"json/{slug}/journal.json", json.dumps(cand["journal_meta"], ensure_ascii=False, indent=2))

            # Sample works (raw JSON)
            if sample_n > 0:
                works = sample_preprints(
                    candidate=cand, n=sample_n, sort_mode=sort_mode,
                    date_from=norm(date_from) or None, date_to=norm(date_to) or None,
                    mailto=mailto, sleep_s=sleep_s
                )
                for idx, w in enumerate(works, start=1):
                    zf.writestr(f"json/{slug}/sample_preprints/doc_{strat}_{cid}_{idx}.json",
                                json.dumps(w, ensure_ascii=False, indent=2))
                sample_saved += len(works)

        servers_rows.append({
            "server_name": name,
            "presence_in_crossref": "yes" if total_estimate > 0 else "no",
            "matched_strategy": ";".join(strategies),
            "matched_ids": ";".join(ids),
            "total_results_estimate": total_estimate,
            "sample_count_saved": sample_saved,
            "notes": row.get("notes","")
        })

        processed += 1
        overall_prog.progress(processed/total_servers)
        if show_logs_flag:
            compact_status.text(f"Processed {processed}/{total_servers}")

    # finalize CSV
    servers_df = pd.DataFrame(servers_rows, columns=[
        "server_name","presence_in_crossref","matched_strategy","matched_ids",
        "total_results_estimate","sample_count_saved","notes"
    ])
    zf.writestr("servers.csv", servers_df.to_csv(index=False))
    zf.close()
    buf.seek(0)
    return buf.read(), servers_df

# Build & Download UI
st.markdown("### 3) Build & Download")
st.write("Click **Build ZIP** to generate `servers.csv` and raw JSON samples (if enabled).")

if show_logs:
    with st.expander("📜 Logs", expanded=False):
        log_box = st.empty()
        log_box.code("Logs will appear here…", language=None)

run_btn = st.button("🚀 Build ZIP", disabled=not bool(st.session_state.cr_candidates))
if run_btn:
    try:
        zip_bytes, servers_df = build_zip_crossref(
            cr_selected=st.session_state.cr_selected,
            cr_candidates=st.session_state.cr_candidates,
            original_rows=input_rows,
            sample_n=int(sample_n),
            sort_mode=sort_mode,
            date_from=norm(date_from) or None,
            date_to=norm(date_to) or None,
            mailto=mailto or None,
            sleep_s=float(sleep_s),
            show_logs_flag=show_logs,
        )
        st.success("✅ Build complete! Preview below and download your ZIP.")
        st.markdown("#### Preview: servers.csv")
        st.dataframe(servers_df, use_container_width=True, height=300)

        # Time-stamped ZIP filename
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        st.download_button(
            "⬇️ Download results ZIP",
            data=zip_bytes,
            file_name=f"crossref_preprint_servers_results_{ts}.zip",
            mime="application/zip",
        )
    except Exception as e:
        st.error(f"Build failed: {e}")

st.markdown("---")
st.markdown(
    """
**What counts as “present in Crossref”?**  
If any selected candidate (ISSN, DOI prefix, Member, Title) returns `total-results > 0` for `type=posted-content`,
we mark the server as present.  

**Smart tips**  
- If you suspect a server deposits with **DataCite** (not Crossref), try DOI prefixes like `10.5281`, `10.5061`, etc.  
- Use **small sampling (N=1–3)** just to verify structure and fields; scale up later.  
- Add **date filters** if you only want recent preprints.
"""
)
