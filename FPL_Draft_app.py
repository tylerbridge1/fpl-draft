import difflib
import io
import json
import html as _html
import logging
import os
import re
import time
import unicodedata
import warnings
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor
from math import erf, factorial

import numpy as np
import pandas as pd
import requests
import streamlit as st

logging.getLogger("streamlit.runtime.scriptrunner.script_runner").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

st.set_page_config(page_title="FPL Hog Statistician", page_icon="🐷", layout="wide")
_PURPLE = "#37003C"
st.markdown(f"""<style>
footer {{visibility: hidden;}}
.block-container {{ padding-top: 3.6rem; padding-bottom: 3rem; max-width: 1400px; }}
h2 {{ font-size: 1.15rem !important; font-weight: 700 !important; }} h3 {{ font-size: 1rem !important; font-weight: 700 !important; }}
[data-testid="stMetricValue"] {{ font-size: 1.3rem !important; font-weight: 700; }}
[data-testid="stMetricLabel"] p {{ font-size: .75rem !important; opacity: .8; }}
.stButton > button, [data-testid^="stBaseButton"] {{ border-radius: 999px !important; font-weight: 600 !important; }}
.stButton > button[kind="primary"], [data-testid="stBaseButton-primary"] {{ background: linear-gradient(90deg,#04F5FF,#00FF87) !important;
   color: {_PURPLE} !important; border: none !important; }}
.stButton > button[kind="secondary"], [data-testid="stBaseButton-secondary"] {{ background: transparent !important; color: inherit !important;
   border: 1px solid rgba(150,60,255,.55) !important; }}
.stButton > button[kind="secondary"]:hover, [data-testid="stBaseButton-secondary"]:hover {{ border-color: #00FF87 !important; }}
.hog-banner {{ display: flex; align-items: center; gap: 14px; padding: 12px 18px; margin: 0 0 12px; border-radius: 14px; color: #fff;
   background: linear-gradient(100deg, #37003C 0%, #580f68 55%, #963CFF 100%); box-shadow: 0 4px 14px rgba(55,0,60,.25);
   border-bottom: 4px solid transparent; border-image: linear-gradient(90deg,#04F5FF,#00FF87) 1; }}
.hog-banner .hog-title {{ font-size: 1.45rem; font-weight: 800; color: #fff !important; line-height: 1.1; letter-spacing: -.01em; }}
.hog-banner .hog-sub {{ font-size: .82rem; color: rgba(255,255,255,.82) !important; margin-top: 2px; }}
.hog-banner .hog-chip {{ margin-left: auto; text-align: right; font-size: .78rem; color: rgba(255,255,255,.9); line-height: 1.35; }}
.hog-banner .hog-chip b {{ color: #00FF87; font-size: .95rem; }}
@media (max-width: 640px) {{ .block-container {{ padding: 3.4rem .7rem 3rem !important; }} .hog-banner .hog-title {{ font-size: 1.15rem; }}
  .hog-banner .hog-chip {{ display: none; }} input, textarea, select {{ font-size: 16px !important; }} }}
</style>""", unsafe_allow_html=True)
PIG_SVG = ("<svg width='40' height='40' viewBox='0 0 64 64' aria-hidden='true'><circle cx='32' cy='35' r='22' fill='#F6A5C0'/>"
           "<path d='M13 22 L11 7 L25 15 Z' fill='#EC8DB0'/><path d='M51 22 L53 7 L39 15 Z' fill='#EC8DB0'/><ellipse cx='32' cy='42' rx='10.5' ry='7.5' fill='#EC8DB0'/>"
           "<circle cx='28.3' cy='42' r='2' fill='#9D3C63'/><circle cx='35.7' cy='42' r='2' fill='#9D3C63'/><circle cx='23.5' cy='30' r='2.6' fill='#37003C'/>"
           "<circle cx='40.5' cy='30' r='2.6' fill='#37003C'/></svg>")
_HERO = st.empty()


def render_hero(sub="Expected points · win chances · best lineups", chip=""):
    _HERO.markdown(f"<div class='hog-banner'>{PIG_SVG}<div><div class='hog-title'>FPL Hog Statistician</div><div class='hog-sub'>{sub}</div></div>"
                   f"<div class='hog-chip'>{chip}</div></div>", unsafe_allow_html=True)


render_hero()
_st_caption = st.caption


def _smart_caption(body="", *a, **k):
    """Short notes show as small text. Long explanations are left out of the screens (they live in the Guide page and in every
    control's ⓘ tooltip), so no empty note lines appear."""
    if len(str(body)) > 170 and not a and not k:
        return None
    return _st_caption(body, *a, **k)


st.caption = _smart_caption
# widgets on screens that aren't open keep their values (Streamlit forgets widgets that weren't drawn)
_WB_PREFIX = ("stab_", "mf_", "rz_", "focus_pick", "recipe_team", "cols_mode", "rank_sub", "bps_", "pt_", "green_", "sm_", "mu_", "ma_", "sf_", "calc_", "tr_", "news_", "cam_", "yt_key", "windy_key", "team_by", "more_pick")
_WB_SKIP = ("calc_k_", "calc_c", "calc_best", "calc_sel", "pt_btn_", "pt_in_", "pt_swap_", "pt_sel", "pt_squad", "pt_xi", "pt_bench", "pt_pending", "pt_init", "pt_metric_last")
_WB = st.session_state.setdefault("_wb", {})
for _k in [k_ for k_ in list(_WB) if str(k_).startswith(_WB_SKIP)]:      # clear anything saved by older versions
    _WB.pop(_k, None)
for _k in list(st.session_state.keys()):
    if isinstance(_k, str) and _k.startswith(_WB_PREFIX) and not _k.startswith(_WB_SKIP):
        _WB[_k] = st.session_state[_k]
for _k, _v in list(_WB.items()):
    if _k not in st.session_state:
        st.session_state[_k] = _v

DRAFT = "https://draft.premierleague.com/api"
FPL = "https://fantasy.premierleague.com/api"
GOAL_PTS = np.array([0, 6, 6, 5, 4], float)   # index = element_type (1 GKP, 2 DEF, 3 MID, 4 FWD)
CS_PTS = np.array([0, 4, 4, 1, 0], float)
DEFCON_LIMIT = np.array([0, 0, 10, 12, 12])   # actions needed for +2 (GKP can't earn it)
STAT_COLS = [
    "minutes", "goals_scored", "assists", "bonus", "yellow_cards", "red_cards", "own_goals",
    "penalties_missed", "saves", "threat", "creativity", "influence", "ict_index", "bps", "starts",
    "expected_goals", "expected_assists", "expected_goal_involvements", "expected_goals_conceded",
    "defensive_contribution", "clearances_blocks_interceptions", "recoveries", "tackles", "total_points",
]
RATE_KEYS = ["g", "a", "gx", "ax", "dc", "bonus", "yc", "rc", "og", "pm", "sv"]
PRIOR_WEIGHT = {"g": 5, "a": 6, "gx": 5, "ax": 6, "dc": 3, "bonus": 4, "yc": 6, "rc": 25, "og": 25, "pm": 40, "sv": 4}
EXTRA_RATES = ["creativity", "threat", "influence", "ict_index", "bps", "expected_goal_involvements",
               "expected_goals_conceded", "defensive_contribution", "clearances_blocks_interceptions",
               "recoveries", "tackles", "saves", "bonus", "expected_goals", "expected_assists"]
FWD_RATES_4 = ["expected_goals", "expected_assists", "threat", "creativity", "influence", "expected_goal_involvements", "bps"]
ODDS_HOST = "https://api.the-odds-api.com/v4"
ODDS_SPORT = "soccer_epl"
# Odds API market keys (soccer): 1X2, totals, team totals, both-teams-to-score, anytime goalscorer, player assists (Over/Under).
# There is no clean-sheet market: clean-sheet odds are derived from the implied goals (team totals / BTTS / totals / 1X2).
ODDS_MARKETS = "h2h,totals,team_totals,btts,player_goal_scorer_anytime,player_assists"
TEAM_ALIAS = {"manchester united": "man utd", "manchester city": "man city", "tottenham hotspur": "spurs", "tottenham": "spurs",
              "newcastle united": "newcastle", "nottingham forest": "nottm forest", "wolverhampton wanderers": "wolves",
              "wolverhampton": "wolves", "brighton and hove albion": "brighton", "brighton hove albion": "brighton",
              "west ham united": "west ham", "leeds united": "leeds", "afc bournemouth": "bournemouth", "leicester city": "leicester",
              "sheffield united": "sheffield utd", "sunderland afc": "sunderland", "ipswich town": "ipswich", "norwich city": "norwich",
              "luton town": "luton", "west bromwich albion": "west brom", "crystal palace fc": "crystal palace"}
MM_STATS = ["g", "a", "thr", "cre", "pts"]   # what an opponent concedes to attackers: goals, assists, threat, creativity, FPL points
GW_TABLE_MIN_ROWS = 1000
POISSON_K = np.arange(0, 12)
POISSON_FACT = np.array([factorial(int(k)) for k in POISSON_K], float)
NB_DISPERSION = 8.0

# ---- Team stability ----
HOT_FORM = 4.0   # a starter counts as "in form" when his last-4-game average is above this
STAB_KEYS = ["momentum", "underlying", "clinical", "squad_form", "hot_share", "continuity", "club_size", "discipline"]
STAB_LABELS = {"momentum": "Momentum", "underlying": "Underlying play", "clinical": "Clinical / resilient",
               "squad_form": "Starters' form", "hot_share": "Roster in form (>4.0)", "continuity": "Lineup continuity",
               "club_size": "Club size", "discipline": "Discipline"}
STAB_DEFAULT_W = {"momentum": 0.22, "underlying": 0.22, "clinical": 0.08, "squad_form": 0.13, "hot_share": 0.13,
                  "continuity": 0.08, "club_size": 0.09, "discipline": 0.05}
# Solve FPL "conditions": z-scored situational signals whose extra effect on AER is fitted (and validated) out of sample
COND_COLS = ["c_home", "c_teamform", "c_hot", "c_morale", "c_skill", "c_create", "c_form", "c_matchup", "c_matrix", "c_carry", "c_hunger"]
COND_LABELS = {"c_home": "Home", "c_teamform": "Team form", "c_hot": "Roster in form", "c_morale": "Morale",
               "c_skill": "Skill", "c_create": "Creativity", "c_form": "Player form", "c_matchup": "Matchup", "c_matrix": "Matchup matrix",
               "c_carry": "Carries team", "c_hunger": "Hungry to carry"}
GOD_PRIOR = np.full(len(COND_COLS), 0.03)
# Fitted context effects (ridge-shrunk toward COEF_PRIOR, bounded by COEF_LO / COEF_HI):
#   0-5  stability: goals own/opp, assists own/opp, clean sheets own/opp
#   6-8  historical match rating: vs this team, vs its system, vs its difficulty tier
#   9-10 desperation (D in the master equation): own attacking returns, clean sheets when either side has a lot at stake
DESIGN_COLS = ["d_gs", "d_go", "d_as", "d_ao", "d_cs", "d_co", "d_ht", "d_hf", "d_hd", "d_dg", "d_dcs"]
COEF_PRIOR = np.array([0.10, -0.08, 0.10, -0.08, 0.15, -0.10, 0.30, 0.25, 0.30, 0.20, -0.15])
COEF_LO = np.array([0, -0.4, 0, -0.4, 0, -0.4, 0, 0, 0, 0, -0.6])
COEF_HI = np.array([0.5, 0, 0.5, 0, 0.5, 0, 1, 1, 1, 0.6, 0])
HM_SHRINK = (2.0, 3.0, 4.0)   # pseudo-matches pulling each historical signal toward 0 (same team / same system / same tier)

# ---- Desperation (match leverage) ----
DES_TARGETS = ((0, 1.0), (4, 0.6), (16, 1.0))  # (index among the other 19 teams to finish above, weight): title, top 5, survival
DES_CARD_BOOST = 0.15                           # extra card rate per unit of stakes (prior, not fitted)

# ---- Squad availability (live forecasts only) ----
AVAIL_REDIST = 0.25   # share of an absent player's attacking output that flows to teammates already in the side
AVAIL_LOSS = 0.50     # share of an absent player's attacking output the team simply loses (opponent's clean-sheet odds rise)

# =========================================================
# CONTROL GUIDE — one descriptor per control, shown as the ⓘ tooltip and listed in "How it works"
# =========================================================
HELP = {
    # League & Gameweek
    "league_id": "Your FPL Draft league, used to know which players are already owned.",
    "refresh": "Clears cached data and re-downloads everything from FPL.",
    "target_gw": "The gameweek being forecast. Only earlier gameweeks are ever used.",
    "horizon": "Adds AER over this many consecutive gameweeks, for planning ahead. Default 1.",
    # Model settings
    "use_learning": "Adds the machine-learning adjustment trained on past forecast misses. Default on.",
    "use_career": "Off by default for speed (it downloads every player's history once). Downloads past-season goals and xG once, to judge whether a player finishes above or below his xG. Default on.",
    "god_on": "Applies the fitted conditions multiplier and spread calibration to AER (and so to DPS, Theta Swole and every lineup). Default off.",
    "god_conv": "Scales the conditions multiplier. 1 = what the data supports, above 1 = your belief that the conditions matter more. Default 1.0.",
    "window": "How many recent games to look at. A home fixture uses the home games among them, an away fixture the away ones. Default 3.",
    "player_pull": "How strongly recent rates are pulled back to the player's season rate. 0 = trust recent games raw. Default 2.0.",
    "minutes_pull": "The same pull, applied to playing-time probabilities. Default 1.0.",
    "team_pull": "Multiplies the data-driven pull of team attack and defence toward the league average. Default 1.0.",
    "xg_weight": "Blend of expected versus actual goals and assists. 1 = pure xG/xA, 0 = pure actual. Default 0.75.",
    "use_defcon": "Includes the +2 defensive-contribution points. Untick it if your league doesn't use them. Default on.",
    # Advanced
    "form_hl": "A game's weight halves every this many gameweeks. Smaller = latest games dominate. Default 3.",
    "minutes_hl": "The same decay, applied to playing-time history. Default 3.",
    "prior_strength": "How strongly thin-sample players are pulled to their position average. Default 1.0.",
    "team_long": "Blends recent-window team strength with a whole-season decayed estimate. 0 = recent games only. Default 0.5.",
    "mins_ml_w": "How much the start/sub/bench pattern model replaces the smoothed minutes rates. 0 = off. Default 0.6.",
    "stratify": "Trains one correction model per position group (DEF/GK, MID, FWD) instead of one pooled model. Default on.",
    # Master xP equation
    "master": "Uses the context-adjusted Poisson equation as the core forecast. Default on.",
    "extras": "Keeps goals-conceded deductions, saves, DEFCON, own goals and penalty misses, which the equation as written leaves out. Default on.",
    "ctx_pow": "An exponent on the venue, momentum and morale multipliers. 0 switches them off. Lower it if they double-count. Default 1.0.",
    "w_venue": "How far the home/away multiplier moves away from 1. Default 0.5.",
    "mom_cap": "The largest boost or penalty from xG-difference momentum (recent xG difference vs the team's own season level). Default ±10%.",
    "w1": "Team-wide penalty for injuries. Off by default because injury redistribution handles absences more precisely. Default 0.",
    "w2": "How much your stability-grid edits (news the data can't see) move attack and defence. Default 0.10.",
    "w3": "How much a drift in win odds since the first logged fetch moves morale. Needs odds. Default 0.5.",
    "sigma_disc": "How much a team's above-average card and own-goal rate raises a player's card risk. Default 0.3.",
    "psi": "How much the defenders' and keeper's error rate raises goals conceded. Default 0.15.",
    "k_reg": "How much career history is needed before career finishing outweighs this season's. Higher = trust career less. Default 300.",
    # Context effects
    "desperation": "Scales the fitted effect of stakes (title, top 5, survival) on attacking returns and clean sheets. Default 1.0.",
    "squad_avail": "Gives part of a flagged-out attacker's output to his teammates and lowers the opponent's threat. Upcoming gameweeks only. Default on.",
    "leader": "Makes reliable, high-performing, older players react more strongly to their team's stability. Default 0.5.",
    "hmpr": "Scales how much past results against this opponent, its system and its difficulty tier move AER. Default 1.0.",
    "hm_min": "Only games a player started and played this long count toward that historical rating. Default 70.",
    "ep_w": "Mixes in FPL's official expected points. This blend is untested against past results. Default 0.",
    # Bookmaker priors
    "odds_key": "Your free key from the-odds-api.com. It is never saved to disk.",
    "odds_regions": "Which bookmakers to pull. Player props are only offered for 'us'; adding ',uk' gives more match lines but costs more credits.",
    "mkt_on": "Lets fetched odds feed the forecast. Default off.",
    "mkt_w": "How much implied goals and scorer odds replace the model's own numbers. Default 0.7.",
    "mkt_margin": "Removes the bookmaker's built-in profit margin from goalscorer prices. Default 10%.",
    "fetch_odds": "Downloads odds for the target gameweek. Uses credits; results are cached for 3 hours.",
    # DPS
    "wpa_h": "How many upcoming gameweeks WPA looks at: each one against the opponent you actually face that week. Default 3.",
    "wpa_decay": "How much each later gameweek counts relative to the one before (0.85 = next week counts 85%). Default 0.85.",
    "rank_by": "How the Rankings table is ordered. Green score = the players whose rows are greenest: every key column adds how far he is "
               "into the green half in seven areas (expected points, upside, minutes, attack, defence, bonus, value to your team), each counted once. "
               "Haul points = expected points from big weeks (haul chance × typical haul size, in points). WPA (default) = how much claiming the player raises your chance of winning your next matchups, "
               "after dropping your least useful player at his position. It falls back to Pts added when there are no head-to-head fixtures, "
               "and to Theta Swole when no team is picked. Theta Swole, AER and DPS give the other orderings.",
    "bps": "BPS profile: each player's bonus-points-system score per 90 from games of 30+ minutes, as an average, a floor (10th percentile) "
           "and a ceiling (90th percentile), compared only with his own position. xBonus and P(3 bonus) simulate each fixture's bonus race.",
    "zipf": "Zipf xVR (Zipf Expected-Value Rating): expected points from the top-20% 'haul' tail of a gameweek, where each elite performance "
            "is weighted by its Zipf rank. Built from every past gameweek: players who played are ranked by points over what an ordinary player in their position "
            "would score in the same minutes, the top 20% "
            "(Pareto's vital few) earn credit = points ÷ rank^s × (1 + minutes fraction), with s fitted from the data. A model fitted on past "
            "gameweeks turns xG, xA, threat, creativity, BPS, minutes, form and past elite rate into each player's chance of joining the top 20% "
            "next gameweek. Shown as Haul points = that chance × the points he usually scores in a haul week (real FPL points). The Zipf-weighted "
            "xVR index stays on the Accuracy page as research. Transfers in are added only if last season shows they predict it.",
    "risk_kappa": "Risk appetite for Theta Swole. 0 = neutral: rank on expected points. Above 0 rewards boom-or-bust players with a wide floor–ceiling "
                  "range (useful when you're the underdog in your matchup); below 0 rewards steady players (useful when you're protecting a lead). Default 0.",
    "dd_scale": "The formula part of DPS uses xG + xA + creativity + threat + influence, either summed (0–5) or averaged (0–1). Because DPS then "
                "averages that with FPL's expected points, the choice matters: 'sum' gives the formula more weight, 'average' gives FPL's number more weight.",
    # Filters (display only, forecasts unchanged)
    "f_team": "Shows one club only. Display filter; forecasts are unchanged.",
    "f_pos": "Chooses which positions to show. Display filter.",
    "f_min": "Hides players with fewer season minutes than this. Display filter.",
    "f_out": "Shows unavailable players too. Display filter.",
    "f_wire": "Hides players already owned in your league. Display filter.",
    "f_search": "Filters by name. Display filter.",
    # Main page
    "solve_btn": "One click that auto-tunes the settings on past gameweeks, then switches on the learned correction and Solve FPL.",
    "focus_pick": "Pick players whose chance of playing you want to adjust by hand.",
    "focus": "Scales a player's chance of playing. 0.5 = expect rotation, 1.2 = safer than his history suggests. Default 1.0.",
    "recipe_team": "Picks which team's stability ingredients to edit.",
    "recipe_reset": "Returns that team's ingredients to their measured values.",
    "stab_reset": "Returns every fixture slider to its automatic score.",
    "stab_fixture": "Your read of this team this gameweek: −1 = chaos (sackings, sanctions, injury crisis), +1 = settled and driven. "
                    "Moving it away from the auto value acts as manager news in the equation.",
    # My Team tab
    "my_entry": "Your team in this league. Needed for WPA, Pts added, Upgrade vs my XI and the My Team page.",
    "team_by": "How your lineup is chosen: by AER (the XI with the most expected points) or by DPS (the XI with the highest DPS). "
               "The projected total always counts expected points, so you can compare the two.",
    # AI assistant
    "ai_provider": "Which AI answers. Google Gemini: free, needs a free API key from aistudio.google.com (no card); on the free tier Google may "
                   "use your questions to improve its models. Ollama: free and fully private, runs on your Mac, no account; install it from "
                   "ollama.com and download a model once. Anthropic Claude: paid, needs an Anthropic API key.",
    "ai_key": "The API key for the chosen provider (Gemini: aistudio.google.com → Get API key; Claude: console.anthropic.com). Kept only for this "
              "session, never written to disk. You can also set GEMINI_API_KEY or ANTHROPIC_API_KEY before starting the app.",
    "ai_model": "Which model answers. Gemini: gemini-flash-latest (always Google's current free Flash model). Ollama: any model you've downloaded, "
                "e.g. llama3.2 (small, fast) or qwen2.5:7b (better answers, needs ~6 GB of memory). Claude: Sonnet, Opus or Haiku.",
    "ai_host": "Where Ollama is running. The default (http://localhost:11434) is right unless you changed it.",
    "ai_web": "Lets the assistant look up current news (injuries, press conferences, fixtures). Gemini uses Google Search (free quota); Ollama uses "
              "DuckDuckGo if the free 'ddgs' package is installed (python3 -m pip install ddgs); Claude uses its own web search (small extra cost).",
    "ai_source": "Also sends the program's full source code, for exact 'how is this calculated' answers. Much larger requests; not available with "
                 "Ollama, whose models can't hold that much text.",
    "ai_clear": "Starts a fresh conversation.",
    # News tab
    "news_clubs": "Clubs whose news to show. Starts with every club playing in the target gameweek.",
    "news_days": "How far back to look for articles. Default 3 days.",
    "news_impact": "Show only stories that can change who plays or how well: injuries, suspensions, fitness, manager news, team news, rotation.",
    "news_search": "Only show stories mentioning this text (a player, club or topic).",
    "news_max": "How many stories to show. Default 30.",
    "ai_autoset": "Lets the AI change sliders and settings itself when you ask it to (e.g. 'set Man City's stability for this gameweek'). "
                  "Every change is listed in its reply. Untick to have changes offered as buttons you confirm instead.",
    # Live cams tab
    "cam_team": "Which club's stadium to look around. Starts with the club picked in the sidebar's Team filter.",
    "cam_radius": "How far from the stadium to search for public webcams. 1 km is about 1,100 yards. Default 3 km.",
    "cam_n": "How many of the nearest cameras to show. Default 4.",
    "cam_auto": "Reloads the snapshot cameras (TfL / Windy) every minute while this tab is open. Live YouTube streams play continuously "
                "and don't need refreshing.",
    "yt_key": "Free YouTube Data API key, used to find streams that are live right now near the ground. Get one at console.cloud.google.com: "
              "create a project → APIs & Services → enable 'YouTube Data API v3' → Credentials → Create API key. The free quota allows roughly "
              "25–100 club searches a day; results are cached for 15 minutes. Kept only for this session; you can also set YOUTUBE_API_KEY.",
    "windy_key": "Free key from api.windy.com/keys (sign up, create a Webcams API key). Adds public webcams near every ground, not just London. "
                 "Kept only for this session; you can also set WINDY_API_KEY before starting the app.",
    # Player trends tab
    "tr_players": "Players to chart (up to 6). Starts with the top of your current ranking.",
    "tr_metrics": "Which weekly stats to chart, one small chart each.",
    "tr_view": "Per gameweek = raw weekly numbers. Rolling = 3-gameweek average (smooths luck). Cumulative = running season total. "
               "Per 90 = rate per 90 minutes over the last 3 gameweeks (fair for rotation players).",
    "tr_last": "Adds last season's weeks to the charts (shown as GW 0 and below), matched by player name.",
    # Accuracy tab
    "rank_signals": "Shows which inputs actually reduce prediction error.",
    # Tune on history
    "past_seasons": "Past seasons to download and learn from, comma-separated (e.g. 2025-26 or 2024-25, 2025-26). Match-by-match data comes from the "
                    "public vaastav/Fantasy-Premier-League dataset on GitHub; it is downloaded once and cached on disk.",
    "use_past": "Off by default for speed. When on, adds past seasons' gameweeks to the learned correction, the fitted effects and the accuracy checks. Older weeks count less "
                "than recent ones. Default on.",
    "tune_mode": "Quick: every 4th past gameweek, one pass (a few minutes). Thorough: every 2nd past gameweek, two passes (roughly 20 minutes). "
                 "Both then check the result on gameweeks the search never saw.",
    "tune_btn": "Searches the model's settings for the lowest squared error between forecasts and actual points across this season and the past "
                "seasons, forecasting every gameweek only from earlier ones. The best settings are saved and applied automatically next time.",
    "use_tuned": "Start each session from the last saved tuned settings. Default on.",
    "past_test": "Checks each past season's files on GitHub one by one and tries to rebuild the season, showing exactly what worked and what didn't.",
    "reset_defaults": "Puts every tunable slider back to its default value.",
}
INGREDIENT_HELP = {
    "momentum": "Recent points per game, recency-weighted.",
    "underlying": "Recent xG difference (chances created minus conceded).",
    "clinical": "Goal difference beyond xG difference: finishing and resilience.",
    "squad_form": "Recent points of the regular 70+ minute starters.",
    "hot_share": "Share of regular starters averaging over 4 points in their last 4 games.",
    "continuity": "How much the starting XI has stayed the same.",
    "club_size": "Season xG difference blended with squad value.",
    "discipline": "Fewer cards = higher.",
}
# Grouping and labels for the control guide in the "How it works" tab
HELP_GUIDE = [
    ("League & Gameweek", [("FPL Draft League ID", "league_id"), (":material/refresh: Refresh data", "refresh"), ("Target Gameweek", "target_gw"),
                           ("Gameweeks to sum", "horizon")]),
    ("Model settings", [("Use learned correction", "use_learning"), ("Use career finishing baseline", "use_career"),
                        ("Solve FPL active", "god_on"), ("Solve FPL conviction ×", "god_conv"), ("Recent games (split home/away)", "window"),
                        ("Recent-form smoothing", "player_pull"), ("Minutes smoothing", "minutes_pull"), ("Team smoothing", "team_pull"),
                        ("Trust xG/xA over actual G/A", "xg_weight"), ("Score DEFCON points", "use_defcon")]),
    ("Advanced", [("Recent-form half-life", "form_hl"), ("Season minutes half-life", "minutes_hl"), ("Season shrinkage", "prior_strength"),
                  ("Long-memory team strength weight", "team_long"), ("Learned minutes model weight", "mins_ml_w"),
                  ("Separate models for DEF/GK, MID, FWD", "stratify")]),
    ("🧮 Master xP equation", [("Use the master equation", "master"), ("Also score rules the equation leaves out", "extras"),
                              ("Context strength", "ctx_pow"), ("Venue weight", "w_venue"), ("Momentum cap", "mom_cap"),
                              ("Morale: squad fitness weight", "w1"), ("Morale: manager news weight", "w2"),
                              ("Morale: betting-line shift weight", "w3"), ("Discipline sensitivity (σ)", "sigma_disc"),
                              ("Defensive-error sensitivity (ψ)", "psi"), ("Finishing regression K", "k_reg")]),
    ("Context effects", [("Desperation impact ×", "desperation"), ("Redistribute injured players' output", "squad_avail"),
                         ("Captain / veteran boost", "leader"), ("Historical rating impact ×", "hmpr"),
                         ("History games: min minutes", "hm_min"), ("Blend FPL's ep_next", "ep_w")]),
    ("📈 Bookmaker priors", [("The Odds API key", "odds_key"), ("Bookmaker regions", "odds_regions"), ("Use bookmaker priors", "mkt_on"),
                            ("Market weight", "mkt_w"), ("Anytime-scorer margin to strip", "mkt_margin"),
                            ("Fetch odds for this gameweek", "fetch_odds")]),
    ("⚡ DPS & Theta Swole", [("Haul points / Zipf research", "zipf"), ("BPS profile", "bps"), ("Rank players by", "rank_by"), ("Theta Swole: risk appetite", "risk_kappa")]),
    ("🔍 Filters", [("Team", "f_team"), ("Positions", "f_pos"), ("Minimum season minutes", "f_min"),
                   ("Include injured / suspended / out", "f_out"), ("Only unowned players", "f_wire"), ("Search player", "f_search")]),
    ("Main page", [("⚡ Solve FPL button", "solve_btn"), ("Focus overrides: player picker", "focus_pick"),
                   ("Focus overrides: per-player slider", "focus"), ("Stability recipe: Team", "recipe_team"),
                   ("Reset this team", "recipe_reset"), (":material/restart_alt: Reset to auto", "stab_reset"), ("Per-fixture stability sliders", "stab_fixture")]),
    ("👕 Your team & WPA", [("Your team", "my_entry"), ("WPA: gameweeks ahead", "wpa_h"), ("WPA: later weeks weight", "wpa_decay")]),
    ("👕 My Team page", [("Choose lineup by", "team_by")]),
    ("🤖 AI assistant", [("AI provider", "ai_provider"), ("API key", "ai_key"), ("Model", "ai_model"), ("Ollama address", "ai_host"),
                         ("Allow web search", "ai_web"),
                         ("Let it read the program's source code", "ai_source"),
                         ("Let the AI change settings", "ai_autoset"), (":material/delete: New conversation", "ai_clear")]),
    ("📰 News", [("Clubs", "news_clubs"), ("Days back", "news_days"), ("Only selection-relevant news", "news_impact"),
                ("Search", "news_search"), ("Stories to show", "news_max")]),
    ("📹 Live cams", [("Club", "cam_team"), ("Search radius", "cam_radius"), ("Cameras to show", "cam_n"), ("Auto-refresh", "cam_auto"), ("YouTube live key", "yt_key"),
                     ("Windy webcams key", "windy_key")]),
    ("📈 Player trends", [("Players", "tr_players"), ("Metrics", "tr_metrics"), ("Show", "tr_view"), ("Include last season", "tr_last")]),
    ("🎯 Tune on history", [("Past seasons to learn from", "past_seasons"), ("Learn from past seasons", "use_past"),
                           ("Tuning depth", "tune_mode"), (":material/history: Tune on all history", "tune_btn"), (":material/cloud_download: Test past-season download", "past_test"),
                           ("Start from saved tuned settings", "use_tuned"), (":material/restart_alt: Default settings", "reset_defaults")]),
    ("🧪 Accuracy tab", [("Rank the most pertinent signals", "rank_signals")]),
]


# =========================================================
# DATA
# =========================================================
def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"})
    return s


@st.cache_data(ttl=900, show_spinner="Loading FPL data…")
def load_core(league_id: int):
    s = make_session()
    boot = s.get(f"{DRAFT}/bootstrap-static", timeout=15).json()
    fixtures = pd.DataFrame(s.get(f"{FPL}/fixtures/", timeout=15).json())
    players = pd.DataFrame(boot["elements"])
    teams = pd.DataFrame(boot["teams"])
    players["team_code"] = players["team"].map(dict(zip(teams["id"], teams["short_name"])))
    players["team_full_name"] = players["team"].map(dict(zip(teams["id"], teams["name"])))
    players["team_shirt"] = players["team"].map(dict(zip(teams["id"], teams["code"]))) if "code" in teams.columns else np.nan
    players["position"] = players["element_type"].map({1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"})
    for c_ in ("ep_next", "ep_this"):
        players[c_] = pd.to_numeric(players[c_], errors="coerce") if c_ in players.columns else np.nan
    if "code" in players.columns and (players["ep_next"].fillna(0).eq(0).all() or players["ep_this"].isna().all()):
        try:   # the Draft feed can lack FPL's expected points: take them from the main FPL feed, matched by the player's permanent code
            cl = pd.DataFrame(s.get(f"{FPL}/bootstrap-static/", timeout=15).json()["elements"]).set_index("code")
            for c_ in ("ep_next", "ep_this"):
                if c_ in cl.columns:
                    fill_ = players["code"].map(pd.to_numeric(cl[c_], errors="coerce"))
                    players[c_] = players[c_].where(players[c_].fillna(0) > 0, fill_)
        except Exception:
            pass
    players["ep_next"] = players["ep_next"].fillna(0)
    if "chance_of_playing_next_round" not in players:
        players["chance_of_playing_next_round"] = np.nan
    taken = None
    try:
        r = s.get(f"{DRAFT}/league/{league_id}/element-status", timeout=15)
        if r.status_code == 200:
            es = pd.DataFrame(r.json()["element_status"])
            taken = es[es["status"].isin(["u", "o"])]["element"].tolist()
    except Exception:
        taken = None
    return players, teams, fixtures, taken


@st.cache_data(ttl=900, show_spinner=False)
def load_league(league_id: int):
    """League teams, head-to-head fixtures and current ownership from the FPL Draft API."""
    s = make_session()
    out = {"entries": pd.DataFrame(), "matches": pd.DataFrame(), "owner": {}}
    try:
        d = s.get(f"{DRAFT}/league/{league_id}/details", timeout=15).json()
        out["entries"] = pd.DataFrame(d.get("league_entries", []))
        out["matches"] = pd.DataFrame(d.get("matches", []))
    except Exception:
        pass
    try:
        es = s.get(f"{DRAFT}/league/{league_id}/element-status", timeout=15).json()["element_status"]
        out["owner"] = {int(e["element"]): e.get("owner") for e in es if e.get("owner") is not None}
    except Exception:
        pass
    return out


@st.cache_data(ttl=900, show_spinner=False)
def load_picks(entry_id: int, gw: int):
    """A team's 15 picks for a gameweek as (element, position) pairs; positions 1-11 start, 12-15 are the bench."""
    try:
        r = make_session().get(f"{DRAFT}/entry/{entry_id}/event/{gw}", timeout=15)
        if r.status_code == 200:
            return [(int(p_["element"]), int(p_.get("position", 0))) for p_ in r.json().get("picks", [])]
    except Exception:
        pass
    return []


def squad_for(lg, le_id, gw, next_gw):
    """Element ids in a league team's squad: the picks made for a past gameweek, current ownership for upcoming ones."""
    ents = lg["entries"]
    if ents.empty or le_id is None:
        return []
    row = ents[ents["id"].astype(int) == int(le_id)]
    if row.empty:
        return []
    eid = int(row["entry_id"].iloc[0])
    if gw < next_gw:
        pk = load_picks(eid, int(gw))
        if pk:
            return [e for e, _ in pk]
    own = [e for e, o in lg["owner"].items() if o in (eid, int(le_id))]
    if own:
        return own
    for g_ in (int(gw), int(next_gw) - 1):
        pk = load_picks(eid, g_) if g_ >= 1 else []
        if pk:
            return [e for e, _ in pk]
    return []


def _secret(*names):
    """A key stored with the app (Streamlit Cloud 'Secrets' or an environment variable). Used when the box is left empty, and never
    shown on screen, so a friend using the shared link gets every feature without seeing or needing the keys."""
    for n_ in names:
        try:
            v_ = st.secrets.get(n_)
        except Exception:
            v_ = None
        v_ = v_ or os.environ.get(n_, "")
        if str(v_).strip():
            return str(v_).strip()
    return ""


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def entry_league(entry_id: int):
    """The FPL Draft league a team (entry) plays in, from the public entry endpoint. None if it can't be found."""
    try:
        js = make_session().get(f"{DRAFT}/entry/{int(entry_id)}/public", timeout=15).json()
        ls = (js.get("entry") or {}).get("league_set") or []
        return int(ls[0]) if ls else None
    except Exception:
        return None


PAST_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
TUNED_FILE = "fpl_tuned_params.json"


def default_past_season():
    """The season before the current one, in the dataset's folder format (e.g. '2025-26' during 2026-27)."""
    t = pd.Timestamp.today()
    start = t.year if t.month >= 7 else t.year - 1
    return f"{start - 1}-{str(start)[2:]}"


class PastDataError(RuntimeError):
    pass


def _read_csv_url(url):
    try:
        r = requests.get(url, timeout=90, headers={"User-Agent": "fpl-draft-aer"})
    except Exception as e_:
        raise PastDataError(f"couldn't reach {url} ({type(e_).__name__}: {e_})")
    if r.status_code == 404:
        raise PastDataError(f"not found (404): {url}")
    if r.status_code != 200:
        raise PastDataError(f"HTTP {r.status_code} for {url}")
    raw = r.content
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        txt = raw.decode("latin-1")
    return pd.read_csv(io.StringIO(txt), on_bad_lines="skip", low_memory=False)


@st.cache_data(persist="disk", show_spinner="Downloading a past season's match-by-match data (one time)…")
def load_past_raw(season: str):
    """teams.csv and fixtures.csv, then the season's combined gameweek file (gws/merged_gw.csv). If the combined file is missing, the
    per-gameweek files (gws/gw1.csv … gw38.csv) are downloaded and stacked instead."""
    base = f"{PAST_BASE}/{season}"
    try:
        tms = _read_csv_url(f"{base}/teams.csv")
    except PastDataError as e_:
        raise PastDataError(f"season folder '{season}' isn't in the dataset (yet): {e_}")
    fxs = _read_csv_url(f"{base}/fixtures.csv")
    try:
        gw = _read_csv_url(f"{base}/gws/merged_gw.csv")
    except PastDataError:
        def one(n_):
            try:
                d_ = _read_csv_url(f"{base}/gws/gw{n_}.csv")
                return d_ if "round" in d_.columns else d_.assign(round=n_)
            except PastDataError:
                return None
        with ThreadPoolExecutor(max_workers=8) as ex:
            parts = [d_ for d_ in ex.map(one, range(1, 39)) if d_ is not None and len(d_)]
        if not parts:
            raise PastDataError(f"no gameweek files found under {base}/gws/ (neither merged_gw.csv nor gw1.csv … gw38.csv)")
        gw = pd.concat(parts, ignore_index=True)
    if "round" not in gw.columns and "GW" in gw.columns:
        gw = gw.assign(round=gw["GW"])
    missing = [c for c in ("element", "fixture", "was_home", "round", "minutes", "total_points") if c not in gw.columns]
    if missing:
        raise PastDataError(f"the gameweek file is missing columns {missing}")
    if "position" not in gw.columns:
        raise PastDataError("the gameweek file has no 'position' column (seasons before 2020-21 don't); use 2020-21 or later")
    return gw, fxs, tms


def past_season_frames(gw, fxs, tms):
    """Turns a past season's per-fixture rows into the same players / per-gameweek history / fixtures / teams frames the live API gives."""
    pos_map = {"GK": 1, "GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}
    g = gw.copy()
    g["element_type"] = g["position"].astype(str).str.upper().map(pos_map) if "position" in g.columns else np.nan
    g = g[g["element_type"].notna() & g["fixture"].notna()].copy()
    fx = fxs.copy()
    fx["finished"] = fx["finished"].astype(str).str.lower().isin(["true", "1"])
    fmap = fx.set_index("id")[["team_h", "team_a"]]
    home = g["was_home"].astype(str).str.lower().isin(["true", "1"])
    fid = pd.to_numeric(g["fixture"], errors="coerce")
    g["team"] = np.where(home, fid.map(fmap["team_h"]), fid.map(fmap["team_a"]))
    g = g.dropna(subset=["team"]).rename(columns={"element": "id"})
    g["team"], g["id"], g["element_type"] = g["team"].astype(int), g["id"].astype(int), g["element_type"].astype(int)
    g["round"] = pd.to_numeric(g["round"], errors="coerce")
    g = g.dropna(subset=["round"])
    g["round"] = g["round"].astype(int)
    last = g.sort_values(["round"] + (["kickoff_time"] if "kickoff_time" in g.columns else [])).groupby("id").last()
    g = g[g["team"].to_numpy() == g["id"].map(last["team"]).to_numpy()]      # one club per player: keep rows at his final club
    cols = [c for c in STAT_COLS if c in g.columns]
    for c in cols:
        g[c] = pd.to_numeric(g[c], errors="coerce").fillna(0)
    hist = g.groupby(["id", "round"], as_index=False)[cols].sum()           # double gameweeks: one row per player per gameweek
    names = last["name"].astype(str) if "name" in last.columns else pd.Series("Player", index=last.index)
    players = pd.DataFrame({"id": last.index.astype(int), "team": last["team"].astype(int).to_numpy(),
                            "element_type": last["element_type"].astype(int).to_numpy(),
                            "web_name": names.str.split().str[-1].to_numpy(), "first_name": names.str.split().str[0].to_numpy(),
                            "second_name": names.str.split().str[-1].to_numpy(), "status": "a", "chance_of_playing_next_round": np.nan,
                            "now_cost": pd.to_numeric(last["value"], errors="coerce").to_numpy() if "value" in last.columns else np.nan})
    players["minutes"] = players["id"].map(hist.groupby("id")["minutes"].sum()).fillna(0)
    players["total_points"] = players["id"].map(hist.groupby("id")["total_points"].sum()).fillna(0) if "total_points" in hist else 0
    teams = tms[["id", "name", "short_name"]].copy()
    return players, hist, prep_fixtures(fx), teams


@st.cache_data(persist="disk", show_spinner="Rebuilding a past season in the model's format (one time)…")
def past_context(season: str):
    gw, fxs, tms = load_past_raw(season)
    players_p, hist_p, fx_p, teams_p = past_season_frames(gw, fxs, tms)
    ctx_p = build_context(hist_p, players_p, fx_p)
    ctx_p["career"] = None
    return ctx_p, players_p, dict(zip(teams_p["id"].astype(int), teams_p["short_name"]))


@st.cache_data(persist="disk", show_spinner="Forecasting every past-season gameweek from the weeks before it (one time per setting)…")
def past_training(season: str, p_key):
    ctx_p, players_p, code_p = past_context(season)
    return build_training(ctx_p, players_p, code_p, dict(p_key))


def load_tuned():
    try:
        with open(TUNED_FILE) as f_:
            return json.load(f_)
    except Exception:
        return None


def save_tuned(res):
    try:
        with open(TUNED_FILE, "w") as f_:
            json.dump(res, f_, indent=1, default=float)
    except Exception:
        pass


def _fetch_live(gw):
    s = make_session()
    for base in (DRAFT, FPL):
        try:
            els = s.get(f"{base}/event/{gw}/live", timeout=20).json()["elements"]
            items = els.items() if isinstance(els, dict) else [(e["id"], e) for e in els]
            rows = [{"id": int(pid), "round": gw, **(e.get("stats") or {})} for pid, e in items]
            if rows:
                return pd.DataFrame(rows)
        except Exception:
            continue
    return pd.DataFrame()


@st.cache_data(ttl=1800, show_spinner="Loading gameweek history (one call per GW)…")
def load_history(gws: tuple):
    with ThreadPoolExecutor(max_workers=8) as ex:
        frames = [f for f in ex.map(_fetch_live, gws) if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame({"id": [], "round": []})


def prep_fixtures(fx):
    fx = fx.dropna(subset=["event"]).copy()
    fx["event"] = fx["event"].astype(int)
    for c in ["team_h_score", "team_a_score"]:
        fx[c] = pd.to_numeric(fx[c], errors="coerce")
    fx["finished"] = fx["finished"].fillna(False).astype(bool)
    return fx


# =========================================================
# LEARNED MINUTES MODEL: P(0 / 1-59 / 60+ minutes) from each player's game-by-game sequence
# =========================================================
MIN_FEATS = [f"m{k}" for k in range(1, 7)] + ["mean3", "mean6", "share60_3", "zeros6", "avail6", "cum_share", "team_t60",
                                               "element_type", "venue", "fdr", "n_fix"]


def _seq_cols(h):
    return pd.DataFrame({"id": h["id"].to_numpy(), "round": h["round"].to_numpy(), "team": h["team"].to_numpy(),
                         "element_type": h["element_type"].to_numpy(), "min_pm": h["min_pm"].to_numpy(),
                         "venue": h["venue"].to_numpy(), "fdr": h["hm_fdr"].fillna(3.0).to_numpy(), "n_fix": h["n_played"].to_numpy()})


def _seq_features(d):
    """Every feature for a row uses only EARLIER rows of the same player (or team), so training rows are leak-free by construction."""
    d = d.sort_values(["id", "round"]).reset_index(drop=True)
    for k in range(1, 7):
        d[f"m{k}"] = d.groupby("id")["min_pm"].shift(k) / 90.0
    M = d[[f"m{k}" for k in range(1, 7)]]
    M3 = d[["m1", "m2", "m3"]]
    d["avail6"] = M.notna().sum(axis=1)
    d["mean3"], d["mean6"] = M3.mean(axis=1), M.mean(axis=1)
    d["share60_3"] = (M3 >= 60 / 90.0).sum(axis=1) / M3.notna().sum(axis=1).replace(0, np.nan)
    d["zeros6"] = (M == 0).sum(axis=1)
    mp0 = d["min_pm"].fillna(0)
    prev_sum = mp0.groupby(d["id"]).cumsum() - mp0
    prev_n = d.groupby("id").cumcount()
    d["cum_share"] = prev_sum / (90.0 * prev_n.replace(0, np.nan))
    d["is60"] = (d["min_pm"] >= 60).astype(float)
    tt = d.groupby(["team", "round"], as_index=False)["is60"].sum().sort_values(["team", "round"])
    tt["team_t60"] = tt.groupby("team")["is60"].transform(lambda z: z.shift(1).rolling(3, min_periods=1).mean())
    d = d.merge(tt[["team", "round", "team_t60"]], on=["team", "round"], how="left")
    d["cls"] = np.where(d["min_pm"].isna(), np.nan, np.where(d["min_pm"] <= 0, 0, np.where(d["min_pm"] < 60, 1, 2)))
    return d


def minutes_seq(h):
    if len(h) == 0:
        return pd.DataFrame(columns=["id", "round", "cls"] + MIN_FEATS)
    return _seq_features(_seq_cols(h))


class SafeHGB:
    """Wraps a scikit-learn histogram gradient-boosting model so that columns with fewer than two distinct values (constant or all-missing
    early in a season) are left out before fitting. Recent scikit-learn versions crash on such columns ('window shape cannot be larger than
    input array shape'). Monotonic constraints are trimmed to match. If nothing usable is left, it predicts the training average."""

    def __init__(self, est):
        self.est, self.keep, self.const = est, None, None

    def fit(self, X, y, sample_weight=None):
        Xa = np.asarray(X, dtype=float)
        y = np.asarray(y)
        keep = []
        for j in range(Xa.shape[1]):
            col = Xa[:, j]
            col = col[~np.isnan(col)]
            if col.size and np.unique(col).size >= 2:
                keep.append(j)
        self.keep = keep
        if not keep or len(y) < 2:
            if hasattr(self.est, "predict_proba"):
                cls_, cnt_ = np.unique(y, return_counts=True)
                self.classes_, self.const = cls_, cnt_ / cnt_.sum()
            else:
                self.const = float(np.average(y.astype(float), weights=sample_weight))
            return self
        mono = getattr(self.est, "monotonic_cst", None)
        if isinstance(mono, (list, tuple, np.ndarray)) and len(mono) == Xa.shape[1]:
            self.est.set_params(monotonic_cst=[mono[j] for j in keep])
        kw = {} if sample_weight is None else {"sample_weight": np.asarray(sample_weight, float)}
        self.est.fit(Xa[:, keep], y, **kw)
        if hasattr(self.est, "classes_"):
            self.classes_ = self.est.classes_
        return self

    def predict(self, X):
        Xa = np.asarray(X, dtype=float)
        if self.const is not None:
            if hasattr(self, "classes_") and isinstance(self.const, np.ndarray):
                return np.repeat(self.classes_[np.argmax(self.const)], len(Xa))
            return np.full(len(Xa), self.const)
        return self.est.predict(Xa[:, self.keep])

    def predict_proba(self, X):
        Xa = np.asarray(X, dtype=float)
        if self.const is not None:
            return np.tile(self.const, (len(Xa), 1))
        return self.est.predict_proba(Xa[:, self.keep])

    def get_params(self, deep=True):
        return {"est": self.est}

    def set_params(self, **kw):
        for k_, v_ in kw.items():
            setattr(self, k_, v_)
        return self


def fit_minutes_model(seq, cutoff):
    d = seq[(seq["round"] < cutoff) & (seq["avail6"] >= 1) & seq["cls"].notna()]
    if len(d) < 1500 or d["cls"].nunique() < 3:
        return None
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
    except Exception:
        return None
    try:      # a failed minutes model must never stop the app: fall back to the smoothed playing-time rates
        return SafeHGB(HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08, max_iter=120, min_samples_leaf=80,
                                                      l2_regularization=3.0, random_state=0)).fit(d[MIN_FEATS], d["cls"].astype(int))
    except Exception:
        return None


@st.cache_resource(show_spinner=False)
def minutes_model_at(_seq, cutoff, key):
    return fit_minutes_model(_seq, cutoff)


def minutes_predict(model, h, players, fx_g, g):
    """P(60+ minutes) and P(sub cameo) for gameweek g from earlier games only; None when no model / no fixtures."""
    if model is None or fx_g.empty:
        return None
    fxr = pd.concat([pd.DataFrame({"team": fx_g["team_h"], "venue": 1, "fdr": fx_g.get("team_h_difficulty", 3)}),
                     pd.DataFrame({"team": fx_g["team_a"], "venue": 0, "fdr": fx_g.get("team_a_difficulty", 3)})])
    tg = fxr.groupby("team").agg(venue=("venue", "mean"), fdr=("fdr", "mean"), n_fix=("venue", "size")).reset_index()
    tg["venue"] = np.where(tg["venue"] == 1, 1, np.where(tg["venue"] == 0, 0, 2))
    tgt = players[["id", "team", "element_type"]].merge(tg, on="team")
    tgt["round"], tgt["min_pm"] = g, np.nan
    hist = _seq_cols(h[h["round"] < g])
    d = _seq_features(pd.concat([hist, tgt[hist.columns]], ignore_index=True))
    d = d[d["round"] == g]
    if d.empty:
        return None
    proba, cls = model.predict_proba(d[MIN_FEATS]), list(model.classes_)
    ok = d["avail6"].to_numpy() >= 1
    out = pd.DataFrame({"id": d["id"].to_numpy(), "p60_ml": np.where(ok, proba[:, cls.index(2)], np.nan),
                        "psub_ml": np.where(ok, proba[:, cls.index(1)], np.nan)})
    return out.groupby("id").mean()


def build_context(hist, players, fx):
    fin = fx[fx["finished"] & fx["team_h_score"].notna() & fx["team_a_score"].notna()]
    home = pd.DataFrame({"fid": fin["id"], "team": fin.team_h, "opp": fin.team_a, "home": 1,
                         "gf": fin.team_h_score, "round": fin.event,
                         "fdr": fin.get("team_h_difficulty", 3)})
    away = pd.DataFrame({"fid": fin["id"], "team": fin.team_a, "opp": fin.team_h, "home": 0,
                         "gf": fin.team_a_score, "round": fin.event,
                         "fdr": fin.get("team_a_difficulty", 3)})
    long = pd.concat([home, away], ignore_index=True)
    long["n_played"] = long.groupby(["team", "round"])["team"].transform("size")
    long["xg"] = np.nan

    h = hist.copy()
    for c in STAT_COLS:
        h[c] = pd.to_numeric(h[c], errors="coerce").fillna(0) if c in h.columns else 0.0
    h = h.merge(players[["id", "team", "element_type"]], on="id")
    h = h.merge(long[["team", "round", "n_played"]].drop_duplicates(), on=["team", "round"])
    h["min_pm"] = h["minutes"] / h["n_played"]
    h["start_pm"] = h["starts"] / h["n_played"]
    vm = long.groupby(["team", "round"])["home"].mean().rename("hm").reset_index()
    h = h.merge(vm, on=["team", "round"], how="left")
    h["venue"] = np.where(h["hm"] == 1, 1, np.where(h["hm"] == 0, 0, 2))  # 1 home, 0 away, 2 mixed double GW

    # historical-match-rating support: single-match rounds only, tagged with opponent, fixture difficulty, opponent's system
    use_starts = h["starts"].sum() > 0
    mask_s = (h["start_pm"] >= 0.5) if use_starts else (h["min_pm"] >= 60)
    ft = pd.DataFrame({"team": pd.Series(dtype=int), "round": pd.Series(dtype=int), "formation": pd.Series(dtype=object)})
    if len(h) and mask_s.any():
        cnt = (h[mask_s].groupby(["team", "round", "element_type"]).size().unstack(fill_value=0)
               .reindex(columns=[1, 2, 3, 4], fill_value=0))
        ft = pd.DataFrame({"formation": cnt[2].astype(str) + "-" + cnt[3].astype(str) + "-" + cnt[4].astype(str)}).reset_index()
    single = long.loc[long["n_played"] == 1, ["team", "round", "opp", "fdr"]]
    ft = ft.merge(single[["team", "round"]], on=["team", "round"])
    single = single.merge(ft.rename(columns={"team": "opp", "formation": "hm_form"}), on=["opp", "round"], how="left")
    single = single.rename(columns={"opp": "hm_opp", "fdr": "hm_fdr"})
    h = h.merge(single[["team", "round", "hm_opp", "hm_fdr", "hm_form"]], on=["team", "round"], how="left")
    h["hm_ok"] = h["hm_opp"].notna() & ((h["start_pm"] >= 0.5) if use_starts else True)

    if h["expected_goals"].sum() > 0:
        txg = h.groupby(["team", "round"])["expected_goals"].sum().rename("txg").reset_index()
        long = long.merge(txg, on=["team", "round"], how="left")
        long["xg"] = long["txg"] / long["n_played"]
    return {"h": h, "long": long, "fin": fin, "form_tbl": ft, "seq": minutes_seq(h)}


# =========================================================
# STRUCTURAL MODEL — team side (last N at venue)
# =========================================================
def _fit_team_core(long, cutoff, p, window, hl=None):
    """Attack/defence by venue from a team's last `window` matches (any venue), optionally exponentially decayed (half-life `hl` gameweeks),
    with empirical-Bayes shrinkage toward the league average."""
    NT = 21
    neutral = dict(att_h=np.full(NT, 1.45), att_a=np.full(NT, 1.15), def_h=np.full(NT, 1.15),
                   def_a=np.full(NT, 1.45), base_h=1.45, base_a=1.15,
                   ease_h=np.ones(NT), ease_a=np.ones(NT), faced_h=np.ones(NT), faced_a=np.ones(NT))
    d = long[long["round"] < cutoff].copy()
    if len(d) < 10:
        return neutral
    xw = p["xg_weight"]
    d["gf_b"] = np.where(d["xg"].notna(), (1 - xw) * d["gf"] + xw * d["xg"], d["gf"])
    d["ga_b"] = d.groupby("fid")["gf_b"].transform("sum") - d["gf_b"]
    base_h = d.loc[d["home"] == 1, "gf_b"].mean()
    base_a = d.loc[d["home"] == 0, "gf_b"].mean()
    d = d.sort_values(["team", "round"])
    d["rk"] = d.groupby("team")["round"].rank(ascending=False, method="first")
    w = d[d["rk"] <= window].copy()
    w["wt"] = 0.5 ** ((cutoff - 1 - w["round"]) / hl) if hl else 1.0
    w["gf_bw"], w["ga_bw"] = w["gf_b"] * w["wt"], w["ga_b"] * w["wt"]

    def _sc(col, home_):
        s_ = w[w["home"] == home_].groupby("team")[[col + "w", "wt"]].sum().reindex(range(1, NT)).fillna(0)
        return s_[col + "w"].to_numpy(), s_["wt"].to_numpy()

    sig2 = max(float(d["gf_b"].var()), 0.3)
    K_eb = float(np.mean([eb_k(*_sc(col_, hm_), sig2, 1.0, 30.0, default=6.0) for col_ in ("gf_b", "ga_b") for hm_ in (1, 0)]))
    K = max(K_eb * p["team_pull"], 0.1)   # empirical-Bayes strength x your smoothing multiplier

    def agg(col, home, prior):
        s_ = w[w["home"] == home].groupby("team")[[col + "w", "wt"]].sum().reindex(range(NT)).fillna(0)
        return ((s_[col + "w"] + K * prior) / (s_["wt"] + K)).to_numpy()

    att_h, def_h = agg("gf_b", 1, base_h), agg("ga_b", 1, base_a)
    att_a, def_a = agg("gf_b", 0, base_a), agg("ga_b", 0, base_h)
    ho = w["home"].to_numpy() == 1
    o, t, wt = w["opp"].to_numpy(int), w["team"].to_numpy(int), w["wt"].to_numpy()
    ease_rel = np.where(ho, def_a[o], def_h[o]) / np.where(ho, base_h, base_a)
    faced_rel = np.where(ho, att_a[o], att_h[o]) / np.where(ho, base_a, base_h)

    def mean_by(vals, mask):
        s_ = np.bincount(t[mask], vals[mask] * wt[mask], NT)
        c_ = np.bincount(t[mask], wt[mask], NT)
        return np.where(c_ > 0, s_ / np.maximum(c_, 1e-9), 1.0)

    return dict(att_h=att_h, att_a=att_a, def_h=def_h, def_a=def_a, base_h=base_h, base_a=base_a,
                ease_h=mean_by(ease_rel, ho), ease_a=mean_by(ease_rel, ~ho),
                faced_h=mean_by(faced_rel, ho), faced_a=mean_by(faced_rel, ~ho))


def fit_team_model(long, cutoff, p):
    """Blend of (a) recent-window venue strengths and (b) a long-memory, exponentially decayed estimate over the whole season
    (Dixon & Coles 1997 style). Short windows react fast; the long memory keeps them from over-reacting to 2-3 games."""
    a = _fit_team_core(long, cutoff, p, p["window"], None)
    w_long = float(p.get("team_long", 0.5))
    if w_long <= 0:
        return a
    b = _fit_team_core(long, cutoff, p, 38, max(2.0 * p["form_hl"], 6.0))
    return {k: (1 - w_long) * v + w_long * b[k] for k, v in a.items()}


_PHI = np.vectorize(lambda x: 0.5 * (1.0 + erf(x / np.sqrt(2.0))))


def team_desperation(long, cutoff):
    """D_desperation as match leverage, per team (index 1-20), from the table before `cutoff`: how much a win instead of a loss moves the
    probability of winning the title, finishing top 5 and surviving, given projected final points and the games left. It is large only
    when a team is near a line AND few games remain, ~0 early in the season and for teams already safe or doomed. Centred on the league
    average, so + = more at stake than usual, - = dead rubber."""
    des = np.zeros(21)
    d = long[long["round"] < cutoff].copy()
    if len(d) < 20:
        return des
    d["ga"] = d.groupby("fid")["gf"].transform("sum") - d["gf"]
    d["pts"] = np.where(d["gf"] > d["ga"], 3, np.where(d["gf"] == d["ga"], 1, 0))
    g = d.groupby("team").agg(pts=("pts", "sum"), n=("pts", "size")).reindex(range(1, 21)).fillna(0)
    pts, n = g["pts"].to_numpy(float), g["n"].to_numpy(float)
    rem = np.clip(38 - n, 0, None)
    ppg = (pts + 3 * 1.37) / (n + 3)                         # shrunk toward league-average points per game
    proj = pts + ppg * rem
    sd = 1.25 * np.sqrt(2 * np.maximum(rem, 1))              # uncertainty in the gap between two teams' final points
    raw = np.zeros(20)
    for i in range(20):
        if rem[i] <= 0:
            continue
        others = np.sort(np.delete(proj, i))[::-1]
        for k, wgt in DES_TARGETS:
            gap = proj[i] - others[k]
            raw[i] += wgt * float(_PHI((gap + 1.5) / sd[i]) - _PHI((gap - 1.5) / sd[i]))
    raw = np.clip(raw, 0, 1)
    des[1:21] = raw - raw.mean()
    return des


# =========================================================
# STRUCTURAL MODEL — player side
# =========================================================
def eb_k(sum_, cnt, sigma2, lo=1.0, hi=40.0, default=None):
    """Empirical-Bayes shrinkage weight (in units of `cnt`) by method of moments: k = sigma2 / tau2, where tau2 is the true
    between-unit variance left after subtracting sampling noise (Efron & Morris 1975; Morris 1983). Replaces hand-picked constants."""
    sum_, cnt = np.asarray(sum_, float), np.asarray(cnt, float)
    m = cnt > 0
    if m.sum() < 5 or sigma2 <= 0:
        return float(np.clip(2.0 if default is None else default, lo, hi))
    w = cnt[m]
    rates = sum_[m] / w
    mean_ = sum_[m].sum() / w.sum()
    var_obs = np.average((rates - mean_) ** 2, weights=w)
    noise = np.average(sigma2 / w, weights=w)
    tau2 = max(var_obs - noise, sigma2 / hi)
    return float(np.clip(sigma2 / tau2, lo, hi))


def exp_floor_div(mu, d, kmax=30):
    """Exact E[floor(X/d)] for X ~ Poisson(mu): expected value of 'one point per d events' scoring (GK saves / 3)."""
    mu = np.clip(np.asarray(mu, float), 1e-9, None)
    pk, out = np.exp(-mu), np.zeros_like(mu)
    for k in range(kmax + 1):
        out += pk * (k // d)
        pk = pk * mu / (k + 1)
    return out


def _eb_weights(S, E, prior, c):
    """Per-player empirical-Bayes prior weight for stat `c`, estimated separately for each position."""
    out = pd.Series(float(PRIOR_WEIGHT[c]), index=S.index)
    for pos_ in (1, 2, 3, 4):
        sel = (S["element_type"] == pos_).to_numpy()
        m = sel & (E.to_numpy() > 0.3)
        mu = float(prior.loc[pos_, c]) if pos_ in prior.index else 0.0
        if m.sum() >= 8 and mu > 0:
            out[sel] = eb_k(S.loc[m, c].to_numpy(), E.to_numpy()[m], mu, 0.5, 60.0, default=PRIOR_WEIGHT[c])
    return out


def _eb_min_k(M, col, pri, default=1.5):
    out = np.full(len(M), float(default))
    et_ = M["element_type"].to_numpy()
    wm = M["wm"].to_numpy()
    for pos_ in (1, 2, 3, 4):
        sel = et_ == pos_
        m = sel & (wm > 0.5)
        mu = float(np.mean(pri[sel])) if sel.any() else 0.0
        if m.sum() >= 8 and 0 < mu < 1:
            out[sel] = eb_k(M[col].to_numpy()[m], wm[m], mu * (1 - mu), 0.3, 10.0, default=default)
    return out


def _blend(h, p):
    xw = p["xg_weight"]
    if len(h) and h["expected_goals"].sum() > 0:
        return (xw * h["expected_goals"] + (1 - xw) * h["goals_scored"],
                xw * h["expected_assists"] + (1 - xw) * h["assists"])
    return h["goals_scored"], h["assists"]


def _stat_cols(h, p):
    g_b, a_b = _blend(h, p)
    has_x = len(h) > 0 and h["expected_goals"].sum() > 0
    gx = h["expected_goals"] if has_x else h["goals_scored"]          # pure xG / xA: the master equation's baseline rates
    ax = h["expected_assists"] if has_x else h["assists"]
    return {"g": g_b, "a": a_b, "gx": gx, "ax": ax, "dc": h["defensive_contribution"], "bonus": h["bonus"],
            "yc": h["yellow_cards"], "rc": h["red_cards"], "og": h["own_goals"],
            "pm": h["penalties_missed"], "sv": h["saves"]}


def season_block(h_all, players, cutoff, p):
    """Season-long, recency-weighted rates shrunk to position priors (the stable base)."""
    idx = pd.Index(players["id"].to_numpy(), name="id")
    et = players.set_index("id")["element_type"].reindex(idx)
    h = h_all[h_all["round"] < cutoff]
    w = 0.5 ** ((cutoff - 1 - h["round"]) / p["form_hl"])
    wm = 0.5 ** ((cutoff - 1 - h["round"]) / p["minutes_hl"])
    cols = _stat_cols(h, p)
    cols["thr"], cols["cre"] = h["threat"], h["creativity"]
    cols["pts"] = h["total_points"]
    W = pd.DataFrame({k: v * w for k, v in cols.items()})
    W["e90"] = w * h["minutes"] / 90.0
    W["id"] = h["id"]
    S = W.groupby("id").sum().reindex(idx).fillna(0)
    S["element_type"] = et.values
    pos_tot = S.groupby("element_type").sum()
    prior = pos_tot.div(pos_tot["e90"].replace(0, np.nan), axis=0).fillna(0)
    P = prior.reindex(S["element_type"].values)
    P.index = S.index
    E = S["e90"]

    def ratio(c, k=3.0):
        sm = (S[c] + k * P[c]) / (E + k)
        return (sm / P[c].replace(0, np.nan)).fillna(1.0).clip(0.25, 3.5) ** 0.8

    rg, ra = ratio("thr"), ratio("cre")     # threat / creativity tilt the goal / assist priors
    out = pd.DataFrame(index=idx)
    out["element_type"] = et.values
    for c in RATE_KEYS:
        Ec = E
        k = _eb_weights(S, Ec, prior, c) * p["prior_strength"]
        mult = rg if c in ("g", "gx") else ra if c in ("a", "ax") else 1.0
        out[c + "90"] = (S[c] + k * P[c] * mult) / (Ec + k)
    out["pts90"] = (S["pts"] + 3.0 * P["pts"]) / (E + 3.0)
    out["thr90"] = (S["thr"] + 3.0 * P["thr"]) / (E + 3.0)
    out["cre90"] = (S["cre"] + 3.0 * P["cre"]) / (E + 3.0)
    rt_, rc_ = out["thr90"] / P["thr"].replace(0, np.nan), out["cre90"] / P["cre"].replace(0, np.nan)
    out["role_f"] = (rt_ / (rt_ + rc_)).fillna(0.5).clip(0.15, 0.85)   # 1 = pure finisher, 0 = pure creator

    hm = pd.DataFrame({"id": h["id"], "wm": wm, "i60": (h["min_pm"] >= 60) * wm,
                       "isub": ((h["min_pm"] > 0) & (h["min_pm"] < 60)) * wm,
                       "stw": (h["min_pm"] >= 60) * h["min_pm"] * wm})
    M = hm.groupby("id").sum().reindex(idx).fillna(0)
    M["element_type"] = et.values
    pm_ = M.groupby("element_type").sum()
    pri60 = (pm_["i60"] / pm_["wm"].replace(0, np.nan)).fillna(0.3).reindex(M["element_type"].values).to_numpy()
    prisub = (pm_["isub"] / pm_["wm"].replace(0, np.nan)).fillna(0.1).reindex(M["element_type"].values).to_numpy()
    kp60 = _eb_min_k(M, "i60", pri60) * p["prior_strength"]
    kps = _eb_min_k(M, "isub", prisub) * p["prior_strength"]
    out["p60"] = (M["i60"].to_numpy() + kp60 * pri60) / (M["wm"].to_numpy() + kp60)
    out["psub"] = (M["isub"].to_numpy() + kps * prisub) / (M["wm"].to_numpy() + kps)
    out["mstart"] = (M["stw"] + 3 * 85.0) / (M["i60"] + 3)
    out["E90"] = E
    out["conf"] = E / (E + 5.0)
    out["form"] = h[h["round"] >= cutoff - 4].groupby("id")["total_points"].mean().reindex(idx).fillna(0)
    out["ppg"] = h.groupby("id")["total_points"].mean().reindex(idx).fillna(0)
    out["n_w"] = 0.0
    return out


def attack_share(h_all, players, cutoff, n_rounds=6):
    """Each player's share of his team's recent expected goal involvements (goals + assists if xGI is missing)."""
    idx = pd.Index(players["id"].to_numpy(), name="id")
    h = h_all[(h_all["round"] < cutoff) & (h_all["round"] >= cutoff - n_rounds)]
    if h.empty:
        return pd.Series(0.0, index=idx)
    v = h["expected_goal_involvements"] if h["expected_goal_involvements"].sum() > 0 else h["goals_scored"] + h["assists"]
    per = v.groupby(h["id"]).sum().reindex(idx).fillna(0)
    team = players.set_index("id")["team"].reindex(idx)
    tot = per.groupby(team.values).transform("sum")
    return (per / tot.replace(0, np.nan)).fillna(0.0)


def venue_features(h_all, base, cutoff, p, side):
    """Rates from the last N games (any venue), keeping only those played at `side` (1 home / 0 away); thin samples are
    pulled toward the season base."""
    idx = base.index
    ha = h_all[h_all["round"] < cutoff].copy()
    ha["rk"] = ha.groupby("id")["round"].rank(ascending=False, method="first")
    hv = ha[(ha["rk"] <= p["window"]) & ((ha["venue"] == side) | (ha["venue"] == 2))]
    W = pd.DataFrame(_stat_cols(hv, p))
    W["e90"] = hv["minutes"] / 90.0
    W["id"] = hv["id"]
    Sw = W.groupby("id").sum().reindex(idx).fillna(0)
    out = base.copy()
    kw = p["player_pull"]
    for c in RATE_KEYS:
        num, den = Sw[c] + kw * base[c + "90"], Sw["e90"] + kw
        out[c + "90"] = (num / den.replace(0, np.nan)).fillna(base[c + "90"])
    M = pd.DataFrame({"id": hv["id"], "n": 1.0, "i60": (hv["min_pm"] >= 60).astype(float),
                      "isub": ((hv["min_pm"] > 0) & (hv["min_pm"] < 60)).astype(float),
                      "stw": ((hv["min_pm"] >= 60) * hv["min_pm"]).astype(float)})
    M = M.groupby("id").sum().reindex(idx).fillna(0)
    km = p["minutes_pull"]
    for col, src in (("p60", "i60"), ("psub", "isub")):
        out[col] = ((M[src] + km * base[col]) / (M["n"] + km).replace(0, np.nan)).fillna(base[col])
    out["mstart"] = (M["stw"] + 2 * base["mstart"]) / (M["i60"] + 2)
    out["n_w"] = M["n"]
    return out


def extra_features(h_all, idx, cutoff, hl=3.0):
    """Recent-form signals for the learning layer. `_3` = last 3 gameweeks (flat), `_4` = last 4 (flat; shot-volume proxies for forwards),
    `_6` = last 6 with exponential decay (half-life `hl` gameweeks) so the latest games dominate and two-month-old games barely count."""
    h = h_all[h_all["round"] < cutoff]
    rk = h.groupby("id")["round"].rank(ascending=False, method="first")
    out = pd.DataFrame(index=idx)
    for k, decayed in ((3, False), (4, False), (6, True)):
        sel = h[rk <= k]
        w = 0.5 ** ((cutoff - 1 - sel["round"]) / max(hl, 0.5)) if decayed else pd.Series(1.0, index=sel.index)
        e90 = (w * sel["minutes"] / 90.0).groupby(sel["id"]).sum().reindex(idx).fillna(0)
        for c in (FWD_RATES_4 if k == 4 else EXTRA_RATES):
            out[f"{c}_{k}"] = (w * sel[c]).groupby(sel["id"]).sum().reindex(idx).fillna(0) / (e90 + 0.5)
        if k != 4:
            wsum = w.groupby(sel["id"]).sum()
            for name, col in ((f"pts_{k}", "total_points"), (f"mins_{k}", "min_pm"), (f"start_{k}", "start_pm")):
                out[name] = ((w * sel[col]).groupby(sel["id"]).sum() / wsum).reindex(idx).fillna(0)
    return out


# =========================================================
# STRUCTURAL MODEL — expected points for one fixture set
# =========================================================
def _z(s, n, k=3.0):
    """z-score across teams, shrunk toward 0 when few matches have been played."""
    s = pd.to_numeric(s, errors="coerce")
    sd = s.std(ddof=0)
    z = (s - s.mean()) / sd if sd and sd > 0 else s * 0.0
    return (z.fillna(0.0) * (n / (n + k))).fillna(0.0)


@st.cache_data(show_spinner=False, max_entries=8)
def team_stability(ctx, players, cutoff, p):
    """Team stability in [-1, 1] from data before `cutoff`: momentum, underlying play, clinical finishing/resilience,
    form of 70+ minute starters, lineup continuity, club size and discipline. Off-field news can't be read from the API:
    edit it by hand in the fixture editor."""
    ids = list(range(1, 21))
    comp = pd.DataFrame(0.0, index=ids, columns=STAB_KEYS)
    comp["auto"], comp["matches"], comp["hot_pct"] = 0.0, 0.0, 0.0
    long, h = ctx["long"], ctx["h"]
    d = long[long["round"] < cutoff].copy()
    if len(d) < 10:
        return comp
    xw = p["xg_weight"]
    d["gf_b"] = np.where(d["xg"].notna(), (1 - xw) * d["gf"] + xw * d["xg"], d["gf"])
    d["ga_b"] = d.groupby("fid")["gf_b"].transform("sum") - d["gf_b"]
    d["ga"] = d.groupby("fid")["gf"].transform("sum") - d["gf"]
    d["pts"] = np.where(d["gf"] > d["ga"], 3, np.where(d["gf"] == d["ga"], 1, 0))
    d["xgd"], d["gd"] = d["gf_b"] - d["ga_b"], d["gf"] - d["ga"]
    d["age"] = d.groupby("team")["round"].rank(ascending=False, method="first")
    d["w"] = 0.5 ** ((d["age"] - 1) / 3.0)

    def wmean(col):
        return (d[col] * d["w"]).groupby(d["team"]).sum() / d["w"].groupby(d["team"]).sum()

    n = d.groupby("team").size().reindex(ids).fillna(0)
    ppg, xgd, gd = wmean("pts"), wmean("xgd"), wmean("gd")
    size_z = _z(d.groupby("team")["xgd"].mean().reindex(ids), n)
    if "now_cost" in players.columns and pd.to_numeric(players["now_cost"], errors="coerce").notna().any():
        cost = (players.assign(c=pd.to_numeric(players["now_cost"], errors="coerce"))
                .groupby("team")["c"].apply(lambda s: s.nlargest(11).mean()))
        size_z = 0.5 * size_z + 0.5 * _z(cost.reindex(ids), 30.0)

    hh = h[h["round"] < cutoff]
    r6, r3, r5 = hh[hh["round"] >= cutoff - 6], hh[hh["round"] >= cutoff - 3], hh[hh["round"] >= cutoff - 5]
    mins = r6.groupby("id")["min_pm"].mean()
    pp = (r3["total_points"] / r3["n_played"]).groupby(r3["id"]).mean()
    r4 = hh[hh["round"] >= cutoff - 4]
    pp4 = (r4["total_points"] / r4["n_played"]).groupby(r4["id"]).mean()
    sf = pd.DataFrame({"mins": mins, "pts": pp, "form4": pp4}).join(hh.groupby("id")["team"].last())
    sf = sf.dropna(subset=["mins", "pts", "team"])
    sf["form4"] = sf["form4"].fillna(0)
    sf = sf[sf["mins"] >= 70]
    hot = (sf["form4"] > HOT_FORM).astype(float).groupby(sf["team"]).mean()   # share of 70+ minute starters in form
    squad = (sf["pts"] * sf["mins"]).groupby(sf["team"]).sum() / sf["mins"].groupby(sf["team"]).sum()

    sets = {}
    for (t, r), g in hh.loc[hh["min_pm"] >= 60, ["team", "round", "id"]].groupby(["team", "round"]):
        sets.setdefault(t, []).append((r, frozenset(g["id"])))
    cont = {}
    for t, lst in sets.items():
        s_ = [x for _, x in sorted(lst, key=lambda z: z[0])][-4:]
        j = [len(a & b) / max(len(a | b), 1) for a, b in zip(s_[:-1], s_[1:])]
        if j:
            cont[t] = float(np.mean(j))
    cont = pd.Series(cont, dtype=float)
    cards = ((r5["yellow_cards"] + 3 * r5["red_cards"]).groupby(r5["team"]).sum()
             / r5.groupby("team")["round"].nunique())

    comp["momentum"] = _z(ppg.reindex(ids), n)
    comp["underlying"] = _z(xgd.reindex(ids), n)
    comp["clinical"] = _z((gd - xgd).reindex(ids), n)
    comp["squad_form"] = _z(squad.reindex(ids), n)
    comp["continuity"] = _z(cont.reindex(ids), n)
    comp["club_size"] = size_z
    comp["discipline"] = -_z(cards.reindex(ids), n)
    comp["hot_share"] = _z(hot.reindex(ids), n)
    comp["hot_pct"] = (hot.reindex(ids).fillna(0) * 100).round(0)
    w = np.array([p.get(f"sw_{k}", STAB_DEFAULT_W[k]) for k in STAB_KEYS], float)
    raw = comp[STAB_KEYS].to_numpy() @ w / max(w.sum(), 1e-9)
    comp["auto"] = np.tanh(1.3 * raw)
    comp["matches"] = n
    return comp


def recipe_scores(comp, team_z):
    """Stability per team. For teams in `team_z`, some ingredient levels (z-scores vs the league) were set by hand; the score is the same
    fixed-weight blend as the automatic one, so raising any ingredient can only raise stability and lowering it can only lower it."""
    w = np.array([STAB_DEFAULT_W[k] for k in STAB_KEYS], float)
    w = w / w.sum()
    out = comp["auto"].copy()
    for t, ov in (team_z or {}).items():
        if t in comp.index:
            z = comp.loc[t, STAB_KEYS].to_numpy(float).copy()
            for i, k in enumerate(STAB_KEYS):
                if k in ov:
                    z[i] = ov[k]
            out.loc[t] = float(np.tanh(1.3 * (z @ w)))
    return out


def leader_scores(h_all, players, base, cutoff):
    """0-1 'captain / veteran' score: reliable starters who are among their team's best recent performers, plus age if known."""
    idx = base.index
    hh = h_all[(h_all["round"] < cutoff) & (h_all["round"] >= cutoff - 6)]
    pts = (hh["total_points"] / hh["n_played"]).groupby(hh["id"]).mean().reindex(idx).fillna(0)
    team = players.set_index("id")["team"].reindex(idx)
    fpct = pts.groupby(team).rank(pct=True).fillna(0)
    vet = pd.Series(0.5, index=idx)
    if "birth_date" in players.columns and players["birth_date"].notna().any():
        bd = pd.to_datetime(players.set_index("id")["birth_date"], errors="coerce").reindex(idx)
        age = (pd.Timestamp.today() - bd).dt.days / 365.25
        vet = ((age - 24) / 6).clip(0, 1).fillna(0.5)
    return (base["p60"].clip(0, 1) * (0.6 * fpct + 0.4 * vet)).clip(0, 1).rename("leader")


def hmpr_tables(ctx, cutoff, p):
    """Historical Match Performance Rating ingredients, from rounds before `cutoff`: for each player, points above their own
    average in games they started and played `hm_min`+ minutes, split by (same opponent, same opponent system, same difficulty tier)."""
    h = ctx["h"]
    empty = pd.DataFrame({"sum": pd.Series(dtype=float), "size": pd.Series(dtype=float)})
    tabs = {"n_base": pd.Series(dtype=float), "team": empty, "form": empty, "fdr": empty, "exp_form": {}}
    q = h[(h["round"] < cutoff) & h["hm_ok"] & (h["min_pm"] >= p["hm_min"])]
    if not q.empty:
        g = q.groupby("id")["total_points"]
        q = q.assign(dev=q["total_points"] - q["id"].map(g.mean()), hm_opp=q["hm_opp"].astype(int),
                     hm_fdr=q["hm_fdr"].astype(int))
        tabs["n_base"] = g.size().astype(float)
        tabs["team"] = q.groupby(["id", "hm_opp"])["dev"].agg(["sum", "size"])
        tabs["form"] = q.dropna(subset=["hm_form"]).groupby(["id", "hm_form"])["dev"].agg(["sum", "size"])
        tabs["fdr"] = q.groupby(["id", "hm_fdr"])["dev"].agg(["sum", "size"])
    tabs["k"] = HM_SHRINK
    if not q.empty:
        sig2 = max(float(q["dev"].var()), 0.5)
        tabs["k"] = tuple(eb_k(t_["sum"].to_numpy(), t_["size"].to_numpy(), sig2, 1.0, 40.0, default=HM_SHRINK[i])
                          if len(t_) else HM_SHRINK[i]
                          for i, t_ in enumerate([tabs["team"], tabs["form"], tabs["fdr"]]))
    ft = ctx["form_tbl"]
    ft = ft[ft["round"] < cutoff].sort_values(["team", "round"])
    for t, gdf in ft.groupby("team"):
        last = list(gdf["formation"].tail(3))
        tabs["exp_form"][int(t)] = max(set(last), key=lambda f: (last.count(f), max(i for i, x in enumerate(last) if x == f)))
    return tabs



# =========================================================
# BOOKMAKER PRIORS (The Odds API): implied goals, clean sheets and scorer probabilities
# =========================================================
def _norm(x):
    x = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", x)).strip()


def match_team(name, teams):
    n = _norm(name)
    n = TEAM_ALIAS.get(n, n)
    full = {int(i): _norm(nm) for i, nm in zip(teams["id"], teams["name"])}
    for tid, cn in full.items():
        if cn == n:
            return tid
    for tid, cn in {int(i): _norm(nm) for i, nm in zip(teams["id"], teams["short_name"])}.items():
        if cn == n:
            return tid
    best = difflib.get_close_matches(n, list(full.values()), n=1, cutoff=0.6)
    return next((tid for tid, cn in full.items() if best and cn == best[0]), None)


def _prep_players(sub):
    sub = sub.copy()
    sub["_full"] = sub["first_name"].map(_norm) + " " + sub["second_name"].map(_norm)
    sub["_web"] = sub["web_name"].map(_norm)
    sub["_sur"] = sub["second_name"].map(lambda v: (_norm(v).split() or [""])[-1])
    return sub


def match_player(name, sub, cache):
    n = _norm(name)
    if n in cache:
        return cache[n]
    pid = None
    for hit in (sub[sub["_full"] == n], sub[sub["_web"] == n]):
        if pid is None and len(hit) == 1:
            pid = int(hit["id"].iloc[0])
    if pid is None and n:
        last = n.split()[-1]
        hit = sub[(sub["_web"] == last) | (sub["_sur"] == last)]
        if len(hit) == 1:
            pid = int(hit["id"].iloc[0])
    if pid is None and n:
        best = difflib.get_close_matches(n, list(sub["_full"]), n=1, cutoff=0.85)
        if best:
            pid = int(sub.loc[sub["_full"] == best[0], "id"].iloc[0])
    cache[n] = pid
    return pid


def _implied(price):
    try:
        x = float(price)
    except Exception:
        return np.nan
    return 1.0 / x if x > 1.0 else np.nan


_GK = np.arange(0, 13)
_GF = np.array([factorial(int(k)) for k in _GK], float)


def _pois_cdf(k, lam):
    out, term = np.zeros_like(lam), np.exp(-lam)
    for n in range(int(k) + 1):
        out = out + term
        term = term * lam / (n + 1)
    return out


def solve_lambdas(obs):
    """Find home/away expected goals (independent Poisson; Maher 1982) that best reproduce the bookmakers' fair probabilities:
    1X2, over/under lines, team totals and both-teams-to-score. Coarse grid, then a fine grid around the best cell."""
    def grid_err(gh, ga):
        lh, la = gh[:, None], ga[None, :]
        err, used = np.zeros((len(gh), len(ga))), False
        if obs.get("h2h") is not None:
            ph = np.exp(-gh)[:, None] * gh[:, None] ** _GK / _GF
            pa = np.exp(-ga)[:, None] * ga[:, None] ** _GK / _GF
            sh = np.concatenate([np.zeros((len(gh), 1)), np.cumsum(ph, axis=1)[:, :-1]], axis=1)   # P(home <= k-1)
            sa = np.concatenate([np.zeros((len(ga), 1)), np.cumsum(pa, axis=1)[:, :-1]], axis=1)   # P(away <= k-1)
            p_h, p_a, p_d = ph @ sa.T, sh @ pa.T, ph @ pa.T
            oh, od, oa = obs["h2h"]
            err += (p_h - oh) ** 2 + (p_d - od) ** 2 + (p_a - oa) ** 2
            used = True
        for line, po in obs.get("over", []):
            err += ((1 - _pois_cdf(np.floor(line), lh + la)) - po) ** 2
            used = True
        for line, po in obs.get("home_over", []):
            err += ((1 - _pois_cdf(np.floor(line), lh + 0 * la)) - po) ** 2
            used = True
        for line, po in obs.get("away_over", []):
            err += ((1 - _pois_cdf(np.floor(line), la + 0 * lh)) - po) ** 2
            used = True
        if obs.get("btts") is not None:
            err += (((1 - np.exp(-lh)) * (1 - np.exp(-la))) - obs["btts"]) ** 2
            used = True
        if used and not (obs.get("over") or obs.get("home_over") or obs.get("btts") is not None):
            err += 0.003 * ((lh + la - 2.7) / 2.0) ** 2        # only 1X2 known: weak prior on total goals
        return err, used

    g = np.arange(0.15, 4.6, 0.05)
    err, used = grid_err(g, g)
    if not used:
        return None
    i, j = np.unravel_index(np.argmin(err), err.shape)
    gh = np.arange(max(0.05, g[i] - 0.1), g[i] + 0.1, 0.005)
    ga = np.arange(max(0.05, g[j] - 0.1), g[j] + 0.1, 0.005)
    err2, _ = grid_err(gh, ga)
    i2, j2 = np.unravel_index(np.argmin(err2), err2.shape)
    return float(gh[i2]), float(ga[j2])


def parse_event(ev, teams, players, margin):
    """One Odds API event-odds JSON -> fair probabilities per market, implied team goals, and matched player quotes."""
    h_id, a_id = match_team(ev.get("home_team", ""), teams), match_team(ev.get("away_team", ""), teams)
    sub = _prep_players(players[players["team"].isin([h_id, a_id])])
    cache = {}
    h2h, totals, tt, btts, goal, assist, unmatched = [], {}, {}, [], {}, {}, []

    def pair(d, a, b):
        return d[a] / (d[a] + d[b]) if (a in d and b in d and not (np.isnan(d[a]) or np.isnan(d[b]))) else np.nan

    for bk in ev.get("bookmakers", []):
        for m in bk.get("markets", []):
            key, outs = m.get("key"), m.get("outcomes", [])
            if key in ("h2h", "h2h_3_way"):
                pr = {}
                for o in outs:
                    nm = o.get("name")
                    tag = "H" if nm == ev.get("home_team") else "A" if nm == ev.get("away_team") else "D" if str(nm).lower() == "draw" else None
                    if tag:
                        pr[tag] = _implied(o.get("price"))
                if len(pr) == 3 and not any(np.isnan(v) for v in pr.values()):
                    tot = sum(pr.values())
                    h2h.append((pr["H"] / tot, pr["D"] / tot, pr["A"] / tot))
            elif key == "totals":
                by = {}
                for o in outs:
                    if o.get("point") is not None:
                        by.setdefault(float(o["point"]), {})[str(o.get("name")).lower()] = _implied(o.get("price"))
                for line, d in by.items():
                    v = pair(d, "over", "under")
                    if not np.isnan(v):
                        totals.setdefault(line, []).append(v)
            elif key in ("team_totals", "alternate_team_totals"):
                by = {}
                for o in outs:
                    tid = match_team(o.get("description", ""), teams)
                    if tid in (h_id, a_id) and o.get("point") is not None:
                        by.setdefault((tid, float(o["point"])), {})[str(o.get("name")).lower()] = _implied(o.get("price"))
                for k_, d in by.items():
                    v = pair(d, "over", "under")
                    if not np.isnan(v):
                        tt.setdefault(k_, []).append(v)
            elif key == "btts":
                d = {str(o.get("name")).lower(): _implied(o.get("price")) for o in outs}
                v = pair(d, "yes", "no")
                if not np.isnan(v):
                    btts.append(v)
            elif key == "player_goal_scorer_anytime":
                for o in outs:
                    if str(o.get("name")).lower() in ("no", "under"):
                        continue
                    nm = o.get("description") or o.get("name")
                    pid, pr_ = (match_player(nm, sub, cache) if nm else None), _implied(o.get("price"))
                    if pid is None:
                        unmatched.append(str(nm))
                    elif not np.isnan(pr_):
                        goal.setdefault(pid, []).append(pr_)
            elif key == "player_assists":
                by = {}
                for o in outs:
                    if o.get("description") and o.get("point") is not None and float(o["point"]) <= 0.5:
                        pid = match_player(o["description"], sub, cache)
                        if pid is None:
                            unmatched.append(str(o["description"]))
                        else:
                            by.setdefault(pid, {})[str(o.get("name")).lower()] = _implied(o.get("price"))
                for pid, d in by.items():
                    v = pair(d, "over", "under")
                    if np.isnan(v) and "over" in d and not np.isnan(d["over"]):
                        v = d["over"] * (1 - margin)
                    if not np.isnan(v):
                        assist.setdefault(pid, []).append(v)

    obs, src = {}, []
    if h2h:
        obs["h2h"] = tuple(np.mean(h2h, axis=0)); src.append("1X2")
    if totals:
        obs["over"] = sorted([(ln, float(np.mean(v))) for ln, v in totals.items()], key=lambda x: abs(x[0] - 2.5))[:3]; src.append("O/U")
    ho = [(ln, float(np.mean(v))) for (tid, ln), v in tt.items() if tid == h_id]
    ao = [(ln, float(np.mean(v))) for (tid, ln), v in tt.items() if tid == a_id]
    if ho or ao:
        obs["home_over"], obs["away_over"] = ho[:3], ao[:3]; src.append("team totals")
    if btts:
        obs["btts"] = float(np.mean(btts)); src.append("BTTS")
    return {"h": h_id, "a": a_id, "lam": solve_lambdas(obs) if obs else None, "sources": src,
            "goal": goal, "assist": {k: float(np.mean(v)) for k, v in assist.items()}, "unmatched": unmatched,
            "pwin": tuple(np.mean(h2h, axis=0)) if h2h else None}


def build_market(events, players, teams, margin, gw=None):
    """events: [{'fid','h','a','odds': event JSON}] -> (market dict for predict_gw, per-fixture info table, meta)."""
    short = dict(zip(teams["id"].astype(int), teams["short_name"]))
    mkt, rows, unmatched = {"team": {}, "goal": {}, "assist": {}, "dprob": {}}, [], 0
    for e in events:
        r = parse_event(e["odds"], teams, players, margin)
        fid, hid, aid = e["fid"], e["h"], e["a"]
        unmatched += len(r["unmatched"])
        lh = la = np.nan
        if r["lam"] is not None:
            lh, la = r["lam"]
            mkt["team"][(fid, hid)], mkt["team"][(fid, aid)] = (lh, la), (la, lh)
        for pid, lst in r["goal"].items():
            mkt["goal"][(pid, fid)] = float(-np.log(1 - min(float(np.mean(lst)) * (1 - margin), 0.95)))
        for pid, pa in r["assist"].items():
            mkt["assist"][(pid, fid)] = float(-np.log(1 - min(pa, 0.95)))
        label = f"{short.get(hid, hid)} v {short.get(aid, aid)}"
        pw = r.get("pwin")
        ph_, pa_ = (pw[0], pw[2]) if pw else (np.nan, np.nan)
        dh = da = 0.0
        op = opening_win(gw, label) if (gw is not None and pw) else None
        if op and op[0] > 0 and op[1] > 0:
            dh, da = ph_ / (op[0] / 100.0) - 1, pa_ / (op[1] / 100.0) - 1          # current / opening implied probability - 1
        mkt["dprob"][(fid, hid)], mkt["dprob"][(fid, aid)] = dh, da
        rows.append({"Fixture": label, "Home win %": ph_ * 100, "Away win %": pa_ * 100, "Home xG (market)": lh, "Away xG (market)": la,
                     "Home CS %": np.exp(-la) * 100 if r["lam"] else np.nan, "Away CS %": np.exp(-lh) * 100 if r["lam"] else np.nan,
                     "Over 2.5 %": (1 - _pois_cdf(2, np.array([lh + la]))[0]) * 100 if r["lam"] else np.nan,
                     "Scorers matched": len(r["goal"]), "Sources": ", ".join(r["sources"]) or "none"})
    return mkt, pd.DataFrame(rows), {"unmatched": unmatched}


def _odds_get(path, key, params):
    r = requests.get(f"{ODDS_HOST}{path}", params={"apiKey": key, **params}, timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"Odds API returned {r.status_code}: {r.text[:160]}")
    return r.json(), {k: r.headers.get(k) for k in ("x-requests-remaining", "x-requests-used", "x-requests-last")}


@st.cache_data(ttl=3 * 3600, show_spinner=False)
def odds_cached(key, path, params_items):
    return _odds_get(path, key, dict(params_items))


def fetch_gw_odds(key, regions, fx_gw, teams):
    """Lists events (free) and pulls odds only for events matching this gameweek's fixtures (costs credits per market returned)."""
    events, _ = odds_cached(key, f"/sports/{ODDS_SPORT}/events", ())
    pairs = {(int(h), int(a)): int(f) for f, h, a in zip(fx_gw["id"], fx_gw["team_h"], fx_gw["team_a"])}
    out, hdr = [], {}
    for ev in events:
        h, a = match_team(ev.get("home_team", ""), teams), match_team(ev.get("away_team", ""), teams)
        fid = pairs.get((h, a))
        if fid is None:
            continue
        js, hdr = odds_cached(key, f"/sports/{ODDS_SPORT}/events/{ev['id']}/odds",
                              (("markets", ODDS_MARKETS), ("oddsFormat", "decimal"), ("regions", regions)))
        out.append({"fid": fid, "h": h, "a": a, "odds": js})
    return out, hdr


def opening_win(gw, label):
    """First logged fair win probabilities (home %, away %) for this fixture: our own 'opening' line, since free plans have no line history."""
    try:
        d = pd.read_csv("fpl_odds_history.csv")
        d = d[(d["gw"] == gw) & (d["Fixture"] == label)].sort_values("fetched_at")
        return (float(d["Home win %"].iloc[0]), float(d["Away win %"].iloc[0])) if len(d) else None
    except Exception:
        return None


def log_market(gw, info):
    """Appends each fetch to fpl_odds_history.csv so bookmaker priors can be back-tested once a few gameweeks have been collected."""
    try:
        d = info.copy()
        d.insert(0, "gw", gw)
        d.insert(0, "fetched_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        d.to_csv("fpl_odds_history.csv", mode="a", header=not os.path.exists("fpl_odds_history.csv"), index=False)
    except Exception:
        pass


@st.cache_data(show_spinner=False, max_entries=8)
def matchup_tables(ctx, cutoff, p):
    """Opponent-vulnerability matrix: what each team has conceded to attackers of each position (DEF/MID/FWD) in goals, assists, threat,
    creativity and FPL points, decayed by recency and shrunk toward the league average. 1.0 = league average. `dev` divides by the
    opponent's overall figure, isolating position-specific leaks. (FPL data has no left/right flank information, so this is by position
    and by type of output, not by side of the pitch.)"""
    NT = 21
    neutral = {"idx": {s_: np.ones((NT, 5)) for s_ in MM_STATS}, "dev": {s_: np.ones((NT, 5)) for s_ in MM_STATS}}
    h = ctx["h"]
    d = h[(h["round"] < cutoff) & h["hm_opp"].notna() & (h["minutes"] > 0)]
    if len(d) < 200:
        return neutral
    g_b, a_b = _blend(d, p)
    vals = pd.DataFrame({"opp": d["hm_opp"].astype(int), "round": d["round"], "pos": d["element_type"].astype(int),
                         "g": g_b, "a": a_b, "thr": d["threat"], "cre": d["creativity"], "pts": d["total_points"]})
    per = vals.groupby(["opp", "round", "pos"])[MM_STATS].sum().reset_index()
    tot = vals.groupby(["opp", "round"])[MM_STATS].sum().reset_index()
    tot["pos"] = 0                                             # 0 = all positions together
    per = pd.concat([per, tot], ignore_index=True)
    w = 0.5 ** ((cutoff - 1 - per["round"]) / max(2.0 * p["form_hl"], 3.0))
    K = 3.0
    idx = {s_: np.ones((NT, 5)) for s_ in MM_STATS}
    for s_ in MM_STATS:
        for pos in range(5):
            sub = per[per["pos"] == pos]
            if sub.empty:
                continue
            ww = w[sub.index]
            m = max(float(sub[s_].mean()), 1e-9)
            val = (((sub[s_] * ww).groupby(sub["opp"]).sum() + K * m) / (ww.groupby(sub["opp"]).sum() + K)) / m
            idx[s_][val.index.to_numpy(int), pos] = val.to_numpy()
    dev = {s_: idx[s_] / np.maximum(idx[s_][:, [0]], 1e-6) for s_ in MM_STATS}
    return {"idx": idx, "dev": dev}


def matchup_view(mm, fx_g, code):
    """Readable matrix for the target gameweek: for each attacking side and position, how much the opponent concedes (1.00 = league average)."""
    rows = []
    for _, f in fx_g.iterrows():
        for atk, dfn, ven in ((int(f["team_h"]), int(f["team_a"]), "H"), (int(f["team_a"]), int(f["team_h"]), "A")):
            for pos, nm in ((2, "DEF"), (3, "MID"), (4, "FWD")):
                rows.append({"Attackers": f"{code[atk]} {nm} ({ven})", "Facing": code[dfn],
                             "Goals conceded": mm["idx"]["g"][dfn, pos], "Assists conceded": mm["idx"]["a"][dfn, pos],
                             "Threat conceded": mm["idx"]["thr"][dfn, pos], "Creativity conceded": mm["idx"]["cre"][dfn, pos],
                             "FPL pts conceded": mm["idx"]["pts"][dfn, pos], "Goals leak vs team norm": mm["dev"]["g"][dfn, pos]})
    return pd.DataFrame(rows)


def _hm_lookup(tab, a, b):
    n = len(a)
    if tab is None or len(tab) == 0:
        return np.zeros(n), np.zeros(n)
    r = tab.reindex(pd.MultiIndex.from_arrays([a, b]))
    return r["sum"].fillna(0).to_numpy(float), r["size"].fillna(0).to_numpy(float)


def availability(players, fixture_gw, next_gw):
    ids = players["id"].to_numpy()
    if next_gw is None or fixture_gw < next_gw:
        return pd.Series(1.0, index=ids)
    chance = pd.to_numeric(players["chance_of_playing_next_round"], errors="coerce") / 100.0
    s = players["status"]
    a_next = np.select([s.eq("u"), s.eq("i"), s.eq("s"), s.eq("d")],
                       [0.0, chance.fillna(0.0), chance.fillna(0.0), chance.fillna(0.75)],
                       default=chance.fillna(1.0)).astype(float)
    a = 1 - (1 - a_next) * (0.6 ** (fixture_gw - next_gw))
    return pd.Series(np.where(s.eq("u"), 0.0, a), index=ids)


def nb_tail(mu, limit, r=NB_DISPERSION):
    """P(actions >= limit) for a negative binomial with mean mu."""
    mu = np.clip(mu, 1e-6, None)
    pk, q, cdf = (r / (r + mu)) ** r, mu / (r + mu), np.zeros_like(mu)
    for k in range(13):
        cdf += np.where(k < limit, pk, 0.0)
        pk = pk * (k + r) / (k + 1) * q
    return np.where(limit > 0, np.clip(1 - cdf, 0, 1), 0.0)


# =========================================================
# MASTER xP EQUATION: context multipliers and micro modifiers
# =========================================================
# w1 (squad-fitness morale) defaults to 0: absences are handled more precisely by the squad-availability redistribution below.
MASTER_DEFAULTS = dict(master=True, extras=True, w_venue=0.5, k1=0.5, mom_cap=0.10, w1=0.0, w2=0.10, w3=0.5,
                       sigma_disc=0.3, psi=0.15, ctx_pow=1.0, k_reg=300, use_career=True)


def momentum_array(long, cutoff, p, n=5, decay=0.3):
    """M_momentum = 1 + cap * tanh(k1 * sum_t (1-decay)^(t-1) * (xGD_t - team's season xGD)) over the last n matches (t = 1 is the latest).
    Built on underlying play trending above or below the team's own level. The GD - xGD version rewards finishing luck, which mean-reverts,
    so it pointed the wrong way."""
    arr = np.ones(21)
    d = long[long["round"] < cutoff].copy()
    if d.empty:
        return arr
    d["gd"] = d["gf"] - (d.groupby("fid")["gf"].transform("sum") - d["gf"])
    xo = d.groupby("fid")["xg"].transform("sum") - d["xg"]
    d["xgd"] = np.where(d["xg"].notna() & xo.notna(), d["xg"] - xo, d["gd"])
    d["dev"] = d["xgd"] - d.groupby("team")["xgd"].transform("mean")
    d["t"] = d.groupby("team")["round"].rank(ascending=False, method="first")
    nplayed = d.groupby("team")["round"].transform("size")
    d = d[d["t"] <= n]
    shrink = nplayed / (nplayed + 5.0)                       # a trend over 3 games of season means little
    s_ = ((1 - decay) ** (d["t"] - 1) * d["dev"] * shrink[d.index]).groupby(d["team"]).sum()
    arr[s_.index.to_numpy(int)] = 1 + p["mom_cap"] * np.tanh(p["k1"] * s_.to_numpy())
    return arr


def error_indices(h, cutoff, p):
    """Relative tactical-error / discipline rates by team (1.0 = league average). FPL has no touches or dispossessions, so the proxy is
    (yellows + 3 x reds + 2 x own goals) per match, decayed; `err_def` uses only the team's defenders and keeper."""
    one = np.ones(21)
    hh = h[(h["round"] < cutoff) & (h["round"] >= cutoff - 8)]
    if hh.empty:
        return one, one
    hl = max(2.0 * p["form_hl"], 3.0)

    def idx(sub):
        g = sub.assign(ev=sub["yellow_cards"] + 3 * sub["red_cards"] + 2 * sub["own_goals"]).groupby(["team", "round"]).agg(
            ev=("ev", "sum"), n=("n_played", "first")).reset_index()
        g["rate"], g["w"] = g["ev"] / g["n"], 0.5 ** ((cutoff - 1 - g["round"]) / hl)
        team = (g["rate"] * g["w"]).groupby(g["team"]).sum() / g["w"].groupby(g["team"]).sum()
        nr = g.groupby("team")["round"].nunique()
        rel = 1 + (team / max(float(team.mean()), 1e-9) - 1) * nr / (nr + 4.0)     # shrunk toward 1 for short samples
        out = np.ones(21)
        out[rel.index.to_numpy(int)] = rel.clip(0.5, 2.0).to_numpy()
        return out

    return idx(hh), idx(hh[hh["element_type"] <= 2])


def sfit_array(players):
    """S_fit: minutes-weighted share of each team's 14 most-used players who are fit for the upcoming gameweek (NaN = unknown)."""
    out = np.full(21, np.nan)
    chance = pd.to_numeric(players["chance_of_playing_next_round"], errors="coerce") / 100.0
    st_ = players["status"]
    av = np.select([st_.eq("u"), st_.eq("i"), st_.eq("s"), st_.eq("d")], [0.0, chance.fillna(0.0), chance.fillna(0.0), chance.fillna(0.75)],
                   default=chance.fillna(1.0)).astype(float)
    df = pd.DataFrame({"team": players["team"].to_numpy(), "av": av, "min": pd.to_numeric(players["minutes"], errors="coerce").fillna(0).to_numpy()})
    for t, g in df.groupby("team"):
        top = g.nlargest(14, "min")
        out[int(t)] = float(np.average(top["av"], weights=top["min"] + 1.0))
    return out


def finishing_table(h, players, cutoff, career, p):
    """alpha_finish = theta * career (GS/xG) + (1 - theta) * season (GS/xG), theta = N_shots / (N_shots + K).  FPL publishes no shot counts, so
    N_shots is estimated as career xG / 0.10.  Ratios are lightly shrunk toward 1 (3 pseudo-goals) and clipped.  beta_assist = the finishing
    quality of the player's teammates, weighted by their share of the team's recent xG (no pass-receiver data exists)."""
    idx = pd.Index(players["id"].to_numpy(), name="id")
    et = players.set_index("id")["element_type"].reindex(idx)
    tm = players.set_index("id")["team"].reindex(idx)
    hh = h[h["round"] < cutoff]
    G = hh.groupby("id")["goals_scored"].sum().reindex(idx).fillna(0)
    X = hh.groupby("id")["expected_goals"].sum().reindex(idx).fillna(0)
    k = 3.0
    s_ratio = (G + k) / (X + k)
    c_ratio, theta = s_ratio, pd.Series(0.0, index=idx)
    if career is not None and len(career):
        cr = career.reindex(idx)
        Gc, Xc = cr["goals_c"].fillna(0), cr["xg_c"].fillna(0)
        c_ratio = pd.Series(np.where(Xc > 0, (Gc + k) / (Xc + k), s_ratio), index=idx)
        shots = Xc / 0.10
        theta = shots / (shots + p["k_reg"])
    alpha = (theta * c_ratio + (1 - theta) * s_ratio).clip(0.6, 1.5)
    alpha[et == 1] = 1.0
    r6 = hh[hh["round"] >= cutoff - 6]
    w_ = r6.groupby("id")["expected_goals"].sum().reindex(idx).fillna(0) + 0.05
    num, den = (w_ * alpha).groupby(tm).transform("sum"), w_.groupby(tm).transform("sum")
    beta = ((num - w_ * alpha) / (den - w_)).where((den - w_) > 1e-9, 1.0).clip(0.7, 1.3)
    return pd.DataFrame({"alpha": alpha, "beta": beta})


def build_ctxp(ctx, players, cutoff, fixture_gw, p, sfit=None, dprob=None):
    if not p.get("master", True):
        return None
    fin = finishing_table(ctx["h"], players, cutoff, ctx.get("career"), p)
    ea, ed = error_indices(ctx["h"], cutoff, p)
    return {"mom": momentum_array(ctx["long"], cutoff, p), "err_all": ea, "err_def": ed, "sfit": sfit, "dprob": dprob or {},
            "alpha": fin["alpha"], "beta": fin["beta"], "p": p}


def _fetch_career(pid):
    s_ = make_session()
    for base in (DRAFT, FPL):
        try:
            hp = s_.get(f"{base}/element-summary/{pid}", timeout=15).json().get("history_past") or []
            if hp:
                g_, x_ = 0.0, 0.0
                for r in hp:
                    xg = float(r.get("expected_goals") or 0)
                    if xg > 0:                          # only seasons that have xG, so goals and xG stay comparable
                        g_, x_ = g_ + float(r.get("goals_scored") or 0), x_ + xg
                return pid, g_, x_
        except Exception:
            continue
    return pid, None, None


@st.cache_data(ttl=7 * 86400, show_spinner="Loading career goals and xG (one-time, can take a minute or two)…")
def load_career(ids: tuple):
    with ThreadPoolExecutor(max_workers=12) as ex:
        res = list(ex.map(_fetch_career, ids))
    rows = [{"id": pid, "goals_c": g_, "xg_c": x_} for pid, g_, x_ in res if g_ is not None]
    return pd.DataFrame(rows).set_index("id") if rows else pd.DataFrame()


RES_COLS = ["xPts", "xPts0", "hm_adj", "u_team", "u_form", "u_diff", "n_team", "n_form", "n_diff",
            "xMins", "cs_team", "xGC", "Fix", "xG", "xA", "pdc", "g90", "a90", "dc90", "n_w",
            "p_app", "p_goal", "p_ast", "p_cs", "p_dc", "p_oth", "n_fix", "home", "stab_self", "stab_opp",
            "opp_att", "opp_def", "team_def", "lam_for", "mm_g", "mm_a", "mm_pts", "mm_idx", "mm_dev", "role_f", "mk_cs", "mk_goal", "p60_eb", "p60_ml", "p60_used",
            "om_att", "om_ratio", "a_fin", "b_ast", "m_mom", "m_mor", "g_ven",
            "des_self", "des_opp", "miss_self", "miss_opp"] + DESIGN_COLS


def predict_gw(feat_h, feat_a, players, fx_gw, model, fixture_gw, next_gw, focus, code, use_defcon=True,
               stab_auto=None, stab_pairs=None, coefs=None, leader=None, kappa=0.0, coefs_dev=None, hm=None, hcoefs=None,
               mm=None, mkt=None, mkt_w=0.0, mins_ml=None, mins_w=0.0, ctxp=None, des=None, dcoefs=None, share=None, avail=None):
    """Expected points per player. The auto stability score acts through `coefs` (fitted from past data, so it only adds what the
    team model doesn't already know); your hand edits (final - auto) act through `coefs_dev`. `kappa` amplifies both for captains/veterans.
    `mkt` (optional) holds bookmaker-implied team goals and player scoring odds, blended in with weight `mkt_w` before any ML correction.
    `des` = match stakes per team, acting through fitted `dcoefs` (the master equation's D term). `share` + `avail` redistribute absent players'
    attacking output to teammates (live forecasts only). xPts0 = forecast with no fitted context effects."""
    idx = pd.Index(players["id"].to_numpy(), name="id")
    if fx_gw.empty:
        res = pd.DataFrame(0.0, index=idx, columns=RES_COLS)
        res["Opponent"], res["FDR"], res["OppSystem"] = "Blank GW", 3.0, "–"
        return res
    stab_auto = np.zeros(21) if stab_auto is None else np.asarray(stab_auto, float)
    stab_pairs = stab_pairs or {}
    coefs = np.zeros(6) if coefs is None else np.asarray(coefs, float)[:6]
    coefs_dev = np.zeros(6) if coefs_dev is None else np.asarray(coefs_dev, float)[:6]
    hcoefs = np.zeros(3) if hcoefs is None else np.asarray(hcoefs, float)[:3]
    des = np.zeros(21) if des is None else np.asarray(des, float)
    dcoefs = np.zeros(2) if dcoefs is None else np.asarray(dcoefs, float)[:2]
    if ctxp is not None:
        coefs_dev = np.zeros(6)   # your stability edits act through the master equation's morale term (B_mgr) instead
    home_rows = pd.DataFrame({"fid": fx_gw["id"], "team": fx_gw.team_h, "opp": fx_gw.team_a, "home": 1,
                              "fdr": fx_gw.get("team_h_difficulty", 3)})
    away_rows = pd.DataFrame({"fid": fx_gw["id"], "team": fx_gw.team_a, "opp": fx_gw.team_h, "home": 0,
                              "fdr": fx_gw.get("team_a_difficulty", 3)})
    rows = pd.concat([home_rows, away_rows], ignore_index=True)
    rows["n_fix"] = rows.groupby("team")["team"].transform("size")
    t, o, fids = rows["team"].to_numpy(int), rows["opp"].to_numpy(int), rows["fid"].to_numpy()
    rows["s_self"] = [stab_pairs.get((f, a), stab_auto[a]) for f, a in zip(fids, t)]
    rows["s_opp"] = [stab_pairs.get((f, b), stab_auto[b]) for f, b in zip(fids, o)]
    rows["a_self"], rows["a_opp"] = stab_auto[t], stab_auto[o]
    rows["des_self"], rows["des_opp"] = des[t], des[o]
    rows["oform"] = rows["opp"].map((hm or {}).get("exp_form", {}))
    ish = rows["home"].to_numpy() == 1
    bh, ba = model["base_h"], model["base_a"]
    base_s, base_o = np.where(ish, bh, ba), np.where(ish, ba, bh)
    att_self = np.where(ish, model["att_h"][t], model["att_a"][t])
    att_opp = np.where(ish, model["att_a"][o], model["att_h"][o])
    def_self = np.where(ish, model["def_h"][t], model["def_a"][t])
    def_opp = np.where(ish, model["def_a"][o], model["def_h"][o])
    rows["lam_ag"] = att_opp * def_self / base_o
    lam_for_s = att_self * def_opp / base_s
    rows["lam_for"] = lam_for_s
    rows["opp_att"], rows["opp_def"], rows["team_def"] = att_opp / base_o, def_opp / base_s, def_self / base_o
    ease = np.where(ish, model["ease_h"][t], model["ease_a"][t])
    faced = np.where(ish, model["faced_h"][t], model["faced_a"][t])
    rows["att_mult"] = np.clip((def_opp / base_s) / ease, 0.5, 2.0)
    rows["shot_mult"] = np.clip((att_opp / base_o) / faced, 0.5, 2.0)
    if ctxp is not None:   # gamma_venue = 1 + (team's rating at this venue / opponent's overall baseline - 1) * w_venue
        wv = ctxp["p"]["w_venue"]
        r_att = np.where(ish, model["att_h"][t], model["att_a"][t]) / (0.5 * (model["def_h"][o] + model["def_a"][o]))
        r_def = (0.5 * (model["att_h"][o] + model["att_a"][o])) / np.where(ish, model["def_h"][t], model["def_a"][t])
        r_opp = np.where(ish, model["att_a"][o], model["att_h"][o]) / (0.5 * (model["def_h"][t] + model["def_a"][t]))
        rows["g_att"], rows["g_def"], rows["g_opp"] = (np.clip(1 + (r_ - 1) * wv, 0.7, 1.4) for r_ in (r_att, r_def, r_opp))

    # bookmaker priors, team level: implied goals for/against replace the structural ones (weight mkt_w)
    mk = mkt or {}
    rows["mk_cs"], rows["mk_ratio"], rows["mk_has"] = np.nan, 1.0, 0.0
    if mk.get("team") and mkt_w > 0:
        pair = [mk["team"].get((f, a), (np.nan, np.nan)) for f, a in zip(fids, t)]
        m_for, m_ag = np.array([x[0] for x in pair], float), np.array([x[1] for x in pair], float)
        has = ~np.isnan(m_for)
        rows["lam_ag"] = np.where(has, mkt_w * m_ag + (1 - mkt_w) * rows["lam_ag"], rows["lam_ag"])
        rows["mk_ratio"] = np.where(has, (mkt_w * m_for + (1 - mkt_w) * lam_for_s) / np.maximum(lam_for_s, 1e-6), 1.0)
        rows["mk_cs"] = np.where(has, np.exp(-m_ag), np.nan)
        rows["mk_has"] = has.astype(float)

    pf = rows.merge(players[["id", "team"]], on="team")
    ids = pf["id"].to_numpy()
    home_row = pf["home"].to_numpy() == 1
    FH, FA = feat_h.reindex(ids), feat_a.reindex(ids)
    F = pd.DataFrame(np.where(home_row[:, None], FH.to_numpy(float), FA.to_numpy(float)), columns=FH.columns)
    pos = F["element_type"].to_numpy(int)
    mf = pf["id"].map(focus).fillna(1.0).to_numpy()
    rot = np.where(pf["n_fix"].to_numpy() > 1, 0.92, 1.0)
    p60_eb, psub_eb = F["p60"].to_numpy(), F["psub"].to_numpy()
    p60_b, psub_b, p60_ml = p60_eb, psub_eb, np.full(len(pf), np.nan)
    if mins_ml is not None and mins_w > 0:      # learned minutes model (sequence of recent games) blended with the smoothed rates
        mq = mins_ml.reindex(ids)
        a60, asub = mq["p60_ml"].to_numpy(float), mq["psub_ml"].to_numpy(float)
        okm = ~np.isnan(a60)
        p60_ml = a60
        p60_b = np.where(okm, mins_w * a60 + (1 - mins_w) * p60_eb, p60_eb)
        psub_b = np.where(okm, mins_w * asub + (1 - mins_w) * psub_eb, psub_eb)
    p60 = np.clip(p60_b * mf * rot, 0, 1)
    psub = np.clip(psub_b * mf, 0, 1 - p60)
    pf["p60_eb"], pf["p60_ml"], pf["p60_used"] = p60_eb, p60_ml, p60
    em = p60 * F["mstart"].to_numpy() + psub * 22.0
    fr = em / 90.0
    am = pf["att_mult"].to_numpy() * pf["mk_ratio"].to_numpy()
    sm, lam = pf["shot_mult"].to_numpy(), pf["lam_ag"].to_numpy()
    mk_has = pf["mk_has"].to_numpy()
    no_mkt = 1.0 - mkt_w * mk_has             # bookmaker prices already contain injuries and stakes, so those priors step back

    # bookmaker priors, player level (anytime scorer / assists)
    pid_fid = list(zip(ids, pf["fid"].to_numpy()))
    live_m = bool(mkt_w > 0)
    mg = np.array([mk.get("goal", {}).get(k, np.nan) for k in pid_fid], float) if (live_m and mk.get("goal")) else np.full(len(pf), np.nan)
    ma = np.array([mk.get("assist", {}).get(k, np.nan) for k in pid_fid], float) if (live_m and mk.get("assist")) else np.full(len(pf), np.nan)
    hasg, hasa = ~np.isnan(mg), ~np.isnan(ma)
    if hasg.any():
        # dropped-player detector: if the market expects far fewer goals than a starter with his rates would produce, he probably isn't starting
        xg_start = F["g90"].to_numpy() * (F["mstart"].to_numpy() / 90.0) * am
        r_ = np.where(hasg, mg / np.maximum(xg_start, 1e-6), 1.0)
        drop = np.where(hasg & (r_ < 1.0), np.clip(r_, 0.35, 1.0) ** 0.5, 1.0)
        drop = 1.0 - mkt_w * (1.0 - drop)
        p60 = np.clip(p60 * drop, 0, 1)
        psub = np.clip(psub * drop, 0, 1 - p60)
        em = p60 * F["mstart"].to_numpy() + psub * 22.0
        fr = em / 90.0

    # squad availability: share of each team's recent xGI that is flagged unavailable. Teammates absorb part of it (redistributed in
    # proportion to their own share); the rest is lost, which also weakens that team as an opponent. Only live forecasts carry flags.
    if share is not None and avail is not None:
        miss = (1 - avail.reindex(idx).fillna(1.0)) * share.reindex(idx).fillna(0.0)
        m_team = miss.groupby(players.set_index("id")["team"].reindex(idx).to_numpy()).sum()
        m_self = np.clip(pf["team"].map(m_team).fillna(0).to_numpy() - pf["id"].map(miss).fillna(0).to_numpy(), 0, 0.8)
        m_opp = np.clip(pf["opp"].map(m_team).fillna(0).to_numpy(), 0, 0.8)
    else:
        m_self, m_opp = np.zeros(len(pf)), np.zeros(len(pf))
    redist = 1 + AVAIL_REDIST * no_mkt * m_self / (1 - m_self)
    lam = lam * (1 - AVAIL_LOSS * no_mkt * m_opp)

    # stability: fitted effect of the auto score + prior effect of your edits (deviation from auto); leaders amplified
    s_self, s_opp = pf["s_self"].to_numpy(), pf["s_opp"].to_numpy()
    a_self, a_opp = pf["a_self"].to_numpy(), pf["a_opp"].to_numpy()
    L = pf["id"].map(leader).fillna(0.0).to_numpy() if leader is not None else np.zeros(len(pf))

    kl = np.minimum(kappa * L, 0.9)   # keeps the leader amplification monotone: more stability can never mean less

    def amp(s_):
        return np.where(s_ > 0, s_ * (1 + kl), s_ * (1 - kl))

    s_eff_auto = amp(a_self)
    dev_self, dev_opp = amp(s_self) - s_eff_auto, s_opp - a_opp

    def factor(i, j):
        return np.clip(1 + coefs[i] * s_eff_auto + coefs[j] * a_opp + coefs_dev[i] * dev_self + coefs_dev[j] * dev_opp, 0.5, 1.6)

    f_goal, f_ast, f_cs = factor(0, 1), factor(2, 3), factor(4, 5)
    s_eff = s_eff_auto  # design terms for fitting use the auto score

    # D_desperation (match leverage): own stakes lift attacking returns; either side's stakes open the game and cut clean-sheet odds.
    # Fitted from past gameweeks through d_dg / d_dcs, not hard-coded.
    d_self, d_opp = pf["des_self"].to_numpy(), pf["des_opp"].to_numpy()
    f_des_att = np.clip(1 + dcoefs[0] * d_self * no_mkt, 0.85, 1.25)
    f_des_cs = np.clip(1 + dcoefs[1] * (d_self + d_opp) * no_mkt, 0.75, 1.15)

    # historical match rating: over/under-performance vs this opponent, its usual system, and same-difficulty teams
    hm_ = hm or {"n_base": pd.Series(dtype=float), "team": None, "form": None, "fdr": None}
    nb = hm_["n_base"].reindex(ids).fillna(0).to_numpy(float)
    rel = nb / (nb + 3.0)
    s_t, n_t = _hm_lookup(hm_["team"], ids, pf["opp"].to_numpy(int))
    s_f, n_f = _hm_lookup(hm_["form"], ids, pf["oform"].to_numpy(object))
    s_d, n_d = _hm_lookup(hm_["fdr"], ids, np.rint(pf["fdr"].to_numpy(float)).astype(int))
    kk = hm_.get("k", HM_SHRINK)
    u_t, u_f, u_d = (rel * s_t / (n_t + kk[0]), rel * s_f / (n_f + kk[1]), rel * s_d / (n_d + kk[2]))
    d_ht, d_hf, d_hd = u_t * p60, u_f * p60, u_d * p60
    hm_adj = hcoefs[0] * d_ht + hcoefs[1] * d_hf + hcoefs[2] * d_hd
    for name, arr in zip(["u_team", "u_form", "u_diff", "n_team", "n_form", "n_diff", "d_ht", "d_hf", "d_hd", "hm_adj"],
                         [u_t, u_f, u_d, n_t, n_f, n_d, d_ht, d_hf, d_hd, hm_adj]):
        pf[name] = arr

    # matchup matrix: how much this opponent leaks to attackers of this position, weighted by whether he is a finisher or a creator
    o_pf = pf["opp"].to_numpy(int)
    role = F["role_f"].to_numpy()

    def mmv(kind, stat):
        return mm[kind][stat][o_pf, pos] if mm is not None else np.ones(len(pf))

    mm_g, mm_a, mm_pts = mmv("idx", "g"), mmv("idx", "a"), mmv("idx", "pts")
    pf["mm_g"], pf["mm_a"], pf["mm_pts"] = mm_g, mm_a, mm_pts
    pf["mm_idx"] = role * mm_g + (1 - role) * mm_a
    pf["mm_dev"] = role * mmv("dev", "g") + (1 - role) * mmv("dev", "a")
    pf["role_f"] = role

    # ---- master xP equation: context-adjusted Poisson rates ----
    #   xP = E[Mins] + Pts_G * lam~_xG + 3 * lam~_xA + Pts_CS * P(60+) * exp(-lam~_conceded) + E[bonus] - E[Cards]
    #   (sum_k k * Poisson(k; lam) = lam, so the expectations are the adjusted rates themselves)
    #   Omega = gamma_venue x M_momentum x M_morale here; D_desperation is applied separately with fitted strength (f_des_att / f_des_cs)
    ones = np.ones(len(pf))
    ctx_att = ctx_ratio = a_fin = b_ast = m_mom_v = m_mor_v = g_ven_v = ones
    ctxm = ctxp is not None
    if ctxm:
        cp = ctxp["p"]
        t_pf, o_pf2, fid_pf = pf["team"].to_numpy(int), pf["opp"].to_numpy(int), pf["fid"].to_numpy()
        sf = ctxp["sfit"]
        if sf is not None and cp["w1"] > 0:
            lg_avg = float(np.nanmean(sf[1:]))
            sfa = np.where(np.isnan(sf), lg_avg, sf)
            sq_t, sq_o = sfa[t_pf] - lg_avg, sfa[o_pf2] - lg_avg
        else:
            sq_t = sq_o = np.zeros(len(pf))
        dpr = ctxp["dprob"]
        dp_t = np.array([dpr.get((f, a), 0.0) for f, a in zip(fid_pf, t_pf)], float)
        dp_o = np.array([dpr.get((f, b), 0.0) for f, b in zip(fid_pf, o_pf2)], float)
        mor_t = np.clip(1 + cp["w1"] * sq_t + cp["w2"] * dev_self + cp["w3"] * dp_t, 0.85, 1.15)   # morale: squad fitness, manager news, odds shift
        mor_o = np.clip(1 + cp["w1"] * sq_o + cp["w2"] * dev_opp + cp["w3"] * dp_o, 0.85, 1.15)
        mom = ctxp["mom"]
        om_att_self = pf["g_att"].to_numpy() * mom[t_pf] * mor_t
        om_att_opp = pf["g_opp"].to_numpy() * mom[o_pf2] * mor_o
        om_def_self = pf["g_def"].to_numpy() * mom[t_pf] * mor_t
        pow_eff = cp["ctx_pow"] * no_mkt     # bookmaker prices already contain this context
        ctx_att = om_att_self ** pow_eff
        ctx_ratio = (om_att_opp / om_def_self) ** pow_eff
        lam = lam * ctx_ratio * (1 + cp["psi"] * (ctxp["err_def"][t_pf] - 1))    # lam~_conceded
        a_fin = ctxp["alpha"].reindex(ids).fillna(1.0).to_numpy()
        b_ast = ctxp["beta"].reindex(ids).fillna(1.0).to_numpy()
        disc = 1 + cp["sigma_disc"] * (ctxp["err_all"][t_pf] - 1)
        m_mom_v, m_mor_v, g_ven_v = mom[t_pf], mor_t, pf["g_att"].to_numpy()
        xg0 = F["gx90"].to_numpy() * fr * am * ctx_att * a_fin * redist        # lam~_xG = lam0_xG * Omega_context,attack * alpha_finish
        xa0 = F["ax90"].to_numpy() * fr * am * ctx_att * b_ast * redist        # lam~_xA = lam0_xA * Omega_context,attack * beta_assist
    else:
        xg0, xa0 = F["g90"].to_numpy() * fr * am * redist, F["a90"].to_numpy() * fr * am * redist
    xg0 = np.where(hasg, mkt_w * mg + (1 - mkt_w) * xg0, xg0)      # market goals per match (already reflects minutes risk)
    xa0 = np.where(hasa, mkt_w * ma + (1 - mkt_w) * xa0, xa0)
    pf["mk_goal"] = np.where(hasg, 1 - np.exp(-mg), np.nan)
    cs0 = np.exp(-lam)
    xg, xa = xg0 * f_goal * f_des_att, xa0 * f_ast * f_des_att
    cs_team = np.clip(cs0 * f_cs * f_des_cs, 0, 0.97)
    lg = np.clip(lam * fr, 1e-6, None)
    pmf = np.exp(-lg[:, None]) * lg[:, None] ** POISSON_K / POISSON_FACT
    gc_pen = np.where(pos <= 2, -(pmf * (POISSON_K // 2)).sum(1), 0.0)
    saves = np.where(pos == 1, exp_floor_div(F["sv90"].to_numpy() * fr * sm, 3), 0.0)
    bonus = F["bonus90"].to_numpy() * fr * np.sqrt(np.where(pos >= 3, am, 1.0 / sm))
    card_mult = np.clip(1 + DES_CARD_BOOST * d_self, 0.9, 1.2)     # high-stakes matches are more fractious
    if ctxm:   # E[Cards] = 1 * P(Yellow) + 3 * P(Red), P = 1 - exp(-lambda_foul * (1 + sigma * mu_error))
        cards = -((1 - np.exp(-F["yc90"].to_numpy() * fr * disc * card_mult)) + 3 * (1 - np.exp(-F["rc90"].to_numpy() * fr * disc * card_mult)))
        extra_cards = -(2 * F["og90"] + 2 * F["pm90"]).to_numpy() * fr
    else:
        cards = -((F["yc90"] + 3 * F["rc90"]).to_numpy() * card_mult + (2 * F["og90"] + 2 * F["pm90"]).to_numpy()) * fr
        extra_cards = 0.0
    mu_dc = F["dc90"].to_numpy() * (F["mstart"].to_numpy() / 90.0) * np.where(pos == 2, sm ** 0.4, 1.0)
    pdc = p60 * nb_tail(mu_dc, DEFCON_LIMIT[pos]) if use_defcon else np.zeros_like(p60)
    ex_ = 1.0 if ((not ctxm) or bool(ctxp["p"].get("extras", True))) else 0.0   # scoring rules the master equation omits

    p_app = 2 * p60 + psub
    p_goal0, p_ast0, p_cs0 = xg0 * GOAL_PTS[pos], xa0 * 3, cs0 * p60 * CS_PTS[pos]
    p_goal, p_ast, p_cs, p_dc = xg * GOAL_PTS[pos], xa * 3, cs_team * p60 * CS_PTS[pos], 2 * pdc * ex_
    p_oth = bonus + cards + ex_ * (gc_pen + saves + extra_cards)
    pf["om_att"], pf["om_ratio"], pf["a_fin"], pf["b_ast"] = ctx_att, ctx_ratio, a_fin, b_ast
    pf["m_mom"], pf["m_mor"], pf["g_ven"] = m_mom_v, m_mor_v, g_ven_v
    pf["miss_self"], pf["miss_opp"] = m_self, m_opp
    pf["x0"] = p_app + p_goal0 + p_ast0 + p_cs0 + p_dc + p_oth
    pf["x"] = p_app + p_goal + p_ast + p_cs + p_dc + p_oth + hm_adj
    pf["d_gs"], pf["d_go"] = p_goal0 * s_eff, p_goal0 * s_opp
    pf["d_as"], pf["d_ao"] = p_ast0 * s_eff, p_ast0 * s_opp
    pf["d_cs"], pf["d_co"] = p_cs0 * s_eff, p_cs0 * s_opp
    pf["d_dg"], pf["d_dcs"] = (p_goal0 + p_ast0) * d_self, p_cs0 * (d_self + d_opp)
    pf["lam_ag"] = lam / (f_cs * f_des_cs)
    pf["em"], pf["cs_team"], pf["xg"], pf["xa"], pf["pdc"] = em, cs_team, xg, xa, pdc
    pf["g90"], pf["a90"], pf["dc90"], pf["n_w"] = F["g90"].to_numpy(), F["a90"].to_numpy(), F["dc90"].to_numpy(), F["n_w"].to_numpy()
    for name, arr in zip(["p_app", "p_goal", "p_ast", "p_cs", "p_dc", "p_oth"], [p_app, p_goal, p_ast, p_cs, p_dc, p_oth]):
        pf[name] = arr
    agg = pf.groupby("id").agg(
        xPts=("x", "sum"), xPts0=("x0", "sum"), xMins=("em", "sum"), cs_team=("cs_team", "mean"), xGC=("lam_ag", "sum"),
        Fix=("att_mult", "mean"), xG=("xg", "sum"), xA=("xa", "sum"), pdc=("pdc", "mean"),
        g90=("g90", "mean"), a90=("a90", "mean"), dc90=("dc90", "mean"), n_w=("n_w", "mean"),
        p_app=("p_app", "sum"), p_goal=("p_goal", "sum"), p_ast=("p_ast", "sum"), p_cs=("p_cs", "sum"),
        p_dc=("p_dc", "sum"), p_oth=("p_oth", "sum"), n_fix=("x", "size"), home=("home", "mean"),
        stab_self=("s_self", "mean"), stab_opp=("s_opp", "mean"),
        des_self=("des_self", "mean"), des_opp=("des_opp", "mean"), miss_self=("miss_self", "mean"), miss_opp=("miss_opp", "mean"),
        d_gs=("d_gs", "sum"), d_go=("d_go", "sum"), d_as=("d_as", "sum"), d_ao=("d_ao", "sum"),
        d_cs=("d_cs", "sum"), d_co=("d_co", "sum"), d_ht=("d_ht", "sum"), d_hf=("d_hf", "sum"), d_hd=("d_hd", "sum"),
        d_dg=("d_dg", "sum"), d_dcs=("d_dcs", "sum"),
        hm_adj=("hm_adj", "sum"), u_team=("u_team", "mean"), u_form=("u_form", "mean"), u_diff=("u_diff", "mean"),
        n_team=("n_team", "mean"), n_form=("n_form", "mean"), n_diff=("n_diff", "mean"),
        opp_att=("opp_att", "mean"), opp_def=("opp_def", "mean"), team_def=("team_def", "mean"), lam_for=("lam_for", "mean"),
        mm_g=("mm_g", "mean"), mm_a=("mm_a", "mean"), mm_pts=("mm_pts", "mean"), mm_idx=("mm_idx", "mean"),
        mm_dev=("mm_dev", "mean"), role_f=("role_f", "mean"), mk_cs=("mk_cs", "mean"), mk_goal=("mk_goal", "mean"),
        p60_eb=("p60_eb", "mean"), p60_ml=("p60_ml", "mean"), p60_used=("p60_used", "mean"),
        om_att=("om_att", "mean"), om_ratio=("om_ratio", "mean"), a_fin=("a_fin", "mean"), b_ast=("b_ast", "mean"),
        m_mom=("m_mom", "mean"), m_mor=("m_mor", "mean"), g_ven=("g_ven", "mean"))
    res = agg.reindex(idx).fillna(0)
    res["mk_cs"], res["mk_goal"] = agg["mk_cs"].reindex(idx), agg["mk_goal"].reindex(idx)   # keep "no market" as NaN
    res["p60_ml"] = agg["p60_ml"].reindex(idx)                                                # keep "no learned minutes" as NaN
    av = availability(players, fixture_gw, next_gw).reindex(idx).to_numpy()
    for c in ["xPts", "xPts0", "hm_adj", "xMins", "xG", "xA", "p_app", "p_goal", "p_ast", "p_cs", "p_dc", "p_oth"]:
        res[c] *= av
    rows["txt"] = rows["opp"].map(code).fillna("UNK") + np.where(ish, " (H)", " (A)")
    tm = players.set_index("id")["team"].reindex(idx)
    res["Opponent"] = tm.map(rows.groupby("team")["txt"].agg(", ".join)).fillna("Blank GW")
    res["FDR"] = tm.map(rows.groupby("team")["fdr"].mean()).fillna(3.0)
    res["OppSystem"] = tm.map(rows.groupby("team")["oform"].agg(lambda s_: ", ".join(str(x) if pd.notna(x) else "?" for x in s_))).fillna("–")
    return res


def condition_features(T, stab, team, share):
    """Situational signals, z-scored within position (players with minutes only): home, team form, roster in form, morale,
    skill, creativity, player form, matchup, carrying the team, and hunger to carry it when the team is in trouble."""
    ok = (T["n_fix"] > 0).astype(float)

    def zpos(s):
        s = s.where(T["E90"] > 0)
        g = s.groupby(T["element_type"])
        return ((s - g.transform("mean")) / (g.transform("std", ddof=0) + 1e-9)).clip(-2.5, 2.5).fillna(0.0)

    C = pd.DataFrame(index=T.index)
    C["c_home"] = (T["home"] - 0.5) * 2
    C["c_teamform"] = team.map(stab["momentum"]).astype(float).fillna(0).clip(-2.5, 2.5)
    C["c_hot"] = team.map(stab["hot_share"]).astype(float).fillna(0).clip(-2.5, 2.5)
    C["c_morale"] = T["stab_self"] * 2
    C["c_skill"] = zpos(T["pts90"])
    C["c_create"] = zpos(T["creativity_6"])
    C["c_form"] = zpos(T["form"])
    C["c_matchup"] = (np.log(T["Fix"].clip(0.3, 3.0)) / 0.25).clip(-2.5, 2.5) - T["stab_opp"]
    C["c_matrix"] = (np.log(T["mm_idx"].clip(0.3, 3.0)) / 0.25).clip(-2.5, 2.5)
    C["c_carry"] = zpos(share)
    C["c_hunger"] = C["c_carry"].clip(lower=0) * (-T["stab_self"]).clip(lower=0) * 2
    return C.mul(ok, axis=0)[COND_COLS]


def _feature_table(ctx, players, cutoff, fixture_gw, fx_g, p, focus, code, stab_pairs=None, coefs=None, kappa=0.0, coefs_dev=None, hcoefs=None,
                  light=False, mkt=None, mkt_w=0.0, mins_ml=None, mins_w=0.0, sfit=None, dprob=None, dcoefs=None, avail=None):
    """One row per player: structural forecast + every learning feature, using only rounds < cutoff."""
    h = ctx["h"]
    team = fit_team_model(ctx["long"], cutoff, p)
    base = season_block(h, players, cutoff, p)
    fh, fa = venue_features(h, base, cutoff, p, 1), venue_features(h, base, cutoff, p, 0)
    ctxp = build_ctxp(ctx, players, cutoff, fixture_gw, p, sfit, dprob)   # master-equation context (None when switched off)
    des = team_desperation(ctx["long"], cutoff)
    share_av = attack_share(h, players, cutoff) if avail is not None else None
    if light:  # structural forecast only (used for fast hyper-parameter tuning)
        res = predict_gw(fh, fa, players, fx_g, team, fixture_gw, None, {}, code, p["use_defcon"], mins_ml=mins_ml, mins_w=mins_w, ctxp=ctxp)
        return res.join(base[["element_type", "form", "ppg", "E90", "conf"]])
    stab = team_stability(ctx, players, cutoff, p)
    auto = np.zeros(21)
    auto[1:21] = stab["auto"].to_numpy()
    leader = leader_scores(h, players, base, cutoff)
    res = predict_gw(fh, fa, players, fx_g, team, fixture_gw, None, focus, code, p["use_defcon"],
                     auto, stab_pairs, coefs, leader, kappa, coefs_dev, hmpr_tables(ctx, cutoff, p), hcoefs,
                     matchup_tables(ctx, cutoff, p), mkt, mkt_w, mins_ml, mins_w, ctxp,
                     des=des, dcoefs=dcoefs, share=share_av, avail=avail)
    ex = extra_features(h, base.index, cutoff, p["form_hl"])
    T = res.join(ex).join(base[["element_type", "form", "ppg", "E90", "conf", "pts90"]])
    T["leader"] = leader
    T["stab_diff"] = T["stab_self"] - T["stab_opp"]
    # share of his team's recent goal involvement (dependence on him)
    r6 = h[(h["round"] < cutoff) & (h["round"] >= cutoff - 6)]
    inv = (r6["expected_goal_involvements"] if h["expected_goal_involvements"].sum() > 0 else r6["goals_scored"] + r6["assists"]).groupby(r6["id"]).sum()
    tm_map = players.set_index("id")["team"]
    inv_team = inv.groupby(tm_map.reindex(inv.index)).transform("sum")
    share = (inv / inv_team.replace(0, np.nan)).reindex(T.index).fillna(0)
    r70 = r6[r6["min_pm"] >= 70]   # games (last 6) in which he played 70+ minutes
    T["perf70"] = (r70["total_points"] / r70["n_played"]).groupby(r70["id"]).mean().reindex(T.index).fillna(0)
    T["n70"] = r70.groupby("id").size().reindex(T.index).fillna(0)
    return T.join(condition_features(T, stab, tm_map.reindex(T.index), share))


feature_table = st.cache_data(show_spinner=False, max_entries=12)(_feature_table)



# =========================================================
# LEARNING LAYER — walk-forward residual model
# =========================================================
class RidgeFallback:
    def __init__(self, lam=200.0):
        self.lam = lam

    def fit(self, X, y, sample_weight=None):
        X = np.nan_to_num(np.asarray(X, float))
        y = np.asarray(y, float)
        w = np.ones(len(y)) if sample_weight is None else np.asarray(sample_weight, float)
        self.mu = np.average(X, axis=0, weights=w)
        self.sd = np.sqrt(np.average((X - self.mu) ** 2, axis=0, weights=w)) + 1e-9
        self.b = float(np.average(y, weights=w))
        Z = (X - self.mu) / self.sd
        self.w = np.linalg.solve((Z * w[:, None]).T @ Z + self.lam * np.eye(Z.shape[1]), (Z * w[:, None]).T @ (y - self.b))
        return self

    def predict(self, X):
        return ((np.nan_to_num(np.asarray(X, float)) - self.mu) / self.sd) @ self.w + self.b


def make_hgb():
    from sklearn.ensemble import HistGradientBoostingRegressor
    return SafeHGB(HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=60, l2_regularization=5.0,
                                                 random_state=0))


class BlendModel:
    """Average of a gradient-boosted model and an extremely-randomised-trees model. The two make different mistakes, so their
    average has lower variance than either alone (bagging/ensembling; Breiman 1996, Geurts et al. 2006)."""

    def fit(self, X, y, sample_weight=None):
        from sklearn.ensemble import ExtraTreesRegressor
        X = np.nan_to_num(np.asarray(X, float))
        y = np.asarray(y, float)
        kw = {} if sample_weight is None else {"sample_weight": np.asarray(sample_weight, float)}
        g = make_hgb().fit(X, y, **kw)
        e = ExtraTreesRegressor(n_estimators=60, min_samples_leaf=40, max_features=0.5, random_state=0, n_jobs=1).fit(X, y, **kw)
        self.parts = [(0.6, g), (0.4, e)]
        return self

    def predict(self, X):
        X = np.nan_to_num(np.asarray(X, float))
        return sum(w * m.predict(X) for w, m in self.parts)


RESID_REFIT = 3   # residual models are refit every 3 gameweeks in the walk-forward (cost control)


def winsor(y, lo=0.01, hi=0.99):
    """Caps extreme residuals (20-point hauls) so a few outliers don't dominate a squared-error fit."""
    y = np.asarray(y, float)
    a, b = np.quantile(y, [lo, hi])
    return np.clip(y, a, b)


def train_weights(gw, g, hl=14.0):
    """Recent gameweeks count more when fitting (half-life 14 gameweeks, floor 0.25)."""
    return np.maximum(0.5 ** ((g - 1 - np.asarray(gw, float)) / hl), 0.25)


def make_qmodel(q, mono=None):
    """Gradient-boosted quantile regression (pinball loss; Koenker & Bassett 1978, Friedman 2001), optionally monotone in chosen signals."""
    try:
        from sklearn.ensemble import HistGradientBoostingRegressor
        kw = {"monotonic_cst": list(mono)} if (mono is not None and any(mono)) else {}
        return SafeHGB(HistGradientBoostingRegressor(loss="quantile", quantile=q, max_depth=3, learning_rate=0.05, max_iter=120,
                                             min_samples_leaf=80, l2_regularization=5.0, random_state=0, **kw))
    except Exception:
        return None


def make_model():
    try:
        return BlendModel() if make_hgb() is not None else RidgeFallback()
    except Exception:
        return RidgeFallback()


POS_GROUP = {1: "DEF/GK", 2: "DEF/GK", 3: "MID", 4: "FWD"}
GROUPS = ["DEF/GK", "MID", "FWD"]
MIN_ROWS_GROUP = 400
_COMMON = ({"form", "ppg", "E90", "conf", "pts90", "pts", "perf70", "n70", "mins", "start", "p60", "psub", "mstart", "xMins", "n_w", "leader",
            "xPts", "xPts0", "stab_self", "stab_opp", "stab_diff", "home", "n_fix", "FDR", "Fix", "hm_adj", "u_team", "u_form", "u_diff",
            "n_team", "n_form", "n_diff", "d_gs", "d_go", "d_as", "d_ao", "d_cs", "d_co", "d_ht", "d_hf", "d_hd", "d_dg", "d_dcs",
            "des_self", "des_opp", "thr90", "cre90"} | set(COND_COLS))
# defenders / keepers: team-level goals against, opponent attack strength, clean-sheet odds, defensive actions, saves
_DEF = _COMMON | {"xGC", "cs_team", "opp_att", "team_def", "p_cs", "p_dc", "p_oth", "p_app", "pdc", "dc90", "sv90", "bonus90", "yc90", "rc90",
                  "expected_goals_conceded", "defensive_contribution", "clearances_blocks_interceptions", "recoveries", "tackles", "saves",
                  "bps", "influence", "bonus", "xG", "xA", "g90", "a90", "gx90", "ax90", "p_goal", "p_ast", "mm_pts"}
# forwards: individual xG, xA, last-4-gameweek shot-volume proxies (xG, xA, threat), opponent defensive vulnerability, matchup matrix
_FWD = _COMMON | {"xG", "xA", "g90", "a90", "gx90", "ax90", "p_goal", "p_ast", "p_app", "threat", "creativity", "expected_goals", "expected_assists",
                  "expected_goal_involvements", "ict_index", "influence", "bps", "bonus", "bonus90", "opp_def", "lam_for", "mm_g", "mm_a",
                  "mm_idx", "mm_dev", "mm_pts", "role_f"}
_UP = {"FWD": {"xG", "xA", "g90", "a90", "expected_goals", "expected_assists", "threat", "expected_goal_involvements", "p_goal", "p_ast",
               "xPts", "xPts0", "opp_def", "lam_for"},
       "DEF/GK": {"cs_team", "p_cs", "xPts", "xPts0"}, "MID": {"xPts", "xPts0"}, "ALL": {"xPts", "xPts0"}}
_DOWN = {"DEF/GK": {"xGC", "opp_att", "team_def"}}


def _stem(name):
    head, _, tail = name.rpartition("_")
    return head if (tail.isdigit() and head) else name


def group_cols(group, feat_cols):
    """Signals a position group's models are allowed to use (midfielders use everything)."""
    if group == "MID":
        return list(feat_cols)
    stems = _DEF if group == "DEF/GK" else _FWD
    cols = [c for c in feat_cols if _stem(c) in stems]
    return cols if len(cols) >= 8 else list(feat_cols)


def mono_vector(group, cols):
    """Forces quantile models to respect obvious directions: more xG/xA/threat/opponent leakiness never lowers a forward's forecast,
    more team goals-against / opponent attack never raises a defender's."""
    up, down = _UP.get(group, set()), _DOWN.get(group, set())
    return [1 if _stem(c) in up else (-1 if _stem(c) in down else 0) for c in cols]


class PosModels:
    """One model per position group (DEF/GK, MID, FWD), each on its own signal set, with a pooled fallback for thin groups."""

    def __init__(self, factory, feat_cols, stratify=True):
        self.factory, self.feat_cols, self.stratify = factory, list(feat_cols), stratify
        self.models, self.pool = {}, None

    def fit(self, X, y, et, sample_weight=None):
        y = np.asarray(y, float)
        sw = None if sample_weight is None else np.asarray(sample_weight, float)
        grp = np.array([POS_GROUP.get(int(e), "MID") for e in np.asarray(et)])
        if self.stratify:
            for g in GROUPS:
                sel = grp == g
                if sel.sum() >= MIN_ROWS_GROUP:
                    cols = group_cols(g, self.feat_cols)
                    kw = {} if sw is None else {"sample_weight": sw[sel]}
                    self.models[g] = (self.factory(g, cols).fit(X.loc[sel, cols], y[sel], **kw), cols)
        if len(self.models) < len(GROUPS):
            kw = {} if sw is None else {"sample_weight": sw}
            self.pool = (self.factory("ALL", self.feat_cols).fit(X[self.feat_cols], y, **kw), self.feat_cols)
        return self

    def predict(self, X, et):
        grp = np.array([POS_GROUP.get(int(e), "MID") for e in np.asarray(et)])
        out = np.zeros(len(X))
        for g in GROUPS:
            sel = grp == g
            if sel.any():
                mdl, cols = self.models.get(g) or self.pool
                out[sel] = mdl.predict(X.loc[sel, cols])
        return out


def fit_quantile_pair(X, y, et, feat_cols, stratify):
    if make_qmodel(0.5) is None:
        return False
    lo = PosModels(lambda g, cols: make_qmodel(0.1, mono_vector(g, cols)), feat_cols, stratify).fit(X, y, et)
    hi = PosModels(lambda g, cols: make_qmodel(0.9, mono_vector(g, cols)), feat_cols, stratify).fit(X, y, et)
    return lo, hi


def build_training(ctx, players, code, p, start_gw=3, only_gws=None, light=False):
    fin, out = ctx["fin"], []
    seq = ctx["seq"]
    mkey = (len(seq), int(seq["round"].max()) if len(seq) else 0)
    for g in sorted(fin["event"].unique()):
        if g < start_gw or (only_gws is not None and g not in only_gws):
            continue
        fin_g = fin[fin["event"] == g]
        anchor = g - ((g - start_gw) % 3)                      # minutes model refit every 3 gameweeks, trained on games before the anchor
        mins_ml = minutes_predict(minutes_model_at(seq, anchor, mkey), ctx["h"], players, fin_g, g) if len(seq) else None
        T = _feature_table(ctx, players, g, g, fin_g, p, {}, code, light=light, mins_ml=mins_ml, mins_w=p.get("mins_ml_w", 0.0))
        act = ctx["h"][ctx["h"]["round"] == g][["id", "total_points", "min_pm"]].rename(columns={"total_points": "actual"})
        act["act60"] = (act.pop("min_pm") >= 60).astype(float)
        T = T.reset_index().merge(act, on="id")
        T = T[(T["n_fix"] > 0) & (T["E90"] > 0)].copy()
        T["GW"] = g
        out.append(T)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def fit_stab_coefs(tr):
    """How much stability (0-5), historical match rating (6-8) and desperation (9-10) move points — fitted on past gameweeks,
    ridge-shrunk toward a prior."""
    if len(tr) < 200:
        return COEF_PRIOR.copy()
    X = tr[DESIGN_COLS].to_numpy(float)
    y = (tr["actual"] - tr["xPts0"]).to_numpy(float)
    X1 = np.column_stack([X, np.ones(len(X))])
    k = X.shape[1]
    lam = 0.5 * np.diag(X.T @ X) + 1e-6          # each coefficient is shrunk in proportion to how much data informs it
    A = X1.T @ X1 + np.diag(np.r_[lam, 0.0]) + 1e-6 * np.eye(k + 1)
    b = X1.T @ y + np.r_[lam * COEF_PRIOR, 0.0]
    # stability can't help the other side; history can't count negatively; stakes can't cut attack or raise clean sheets
    return np.clip(np.linalg.solve(A, b)[:k], COEF_LO, COEF_HI)


def base_from(T, c):
    return T["xPts0"] + T[DESIGN_COLS].to_numpy(float) @ np.asarray(c, float)


def walk_forward(train, feat_cols, stratify=True):
    out = []
    m = c = qm = None
    m_last = q_last = 0
    for g in sorted(train["GW"].unique()):
        tr, te = train[train["GW"] < g], train[train["GW"] == g]
        if tr["GW"].nunique() < 2 or len(tr) < GW_TABLE_MIN_ROWS:
            continue
        Xtr, Xte = tr[feat_cols].fillna(0), te[feat_cols].fillna(0)
        if m is None or g - m_last >= RESID_REFIT:
            c = fit_stab_coefs(tr)
            m = PosModels(lambda grp, cols: make_model(), feat_cols, stratify).fit(
                Xtr, winsor(tr["actual"] - base_from(tr, c)), tr["element_type"], train_weights(tr["GW"], g))
            m_last = g
        if qm is None or g - q_last >= 3:                      # ceiling/floor models are refit every 3 gameweeks (cost control)
            qm = fit_quantile_pair(Xtr, tr["actual"], tr["element_type"], feat_cols, stratify)
            q_last = g
        te = te.assign(xPts=base_from(te, c), adj=m.predict(Xte, te["element_type"]),
                       q10=qm[0].predict(Xte, te["element_type"]) if qm else np.nan,
                       q90=qm[1].predict(Xte, te["element_type"]) if qm else np.nan)
        out.append(te[["GW", "id", "element_type", "xPts", "adj", "actual", "form", "ppg", "home", "FDR", "stab_self", "perf70", "n70", "mins_6",
                       "creativity_6", "threat_6", "influence_6", "xG", "xA", "q10", "q90"] + COND_COLS])
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


@st.cache_data(persist="disk", show_spinner="Learning from past gameweeks (walk-forward)…")
def learn_from_history(_ctx, _players, _code, p_key, key, seasons=()):
    """Training rows from this season plus any past seasons. Past-season gameweeks are shifted back by 38 per season
    (last season's GW38 becomes GW0), so the walk-forward treats them as earlier weeks and train_weights counts them less."""
    parts, failed = [], []
    cur = build_training(_ctx, _players, _code, dict(p_key))
    if len(cur):
        parts.append(cur.assign(season="this season"))
    for k_, s_ in enumerate(seasons):
        try:
            tp_ = past_training(s_, p_key)
        except Exception as e_past:
            failed.append((s_, f"{type(e_past).__name__}: {e_past}"[:400]))
            continue
        if len(tp_):
            parts.append(tp_.assign(GW=tp_["GW"] - 38 * (k_ + 1), season=s_))
    if not parts:
        return pd.DataFrame(), pd.DataFrame(), [], failed
    train = pd.concat(parts, ignore_index=True)
    feat_cols = prune_features(train, [c for c in train.select_dtypes("number").columns
                                       if c not in ("actual", "GW", "id", "act60", "miss_self", "miss_opp")])  # availability is live-only
    return train, walk_forward(train, feat_cols, dict(p_key).get("stratify", True)), feat_cols, failed


@st.cache_resource(show_spinner="Fitting final learned model…")
def fit_final_model(_train_use, feat_cols, target_gw, p_key, key):
    c = fit_stab_coefs(_train_use)
    X = _train_use[list(feat_cols)].fillna(0)
    m = PosModels(lambda g, cols: make_model(), feat_cols, dict(p_key).get("stratify", True)).fit(
        X, winsor(_train_use["actual"] - base_from(_train_use, c)), _train_use["element_type"], train_weights(_train_use["GW"], target_gw))
    return m, c


@st.cache_resource(show_spinner="Fitting ceiling/floor models…")
def fit_final_quantiles(_train_use, feat_cols, target_gw, p_key, key):
    if len(_train_use) < GW_TABLE_MIN_ROWS:
        return None
    q = fit_quantile_pair(_train_use[list(feat_cols)].fillna(0), _train_use["actual"], _train_use["element_type"],
                          feat_cols, dict(p_key).get("stratify", True))
    return q if q else None


def calibrated_interval(wf, aer):
    """Out-of-sample floor/ceiling for every walk-forward row: quantile-model outputs shifted by offsets learned only from earlier
    gameweeks (conformalised quantile regression; Romano, Patterson & Candes 2019). Ceiling >= AER >= Floor >= 0."""
    lo, hi = pd.Series(np.nan, index=wf.index), pd.Series(np.nan, index=wf.index)
    if "q90" not in wf.columns or wf["q90"].isna().all():
        return lo, hi
    for g in sorted(wf["GW"].unique()):
        prev = wf[(wf["GW"] < g) & wf["q90"].notna()]
        sel = (wf["GW"] == g).to_numpy()
        d90 = float(np.quantile(prev["actual"] - prev["q90"], 0.9)) if len(prev) >= 300 else 0.0
        d10 = float(np.quantile(prev["actual"] - prev["q10"], 0.1)) if len(prev) >= 300 else 0.0
        hi[sel], lo[sel] = wf.loc[sel, "q90"] + d90, wf.loc[sel, "q10"] + d10
    aer = pd.Series(np.asarray(aer, float), index=wf.index)
    return np.clip(np.minimum(lo, aer), 0, None), np.maximum(hi, aer)


@st.cache_data(show_spinner=False, max_entries=8)
def interval_report(wf, aer):
    lo, hi = calibrated_interval(wf, aer)
    ok = hi.notna()
    if not ok.any():
        return None
    y = wf.loc[ok, "actual"]
    return {"ceiling_hit": float((y <= hi[ok]).mean()), "floor_hit": float((y >= lo[ok]).mean()),
            "raw_ceiling_hit": float((y <= wf.loc[ok, "q90"]).mean()), "raw_floor_hit": float((y >= wf.loc[ok, "q10"]).mean()),
            "width": float((hi - lo)[ok].mean()), "rows": int(ok.sum())}


def ict_pct(values, groups):
    """0-1 percentile of creativity / threat / influence within position (among the players passed in)."""
    return pd.Series(values).groupby(groups).rank(pct=True)


def dps_formula(aer, form, ixg, ixa, cre, thr, inf, ceiling, mode="sum"):
    """DPS (Deterministic Points Scored) = ((AER + Form) x (xG + xA + creativity + threat + influence)) / sqrt(Ceiling).
    The five terms are 0-1 percentile indices within position; mode='avg' divides their sum by 5."""
    s = ixg + ixa + cre + thr + inf
    if mode != "sum":
        s = s / 5.0
    return ((aer + form) * s) / np.sqrt(np.maximum(ceiling, 1.0))


THETA_SLOTS = {1: 1, 2: 4, 3: 4, 4: 2}   # typical starters per team by position (GK, DEF, MID, FWD): sets the replacement level


def dps_resid(dd, aer, mask=None):
    """The part of DPS that AER doesn't already contain: the residual of a least-squares line of DPS on AER across players."""
    dd, aer = np.asarray(dd, float), np.asarray(aer, float)
    m = np.isfinite(dd) & np.isfinite(aer) & (np.ones(len(dd), bool) if mask is None else np.asarray(mask, bool))
    if m.sum() < 10 or np.ptp(aer[m]) <= 0:
        return np.zeros(len(dd))
    b1, b0 = np.polyfit(aer[m], dd[m], 1)
    r = dd - (b0 + b1 * aer)
    return np.where(np.isfinite(r), r, 0.0)


@st.cache_data(show_spinner=False, max_entries=8)
def fit_theta_beta(rows):
    """Points per unit of 'DPS not explained by AER', fitted on past gameweeks: slope of (actual - AER) on that residual,
    ridge-shrunk toward 0 and never negative. 0 means DPS adds nothing AER doesn't already know."""
    if rows is None or len(rows) < 300:
        return 0.0
    x = rows["resid"].to_numpy(float) - rows["resid"].mean()
    y = (rows["actual"] - rows["aer"]).to_numpy(float)
    y = y - y.mean()
    sxx = float((x ** 2).sum())
    return float(np.clip((x * y).sum() / (1.5 * sxx), 0.0, 2.0)) if sxx > 0 else 0.0


def replacement_levels(frame, col, n_teams):
    """Replacement level per position: the `col` value of the first player below the league's starting-calibre pool
    (n_teams x typical starters at that position). Scarce positions get a lower bar."""
    out = {}
    for et, slots in THETA_SLOTS.items():
        v = frame.loc[frame["element_type"] == et, col].dropna().sort_values(ascending=False)
        out[et] = float(v.iloc[min(n_teams * slots, len(v) - 1)]) if len(v) else 0.0
    return out


@st.cache_data(show_spinner=False, max_entries=8)
def dps_history(wf, alpha, conviction, mode):
    """Per past gameweek (70+ minute players): AER as forecast then, the DPS formula with out-of-sample ceilings, and its residual on AER."""
    w = wf.assign(aer=god_expanding(wf, alpha, conviction))
    lo_s, hi_s = calibrated_interval(w, w["aer"])
    use_q = bool(hi_s.notna().any())
    out = []
    for g in sorted(w["GW"].unique()):
        prev, cur = w[w["GW"] < g], w[(w["GW"] == g) & (w["mins_6"] >= 70)]
        if use_q:
            cur = cur[hi_s[cur.index].notna()]
        if len(prev) < 300 or len(cur) < 22:
            continue
        ceil = hi_s[cur.index].to_numpy(float) if use_q else lookup_bands(make_bands(prev["aer"], prev["actual"]), cur["aer"].to_numpy())[1]
        idx5 = [ict_pct(cur[c], cur["element_type"]).to_numpy(float) for c in ("xG", "xA", "creativity_6", "threat_6", "influence_6")]
        dd = dps_formula(cur["aer"].to_numpy(float), cur["form"].to_numpy(float), *idx5, ceil, mode)
        out.append(cur.assign(dd=dd, resid=dps_resid(dd, cur["aer"].to_numpy(float))))
    return out


@st.cache_data(show_spinner=False, max_entries=8)
def dps_backtest(wf, alpha, conviction, topn=11, mode="sum"):
    """Out of sample, gameweek by gameweek: actual points of the best legal XI and of the top N chosen by DPS, by AER and by Theta Swole's
    points estimate (AER + fitted DPS signal, with the fit using only earlier gameweeks), plus each estimate's squared error."""
    rows, past = [], []
    for cur in dps_history(wf, alpha, conviction, mode):
        g = int(cur["GW"].iloc[0])
        beta = fit_theta_beta(pd.concat(past, ignore_index=True) if past else None)
        cur = cur.assign(eadj=cur["aer"] + beta * cur["resid"])

        def xi_pts(col):
            xi_, _, form_ = best_lineup(cur.dropna(subset=[col]), col)
            return float(xi_["actual"].sum()) if form_ != "incomplete squad" else np.nan

        rows.append({"GW": g,
                     "XI chosen by DPS (actual pts)": xi_pts("dd"),
                     "XI chosen by AER (actual pts)": xi_pts("aer"),
                     "XI chosen by Theta Swole points (actual pts)": xi_pts("eadj"),
                     f"Top {topn} by DPS (avg pts)": cur.nlargest(topn, "dd")["actual"].mean(),
                     f"Top {topn} by AER (avg pts)": cur.nlargest(topn, "aer")["actual"].mean(),
                     f"Top {topn} by Theta Swole points (avg pts)": cur.nlargest(topn, "eadj")["actual"].mean(),
                     "Squared error: AER": float(((cur["actual"] - cur["aer"]) ** 2).mean()),
                     "Squared error: Theta Swole points": float(((cur["actual"] - cur["eadj"]) ** 2).mean()),
                     "DPS signal weight used (β)": beta})
        past.append(cur)
    return pd.DataFrame(rows)


PROTECTED = {"xGC", "cs_team", "opp_att", "team_def", "opp_def", "lam_for", "xG", "xA", "expected_goals_4", "expected_assists_4", "threat_4"}


def prune_features(train, feat_cols, thresh=0.97):
    """Unsupervised pruning (no use of the target): drop constant and near-duplicate signals so only distinct information remains.
    Signals the position models are built around (team goals against, opponent attack/defence, xG, xA, last-4 shot-volume proxies) are never dropped."""
    X = train[feat_cols].fillna(0)
    keep = [c for c in feat_cols if X[c].std() > 1e-9]
    corr = X[keep].corr().abs()
    drop = set()
    for i, c in enumerate(keep):
        if c in drop:
            continue
        for c2 in keep[i + 1:]:
            if c2 in drop or corr.loc[c, c2] <= thresh:
                continue
            if c2 in PROTECTED and c not in PROTECTED:
                drop.add(c)
                break
            if c2 not in PROTECTED:
                drop.add(c2)
    return [c for c in keep if c not in drop]


def fit_god(rows):
    """Solve FPL: (1) a conditions multiplier M = 1 + sum(beta_k * z_k), betas >= 0, ridge-shrunk toward a small positive prior and fitted on
    out-of-sample forecasts; (2) a spread calibration slope/intercept so forecast averages are stretched (or shrunk) to match reality."""
    k = len(COND_COLS)
    if len(rows) < 800 or rows["GW"].nunique() < 2:
        return np.zeros(k), 1.0, 0.0
    C = rows[COND_COLS].to_numpy(float)
    a, y = rows["aer"].to_numpy(float), rows["actual"].to_numpy(float)
    X = C * a[:, None]
    X1 = np.column_stack([X, np.ones(len(X))])
    lam = 0.5 * np.diag(X.T @ X) + 1e-6
    A = X1.T @ X1 + np.diag(np.r_[lam, 0.0]) + 1e-6 * np.eye(k + 1)
    b = X1.T @ (y - a) + np.r_[lam * GOD_PRIOR, 0.0]
    beta = np.clip(np.linalg.solve(A, b)[:k], 0.0, 0.20)
    pred = a * np.clip(1 + C @ beta, 0.6, 1.6)
    vp = float(pred.var())
    slope = float(np.clip(np.cov(pred, y)[0, 1] / vp, 0.7, 1.6)) if vp > 1e-9 else 1.0
    return beta, slope, float(y.mean() - slope * pred.mean())


def apply_god(aer, C, model, conviction=1.0):
    beta, slope, icpt = model
    pred = aer * np.clip(1 + C @ (np.asarray(beta) * conviction), 0.6, 1.6)
    return np.where(aer > 0, np.clip(slope * pred + icpt, 0, None), aer)


@st.cache_data(show_spinner=False, max_entries=8)
def god_expanding(wf, alpha, conviction=1.0):
    """Out-of-sample Solve FPL forecasts: each gameweek uses a fit made only on earlier gameweeks."""
    aer = np.clip(wf["xPts"] + alpha * wf["adj"], 0, None)
    out = aer.copy()
    for g in sorted(wf["GW"].unique()):
        prev = wf[wf["GW"] < g]
        if prev["GW"].nunique() < 2 or len(prev) < 800:
            continue
        m = fit_god(prev.assign(aer=aer[prev.index]))
        sel = (wf["GW"] == g).to_numpy()
        out[sel] = apply_god(aer[sel].to_numpy(float), wf.loc[sel, COND_COLS].to_numpy(float), m, conviction)
    return out


@st.cache_data(show_spinner=False, max_entries=8)
def residual_patterns(w):
    """Where forecasts were systematically too low/high in past gameweeks (needs columns pred, actual, home, FDR, stab_self, form, c_hot)."""
    w = w.copy()
    w["resid"] = w["actual"] - w["pred"]
    parts = {
        "Venue": pd.Series(np.where(w["home"] == 1, "Home", np.where(w["home"] == 0, "Away", "Mixed")), index=w.index),
        "Position": w["element_type"].map({1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}),
        "Fixture difficulty": "FDR " + w["FDR"].round().astype(int).astype(str),
        "Team stability": pd.cut(w["stab_self"], [-1.01, -0.33, 0.33, 1.01], labels=["Low", "Mid", "High"]).astype(str),
        "Player form": pd.cut(w["form"], [-1, 2, 4, 100], labels=["<2", "2–4", ">4"]).astype(str),
        "Roster in form": pd.cut(w["c_hot"], [-10, -0.5, 0.5, 10], labels=["Cold", "Average", "Hot"]).astype(str),
        "Forecast size": pd.cut(w["pred"], [-1, 2, 3.5, 5, 100], labels=["<2", "2–3.5", "3.5–5", ">5"]).astype(str),
    }
    rows = []
    for name, key in parts.items():
        for lvl, g in w.groupby(key):
            n = len(g)
            se = g["resid"].std(ddof=1) / np.sqrt(n) if n > 2 else np.nan
            mu = g["resid"].mean()
            t = mu / se if se and se > 0 else np.nan
            rows.append({"Pattern": name, "Group": lvl, "Players": n, "Actual − forecast (pts)": mu, "t-stat": t,
                         "Signal": "▲ under-forecast" if t > 2 else ("▼ over-forecast" if t < -2 else "")})
    return pd.DataFrame(rows)


def signal_importance(train, feat_cols):
    """Permutation importance on the last two gameweeks (model fitted on earlier ones): MSE increase when a signal is shuffled.
    Computed directly (no scikit-learn helper), so it works with any model wrapper and any scikit-learn version."""
    gws = sorted(train["GW"].unique())
    if len(gws) < 4:
        return None
    tr, te = train[train["GW"] < gws[-2]], train[train["GW"] >= gws[-2]]
    c = fit_stab_coefs(tr)
    try:
        m = make_hgb().fit(tr[feat_cols].fillna(0), winsor(tr["actual"] - base_from(tr, c)))
    except Exception:
        return None
    Xte = te[feat_cols].fillna(0).to_numpy(float)
    yte = (te["actual"] - base_from(te, c)).to_numpy(float)
    base_mse = float(np.mean((yte - m.predict(Xte)) ** 2))
    rng = np.random.default_rng(0)
    out = {}
    for j_, col in enumerate(feat_cols):
        inc = []
        for _ in range(3):
            Xp = Xte.copy()
            Xp[:, j_] = rng.permutation(Xp[:, j_])
            inc.append(float(np.mean((yte - m.predict(Xp)) ** 2)) - base_mse)
        out[col] = float(np.mean(inc))
    return pd.Series(out).sort_values(ascending=False)


TUNE_GRID = {"window": [2, 3, 4, 6], "player_pull": [1.0, 2.0, 4.0], "minutes_pull": [0.5, 1.0, 2.0], "form_hl": [2, 3, 5, 8],
             "minutes_hl": [2, 3, 5], "xg_weight": [0.5, 0.75, 1.0], "team_pull": [0.5, 1.0, 2.0], "team_long": [0.0, 0.3, 0.5, 0.7],
             "prior_strength": [0.5, 1.0, 2.0], "mins_ml_w": [0.0, 0.4, 0.6, 0.8], "ctx_pow": [0.0, 0.5, 1.0],
             "w_venue": [0.25, 0.5, 0.75], "mom_cap": [0.0, 0.05, 0.1], "psi": [0.0, 0.15, 0.3], "sigma_disc": [0.0, 0.3, 0.6],
             "k_reg": [150, 300, 500]}


def history_sets(ctx, players, code, past_ctxs, quick=True, offset=0):
    """Gameweeks to score: every finished gameweek of this season (from GW3) plus every 4th (quick) or 2nd (thorough) gameweek of each
    past season. `offset` shifts the past-season sample so a held-out check uses gameweeks the search never saw (this season is excluded then)."""
    step = 4 if quick else 2
    sets = []
    if offset == 0:
        cur = [g for g in sorted(ctx["fin"]["event"].unique()) if g >= 3]
        if cur:
            sets.append(("this season", ctx, players, code, cur))
    for name_, (c_, pl_, cd_) in past_ctxs:
        gws_ = [g for g in sorted(c_["fin"]["event"].unique()) if g >= 4][offset::step]
        if gws_:
            sets.append((name_, c_, pl_, cd_, gws_))
    return sets


def history_error(sets, p):
    """Mean squared error of the structural forecast vs actual points over every set (each gameweek forecast only from earlier ones)."""
    num = den = 0.0
    parts = {}
    for name_, c_, pl_, cd_, gws_ in sets:
        tr = build_training(c_, pl_, cd_, p, only_gws=gws_, light=True)
        if len(tr):
            e = (tr["actual"] - tr["xPts0"]) ** 2
            num, den = num + float(e.sum()), den + len(e)
            parts[name_] = float(e.mean())
    return (num / den if den else float("inf")), parts


def tune_on_history(sets, p, passes=1, progress=None):
    """Coordinate descent over TUNE_GRID: change one setting at a time, keep it if the error over all history falls."""
    p = dict(p)
    before, before_parts = history_error(sets, p)
    best = before
    total = max(passes * sum(len(v) for v in TUNE_GRID.values()), 1)
    done = 0
    for pass_ in range(passes):
        improved = False
        for name, vals in TUNE_GRID.items():
            for v in vals:
                done += 1
                if progress:
                    progress(done / total, f"Pass {pass_ + 1}/{passes} · {name} = {v} · best error {best:.3f}")
                if v == p.get(name):
                    continue
                e, _ = history_error(sets, {**p, name: v})
                if e < best - 1e-4:
                    best, p, improved = e, {**p, name: v}, True
        if not improved:
            break
    _, after_parts = history_error(sets, p)
    return {"params": {k: p[k] for k in TUNE_GRID}, "before": before, "after": best,
            "before_parts": before_parts, "after_parts": after_parts}


@st.cache_data(show_spinner=False, max_entries=8)
def best_alpha(wf):
    if wf.empty:
        return 0.0
    grid = np.linspace(0, 1.5, 31)
    mse = [np.mean((wf["actual"] - np.clip(wf["xPts"] + a * wf["adj"], 0, None)) ** 2) for a in grid]
    return float(grid[int(np.argmin(mse))])


def make_bands(pred, actual, nb=8):
    edges = np.unique(np.quantile(pred, np.linspace(0, 1, nb + 1)[1:-1]))
    b = np.searchsorted(edges, pred)
    df = pd.DataFrame({"b": b, "a": np.asarray(actual)})
    st_ = df.groupby("b")["a"].agg(q10=lambda s: s.quantile(0.1), q90=lambda s: s.quantile(0.9),
                                   p5=lambda s: (s >= 5).mean(), p8=lambda s: (s >= 8).mean())
    return edges, st_.reindex(range(len(edges) + 1)).ffill().bfill()


def lookup_bands(bands, mu):
    if bands is None:
        return pd.DataFrame(np.nan, index=range(len(mu)), columns=["q10", "q90", "p5", "p8"]).to_numpy().T
    edges, stats = bands
    b = np.searchsorted(edges, mu)
    return stats.reindex(b)[["q10", "q90", "p5", "p8"]].to_numpy().T


def rank_corr(a, b):
    """Spearman rank correlation without scipy: Pearson correlation of the ranks (ties get their average rank)."""
    a, b = pd.Series(a).reset_index(drop=True), pd.Series(b).reset_index(drop=True)
    return a.rank().corr(b.rank())


@st.cache_data(show_spinner=False, max_entries=8)
def score_table(wf, alpha, god=None):
    wf = wf.copy()
    wf["aer"] = np.clip(wf["xPts"] + alpha * wf["adj"], 0, None)
    wf["god"] = np.asarray(god, float) if god is not None else wf["aer"]
    rows = []
    for g, m in wf.groupby("GW"):
        rows.append({
            "GW": g, "Players": len(m),
            "Rank corr (AER)": rank_corr(m["aer"], m["actual"]),
            "Rank corr (Base)": rank_corr(m["xPts"], m["actual"]),
            "Rank corr (Form)": rank_corr(m["form"], m["actual"]),
            "Rank corr (Solve)": rank_corr(m["god"], m["actual"]),
            "Avg miss (AER)": (m["aer"] - m["actual"]).abs().mean(),
            "Avg miss (Base)": (m["xPts"] - m["actual"]).abs().mean(),
            "Avg miss (Form)": (m["form"] - m["actual"]).abs().mean(),
            "Avg miss (Solve)": (m["god"] - m["actual"]).abs().mean(),
            "Bias (AER)": (m["aer"] - m["actual"]).mean(),
            "Top-20 pts (AER)": m.nlargest(20, "aer")["actual"].mean(),
            "Top-20 pts (Base)": m.nlargest(20, "xPts")["actual"].mean(),
            "Top-20 pts (Form)": m.nlargest(20, "form")["actual"].mean(),
            "Top-20 pts (Solve)": m.nlargest(20, "god")["actual"].mean(),
            "Within ±2 (AER)": ((m["aer"] - m["actual"]).abs() <= 2).mean(),
        })
    var = ((wf["actual"] - wf["actual"].mean()) ** 2).sum()
    r2 = lambda c: 1 - ((wf["actual"] - wf[c]) ** 2).sum() / var
    return pd.DataFrame(rows), {"r2_aer": r2("aer"), "r2_base": r2("xPts"), "r2_form": r2("form"), "r2_god": r2("god")}



# =========================================================
# ZIPF xVR — Zipf / Pareto expected-value rating
# =========================================================
ZIPF_TOP = 0.20            # Pareto split: the top 20% of players who played in a gameweek
ZIPF_HL = 4.0              # half-life (gameweeks) for a player's elite history
BPS_FEATS = ["z_bps_avg", "z_bps_floor", "z_bps_ceil", "bps_vol"]
ZIPF_FEATS = ["xPts", "g90", "a90", "threat_6", "creativity_6", "bps_6", "mins_6", "start_6", "form", "dc90", "elite_rate",
              "pos_gk", "pos_def", "pos_mid"] + BPS_FEATS


@st.cache_data(show_spinner=False, max_entries=4)
def zipf_table(h, exp_rows, baseline="position"):
    """Every finished gameweek: all players who played, points over expectation, Zipf rank, the top-20% 'vital few', the Zipf exponent
    fitted on them (log surplus vs log rank) and each elite player's credit = points / rank^s x (1 + minutes fraction).
    `exp_rows` (id, GW, xPts) are the model's pre-gameweek forecasts; where missing, the expectation is the position's points per 90
    in that gameweek x minutes played."""
    d = h[h["minutes"] > 0][["id", "round", "element_type", "minutes", "total_points", "n_played"]].rename(columns={"round": "GW"}).copy()
    if d.empty:
        return pd.DataFrame(), pd.DataFrame()
    # baseline "position" (default): points over what an ordinary player in the same position would score in the same minutes (Theta Swole's
    # 'compare with the position' idea). baseline "forecast": points over the model's own pre-gameweek forecast for that player (rewards surprise).
    if baseline == "forecast" and exp_rows is not None and len(exp_rows):
        d = d.merge(exp_rows.rename(columns={"xPts": "exp"}).drop_duplicates(["id", "GW"]), on=["id", "GW"], how="left")
    else:
        d["exp"] = np.nan
    grp = d.groupby(["GW", "element_type"])
    p90 = grp["total_points"].transform("sum") / (grp["minutes"].transform("sum") / 90.0).replace(0, np.nan)
    d["exp"] = d["exp"].fillna(p90 * d["minutes"] / 90.0)
    d["surplus"] = d["total_points"] - d["exp"]
    rows, law = [], []
    for g, gd in d.groupby("GW"):
        n = len(gd)
        gd = gd.sort_values("surplus", ascending=False).assign(rank=np.arange(1, n + 1))
        k = max(1, int(np.ceil(ZIPF_TOP * n)))
        el = gd.head(k)
        pos = el[el["surplus"] > 0]
        s_, r2 = 1.0, np.nan
        if len(pos) >= 8:
            x, y = np.log(pos["rank"].to_numpy(float)), np.log(pos["surplus"].to_numpy(float))
            b, a = np.polyfit(x, y, 1)
            s_ = float(np.clip(-b, 0.2, 2.5))
            r2 = float(np.corrcoef(x, y)[0, 1] ** 2)
        mfrac = (gd["minutes"] / (90.0 * gd["n_played"].clip(lower=1))).clip(0, 1)
        elite = gd["rank"] <= k
        credit = np.where(elite, gd["total_points"].clip(lower=0) / gd["rank"].astype(float) ** s_ * (1 + mfrac), 0.0)
        rows.append(gd.assign(N=n, elite=elite.astype(float), credit=credit, s=s_))
        pts_pos = gd["total_points"].clip(lower=0)
        law.append({"GW": int(g), "Players who played": n, "Top 20%": k, "Zipf exponent s": s_, "Log-log fit R²": r2,
                    "Share of points scored by the top 20%": float(pts_pos[elite.to_numpy()].sum() / max(pts_pos.sum(), 1e-9))})
    return pd.concat(rows, ignore_index=True), pd.DataFrame(law)


def zipf_history(Z, hl=ZIPF_HL):
    """For each player and gameweek, using only EARLIER gameweeks: decayed elite rate (shrunk toward the 20% base rate) and his average
    credit when elite. Also the same numbers after the latest gameweek (key = last GW + 1) for live use."""
    if Z.empty:
        return pd.DataFrame(columns=["id", "GW", "elite_rate", "credit_mean", "n_elite"])
    gws = sorted(Z["GW"].unique())
    E = Z.pivot_table(index="id", columns="GW", values="elite", aggfunc="max")
    C = Z.pivot_table(index="id", columns="GW", values="credit", aggfunc="sum").reindex_like(E)
    P = E.notna()
    E, C = E.fillna(0.0), C.fillna(0.0)
    dec = 0.5 ** (1.0 / hl)
    S = W = CS = NE = np.zeros(len(E))
    out = []
    for g in gws + [gws[-1] + 1]:
        rate = (S + 0.2 * 1.0) / (W + 1.0)
        cmean = np.where(NE > 0, CS / np.maximum(NE, 1e-9), np.nan)
        out.append(pd.DataFrame({"id": E.index, "GW": g, "elite_rate": rate, "credit_mean": cmean, "n_elite": NE}))
        if g in E.columns:
            e_, c_, p_ = E[g].to_numpy(), C[g].to_numpy(), P[g].to_numpy().astype(float)
            S, W = S * dec + e_ * p_, W * dec + p_
            CS, NE = CS + c_, NE + e_
    return pd.concat(out, ignore_index=True)


THETA_FEATS = ["theta_rel", "dps_resid", "spread", "upside"]   # Theta Swole's ingredients, as the elite model sees them


def _zipf_X(frame, bps=True, theta=False):
    X = pd.DataFrame(index=frame.index)
    feats_ = (ZIPF_FEATS if bps else [f_ for f_ in ZIPF_FEATS if f_ not in BPS_FEATS]) + (THETA_FEATS if theta else [])
    for c_ in feats_:
        if c_ == "pos_gk":
            X[c_] = (frame["element_type"] == 1).astype(float)
        elif c_ == "pos_def":
            X[c_] = (frame["element_type"] == 2).astype(float)
        elif c_ == "pos_mid":
            X[c_] = (frame["element_type"] == 3).astype(float)
        else:
            X[c_] = pd.to_numeric(frame[c_], errors="coerce") if c_ in frame.columns else 0.0
    return X.fillna(0.0)


def logit_fit(X, y, l2=1.0, iters=60):
    """L2-regularised logistic regression by Newton steps on standardised features. Returns (weights, standard errors, mean, sd)."""
    X, y = np.asarray(X, float), np.asarray(y, float)
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z1 = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    w = np.zeros(Z1.shape[1])
    pen = np.r_[0.0, np.full(Z1.shape[1] - 1, l2)]
    for _ in range(iters):
        p_ = 1 / (1 + np.exp(-np.clip(Z1 @ w, -30, 30)))
        H = Z1.T @ (Z1 * (p_ * (1 - p_))[:, None]) + np.diag(pen)
        step = np.linalg.solve(H, Z1.T @ (y - p_) - pen * w)
        w += step
        if np.abs(step).max() < 1e-7:
            break
    se = np.sqrt(np.clip(np.diag(np.linalg.inv(H)), 0, None))
    return w, se, mu, sd


def logit_predict(model, X):
    w, _, mu, sd = model
    z = np.column_stack([np.ones(len(X)), (np.asarray(X, float) - mu) / sd]) @ w
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def _wq(v, w, q):
    """Weighted quantile (linear interpolation on the weighted CDF)."""
    o = np.argsort(v)
    v, w = np.asarray(v, float)[o], np.asarray(w, float)[o]
    c = (np.cumsum(w) - 0.5 * w) / max(w.sum(), 1e-12)
    return float(np.interp(q, c, v))


@st.cache_data(show_spinner=False, max_entries=48)
def bps_profile(h, cutoff, hl=6.0, k=3.0):
    """Each player's BPS per 90 from games of 30+ minutes before `cutoff` (recent games weighted most): average, floor (10th percentile),
    ceiling (90th percentile) and volatility, each shrunk toward his position's values by k pseudo-games, then expressed as z-scores and
    percentiles WITHIN position (defenders and forwards collect BPS in different ways, so they are only compared with their own kind)."""
    cols = ["element_type", "bps_avg", "bps_floor", "bps_ceil", "bps_vol", "bps_n", "z_bps_avg", "z_bps_floor", "z_bps_ceil",
            "pct_bps_avg", "pct_bps_floor", "pct_bps_ceil"]
    d = h[(h["round"] < cutoff) & (h["minutes"] >= 30)][["id", "element_type", "round", "minutes", "bps"]].copy()
    if d.empty:
        return pd.DataFrame(columns=cols)
    d["b90"] = d["bps"] * 90.0 / d["minutes"].clip(lower=30)
    d["w"] = 0.5 ** ((cutoff - 1 - d["round"]) / hl)
    pri = {et: (float(np.average(g["b90"], weights=g["w"])), _wq(g["b90"], g["w"], 0.1), _wq(g["b90"], g["w"], 0.9))
           for et, g in d.groupby("element_type")}
    rows = []
    for pid, g in d.groupby("id"):
        et = int(g["element_type"].iloc[0])
        n = float(len(g))
        own = (float(np.average(g["b90"], weights=g["w"])), _wq(g["b90"], g["w"], 0.1), _wq(g["b90"], g["w"], 0.9))
        sh = [(n * o_ + k * p_) / (n + k) for o_, p_ in zip(own, pri[et])]
        rows.append({"id": pid, "element_type": et, "bps_avg": sh[0], "bps_floor": sh[1], "bps_ceil": sh[2], "bps_n": n})
    out = pd.DataFrame(rows).set_index("id")
    out["bps_vol"] = (out["bps_ceil"] - out["bps_floor"]) / out["bps_avg"].clip(lower=1.0)
    for c_ in ("avg", "floor", "ceil"):
        g_ = out.groupby("element_type")[f"bps_{c_}"]
        out[f"z_bps_{c_}"] = ((out[f"bps_{c_}"] - g_.transform("mean")) / (g_.transform("std") + 1e-9)).clip(-3, 3)
        out[f"pct_bps_{c_}"] = g_.rank(pct=True)
    return out[cols]


def bps_profiles_all(h, gws):
    """bps_profile as it stood before each gameweek in `gws` (for leak-free training rows)."""
    parts = [bps_profile(h, int(g)).assign(GW=int(g)).reset_index() for g in gws]
    parts = [p_ for p_ in parts if len(p_)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["id", "GW"] + BPS_FEATS)


@st.cache_data(show_spinner=False, max_entries=4)
def bonus_sim(slim, fixtures, n=4000, seed=0):
    """Monte Carlo of the bonus race in every fixture: each player plays (start / sub / not) with his playing-time probabilities, scores a
    BPS drawn from his own floor-average-ceiling profile (a two-piece normal: floor and ceiling are its 10th and 90th percentiles), scaled
    by how good this fixture is for him; the top three BPS in the match get 3, 2 and 1 bonus points. Returns expected bonus,
    P(3 bonus) and P(any bonus) per player."""
    rng = np.random.default_rng(seed)
    out = pd.DataFrame({"xBonus": 0.0, "P3": 0.0, "Pany": 0.0}, index=slim.index)
    for _, f in fixtures.iterrows():
        sub = slim[slim["team"].isin([f["team_h"], f["team_a"]]) & ((slim["start_p"] + slim["sub_p"]) > 0.03)]
        if len(sub) < 3:
            continue
        m = len(sub)
        u, z = rng.random((n, m)), rng.standard_normal((n, m))
        stp, sbp = sub["start_p"].to_numpy(float), sub["sub_p"].to_numpy(float)
        med = sub["bps_avg"].to_numpy(float)
        up = np.clip(sub["bps_ceil"].to_numpy(float) - med, 1.0, None) / 1.2816
        dn = np.clip(med - sub["bps_floor"].to_numpy(float), 1.0, None) / 1.2816
        b = (med + np.where(z > 0, z * up, z * dn)) * sub["scale"].to_numpy(float)
        b = np.where(u < stp, b, np.where(u < stp + sbp, b * 0.3, -1e9))
        order = np.argsort(-b, axis=1)
        bonus = np.zeros((n, m))
        r_ = np.arange(n)
        for k_, pts_ in ((0, 3.0), (1, 2.0), (2, 1.0)):
            ix = order[:, k_]
            ok = b[r_, ix] > -1e8
            bonus[r_[ok], ix[ok]] = pts_
        out.loc[sub.index, "xBonus"] += bonus.mean(0)
        out.loc[sub.index, "P3"] = 1 - (1 - out.loc[sub.index, "P3"]) * (1 - (bonus == 3).mean(0))
        out.loc[sub.index, "Pany"] = 1 - (1 - out.loc[sub.index, "Pany"]) * (1 - (bonus > 0).mean(0))
    return out


def _credit_fit_raw(rows, lam=5.0):
    el = rows[rows["elite"] > 0]
    if len(el) < 40:
        return None
    X = _zipf_X(el).to_numpy()
    y = np.log1p(el["credit"].clip(lower=0).to_numpy(float))
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Zs = (X - mu) / sd
    b = float(y.mean())
    w = np.linalg.solve(Zs.T @ Zs + lam * np.eye(Zs.shape[1]), Zs.T @ (y - b))
    s2 = float(np.var(y - (b + Zs @ w)))
    return w, b, mu, sd, s2


@st.cache_data(show_spinner=False, max_entries=4)
def credit_fit(rows):
    """How big a player's top-20% week tends to be (his Zipf credit), from the same pre-gameweek signals incl. BPS ceiling, fitted on
    past elite weeks (ridge regression on log credit)."""
    return _credit_fit_raw(rows)


def credit_predict(m, X):
    w, b, mu, sd, s2 = m
    return np.clip(np.expm1(b + ((np.asarray(X, float) - mu) / sd) @ w + s2 / 2), 0, None)


def zipf_training_rows(train, Z, hist_tab, h=None):
    """Pre-gameweek feature rows (the model's own forecasts and recent stats) labelled with whether the player made that gameweek's top 20%."""
    if train is None or train.empty or Z.empty:
        return pd.DataFrame()
    t = train[train["GW"] >= 1].copy() if "season" not in train.columns else train[train["season"] == "this season"].copy()
    t = t.merge(Z[["id", "GW", "elite", "credit", "total_points"]], on=["id", "GW"], how="left")
    t["elite"] = t["elite"].fillna(0.0)
    t = t.merge(hist_tab[["id", "GW", "elite_rate", "credit_mean", "n_elite"]], on=["id", "GW"], how="left")
    t["elite_rate"] = t["elite_rate"].fillna(0.2)
    if h is not None and len(t):
        bp = bps_profiles_all(h, tuple(sorted(int(g_) for g_ in t["GW"].unique())))
        if len(bp):
            t = t.merge(bp[["id", "GW"] + BPS_FEATS], on=["id", "GW"], how="left")
    return t


@st.cache_data(show_spinner=False, max_entries=4)
def zipf_model(rows, theta=False, bps=True):
    """Chance of making the top 20% from pre-gameweek signals (BPS profile and Theta Swole's ingredients optional)."""
    if rows is None or len(rows) < 300 or rows["elite"].sum() < 30:
        return None
    return logit_fit(_zipf_X(rows, bps=bps, theta=theta).to_numpy(), rows["elite"].to_numpy())


def _auc(score, y):
    """Probability that a random top-20% player is scored above a random other player (0.5 = guessing)."""
    y = np.asarray(y, float)
    ra = pd.Series(np.asarray(score, float)).rank().to_numpy()
    npos, nneg = y.sum(), len(y) - y.sum()
    return float((ra[y == 1].sum() - npos * (npos + 1) / 2) / max(npos * nneg, 1)) if npos and nneg else np.nan


@st.cache_data(show_spinner=False, max_entries=4)
def add_theta_feats(rows, wf, alpha, conviction, mode, n_teams):
    """Theta Swole's ingredients for every past player-gameweek, using only what was known before it:
    theta_rel = forecast minus the replacement level at his position that gameweek (Theta's 'points above replacement');
    dps_resid = the part of the DPS formula that AER doesn't contain (Theta's DPS signal, before its weight beta);
    spread = Ceiling - Floor and upside = Ceiling - AER (Theta's risk-appetite term), from out-of-sample calibrated intervals."""
    r = rows.copy()
    if r.empty:
        return r
    r["theta_rel"] = 0.0
    for (g_, et_), grp in r.groupby(["GW", "element_type"]):
        v_ = grp["xPts"].sort_values(ascending=False)
        repl_ = float(v_.iloc[min(n_teams * THETA_SLOTS.get(int(et_), 2), len(v_) - 1)])
        r.loc[grp.index, "theta_rel"] = grp["xPts"] - repl_
    if wf is not None and len(wf):
        w = wf.assign(aer=god_expanding(wf, alpha, conviction))
        lo, hi = calibrated_interval(w, w["aer"])
        w = w.assign(spread=(hi - lo).to_numpy(), upside=(hi - w["aer"]).to_numpy())
        r = r.merge(w[["id", "GW", "spread", "upside"]].drop_duplicates(["id", "GW"]), on=["id", "GW"], how="left")
        hist_ = dps_history(wf, alpha, conviction, mode)
        if hist_:
            dr_ = pd.concat(hist_, ignore_index=True)[["id", "GW", "resid"]].rename(columns={"resid": "dps_resid"})
            r = r.merge(dr_.drop_duplicates(["id", "GW"]), on=["id", "GW"], how="left")
    for c_ in ("spread", "upside", "dps_resid"):
        if c_ not in r.columns:
            r[c_] = np.nan
    for c_ in ("spread", "upside"):
        r[c_] = r[c_].fillna(r.groupby(["GW", "element_type"])[c_].transform("median")).fillna(r[c_].median()).fillna(0.0)
    r["dps_resid"] = r["dps_resid"].fillna(0.0)
    return r


@st.cache_data(show_spinner=False, max_entries=2)
def zipf_transfer_check(season, p_key):
    """Does FPL's 'transfers in' for a gameweek predict making that gameweek's top 20%, beyond the other signals? Tested on a past
    season (the only place the API history includes gameweek transfer counts). Returns (coefficient per standard deviation, z-score, rows)."""
    try:
        ctx_p, pl_p, _ = past_context(season)
        tr_p = past_training(season, p_key)
        gw_raw, _, _ = load_past_raw(season)
    except Exception:
        return 0.0, 0.0, 0
    if tr_p is None or tr_p.empty or "transfers_in" not in gw_raw.columns:
        return 0.0, 0.0, 0
    Zp, _ = zipf_table(ctx_p["h"], tr_p[["id", "GW", "xPts"]], "position")
    rows = zipf_training_rows(tr_p.assign(season="this season"), Zp, zipf_history(Zp), ctx_p["h"])
    tin = gw_raw.rename(columns={"element": "id", "round": "GW"})[["id", "GW", "transfers_in"]]
    tin = tin.assign(transfers_in=pd.to_numeric(tin["transfers_in"], errors="coerce")).groupby(["id", "GW"], as_index=False)["transfers_in"].max()
    rows = rows.merge(tin, on=["id", "GW"], how="left")
    lt = np.log1p(rows["transfers_in"].fillna(0).clip(lower=0))
    rows["z_tr"] = (lt - lt.groupby(rows["GW"]).transform("mean")) / (lt.groupby(rows["GW"]).transform("std") + 1e-9)
    if len(rows) < 500 or rows["elite"].sum() < 50:
        return 0.0, 0.0, len(rows)
    X = np.column_stack([_zipf_X(rows).to_numpy(), rows["z_tr"].to_numpy()])
    w, se, _, sd = logit_fit(X, rows["elite"].to_numpy())
    coef = float(w[-1] / sd[-1])                 # per 1 sd of the (already standardised) transfers signal
    return coef, float(w[-1] / max(se[-1], 1e-9)), int(len(rows))


@st.cache_data(ttl=3600, show_spinner=False)
def load_deadlines():
    """Gameweek deadlines from the main FPL game (the Draft lineup deadline follows the same schedule)."""
    try:
        ev = make_session().get(f"{FPL}/bootstrap-static/", timeout=15).json()["events"]
        return {int(e["id"]): e.get("deadline_time") for e in ev}
    except Exception:
        return {}


@st.cache_data(ttl=900, show_spinner=False)
def load_transfers_in():
    """FPL's live 'transfers in this gameweek' (main game, updates until the deadline), keyed by the player's permanent code."""
    try:
        el = pd.DataFrame(make_session().get(f"{FPL}/bootstrap-static/", timeout=15).json()["elements"])
        return el.set_index("code")["transfers_in_event"].astype(float)
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(show_spinner=False, max_entries=4)
@st.cache_data(show_spinner=False, max_entries=4)
def zipf_backtest(rows):
    """Walk-forward check: each gameweek, fit on earlier gameweeks only, then score how well xVR picked that gameweek's top 20%."""
    out = []
    if rows is None or rows.empty:
        return pd.DataFrame()
    cpos = rows[rows["elite"] > 0].groupby("element_type")["credit"].mean()
    for g in sorted(rows["GW"].unique()):
        tr, te = rows[rows["GW"] < g], rows[rows["GW"] == g]
        if tr["GW"].nunique() < 2 or len(tr) < 300 or tr["elite"].sum() < 30 or te["elite"].sum() < 3:
            continue
        m_ = logit_fit(_zipf_X(tr).to_numpy(), tr["elite"].to_numpy())
        p_ = logit_predict(m_, _zipf_X(te).to_numpy())
        m0_ = logit_fit(_zipf_X(tr, bps=False).to_numpy(), tr["elite"].to_numpy())
        p0_ = logit_predict(m0_, _zipf_X(te, bps=False).to_numpy())
        has_t = all(c_ in rows.columns for c_ in THETA_FEATS)
        if has_t:
            mt_ = logit_fit(_zipf_X(tr, theta=True).to_numpy(), tr["elite"].to_numpy())
            pt_ = logit_predict(mt_, _zipf_X(te, theta=True).to_numpy())
        cf_ = _credit_fit_raw(tr)
        prior_ = te["element_type"].map(cpos).fillna(cpos.mean()).to_numpy(float)
        cmod_ = credit_predict(cf_, _zipf_X(te).to_numpy()) if cf_ is not None else prior_
        ne_ = te["n_elite"].fillna(0).to_numpy(float) if "n_elite" in te.columns else np.zeros(len(te))
        cm = np.expm1((np.log1p(te["credit_mean"].fillna(0).clip(lower=0).to_numpy(float)) * ne_ + 3.0 * np.log1p(np.clip(cmod_, 0, None))) / (ne_ + 3.0))
        xvr = p_ * cm
        y = te["elite"].to_numpy()
        ra = pd.Series(p_).rank().to_numpy()
        npos, nneg = y.sum(), len(y) - y.sum()
        auc = float((ra[y == 1].sum() - npos * (npos + 1) / 2) / max(npos * nneg, 1))
        ra0 = pd.Series(p0_).rank().to_numpy()
        auc0 = float((ra0[y == 1].sum() - npos * (npos + 1) / 2) / max(npos * nneg, 1))
        pts = te["total_points"].fillna(0).to_numpy()
        top = lambda v: float(pts[np.argsort(-np.asarray(v))[:20]].mean())
        haul_ = pts >= np.quantile(pts[pts > 0], 0.8) if (pts > 0).sum() >= 10 else pts > 0       # top 20% by raw points among those who scored
        hauls = lambda v: float(haul_[np.argsort(-np.asarray(v))[:20]].mean())
        row_ = {"GW": int(g), "Hauls among xVR top 20": hauls(xvr), "AUC (picking the top 20%)": auc, "AUC without BPS profile": auc0, "Top-20 pts by Zipf xVR": top(xvr),
                "Top-20 pts by AER": top(te["xPts"].to_numpy()), "Rank corr xVR vs points": rank_corr(pd.Series(xvr), pd.Series(pts))}
        if has_t:
            th_ = te["theta_rel"].to_numpy(float)
            row_.update({"AUC with Theta Swole inputs": _auc(pt_, y), "AUC of Theta Swole alone": _auc(th_, y),
                         "Top-20 pts by converged xVR": top(pt_ * cm), "Top-20 pts by Theta Swole": top(th_),
                         "Rank corr Theta Swole vs xVR": rank_corr(pd.Series(th_), pd.Series(xvr))})
        out.append(row_)
    return pd.DataFrame(out)



# =========================================================
# SIDEBAR + LOAD
# =========================================================
_rerun = getattr(st, "rerun", None) or st.experimental_rerun
_saved_tune = load_tuned()
if _saved_tune and st.session_state.get("use_tuned", True) and not st.session_state.get("_tuned_applied"):
    for _k, _v in (_saved_tune.get("params") or {}).items():   # start the session from the last saved tune
        st.session_state[f"s_{_k}"] = _v
    st.session_state["_tuned_applied"] = True
_pending = st.session_state.pop("god_pending", None)     # settings chosen by Solve FPL / the tuner, applied before widgets are built
if _pending:
    for _k, _v in _pending.items():
        st.session_state[_k] = _v
with st.sidebar.expander(":material/settings: League & gameweek", expanded=True):
    st.caption("Hover the ⓘ next to any control for what it does. The full guide is in the 📖 How it works tab.")
    try:
        _qp = st.query_params
        qp_team = int(str(_qp.get("team", "")).strip()) if str(_qp.get("team", "")).strip().isdigit() else None
        qp_league = int(str(_qp.get("league", "")).strip()) if str(_qp.get("league", "")).strip().isdigit() else None
    except Exception:
        qp_team = qp_league = None
    if not st.session_state.get("_qp_applied") and (qp_team or qp_league):
        lg_found = qp_league or (entry_league(qp_team) if qp_team else None)
        if lg_found:
            st.session_state["league_id"] = int(lg_found)
        st.session_state["_qp_applied"] = True
        st.session_state["_qp_team_pending"] = qp_team
    st.session_state.setdefault("league_id", 46939)
    league_id = st.number_input("FPL Draft League ID", step=1, key="league_id", help=HELP["league_id"])
    if st.button(":material/refresh: Refresh data", help=HELP["refresh"]):
        st.cache_data.clear()
        st.cache_resource.clear()

    players, teams, fx_raw, taken_ids = load_core(int(league_id))
    fx = prep_fixtures(fx_raw)
    code = dict(zip(teams["id"], teams["short_name"]))
    started = sorted(fx[fx["finished"]]["event"].unique().tolist())
    hist = load_history(tuple(started)) if started else pd.DataFrame({"id": [], "round": []})
    unfinished = fx.loc[~fx["finished"], "event"]
    next_gw = int(unfinished.min()) if len(unfinished) else 38
    # FPL publishes expected points only for the current (ep_this) and next (ep_next) gameweek
    _gw_started = bool("started" in fx.columns and fx.loc[fx["event"] == next_gw, "started"].fillna(False).astype(bool).any())
    ep_map = {next_gw: "ep_this", next_gw + 1: "ep_next"} if _gw_started else {next_gw: "ep_next"}

    selected_gw = st.selectbox("Target Gameweek", list(range(1, 39)), index=max(0, min(37, next_gw - 1)), help=HELP["target_gw"])
    st.session_state.setdefault("s_horizon", 1)
    horizon = st.slider("Gameweeks to sum", 1, 6, key="s_horizon", help=HELP["horizon"])

with st.sidebar.expander(":material/person: Your team", expanded=True):
    _lg_side = load_league(int(league_id))
    _ents_side = _lg_side["entries"]
    if not _ents_side.empty and {"id", "entry_name"} <= set(_ents_side.columns):
        _lab_side = {int(i_): str(n_) for i_, n_ in zip(_ents_side["id"], _ents_side["entry_name"])}
        _pt = st.session_state.pop("_qp_team_pending", None)
        if _pt and "entry_id" in _ents_side.columns:       # team from the link: select it
            _hit = _ents_side[_ents_side["entry_id"].astype(int) == int(_pt)]
            if len(_hit):
                st.session_state["my_entry"] = int(_hit["id"].iloc[0])
            else:
                st.warning(f"Team {_pt} isn't in league {league_id}. Check the link.")
        if st.session_state.get("my_entry") not in _lab_side:
            st.session_state["my_entry"] = None
        my_entry_sel = st.selectbox("Your team", [None] + list(_lab_side), key="my_entry", help=HELP["my_entry"],
                                            format_func=lambda i_: "— pick your team —" if i_ is None else _lab_side[i_])
        if my_entry_sel is not None and "entry_id" in _ents_side.columns:
            _eid = _ents_side.loc[_ents_side["id"].astype(int) == int(my_entry_sel), "entry_id"]
            if len(_eid):
                st.caption(f"Share link for this team: add **?team={int(_eid.iloc[0])}** to the end of the app's web address.")
    else:
        my_entry_sel = None
        st.caption("Couldn't load this league's teams (check the league ID).")
    st.session_state.setdefault("s_wpa_h", 3)
    st.session_state.setdefault("s_wpa_decay", 0.85)
    wpa_h = st.slider("WPA: gameweeks ahead", 1, 5, key="s_wpa_h", help=HELP["wpa_h"])
    wpa_decay = st.slider("WPA: later weeks weight", 0.5, 1.0, step=0.05, key="s_wpa_decay", help=HELP["wpa_decay"])

with st.sidebar.expander(":material/filter_list: Filters", expanded=False):
    st.caption("Display only: filters change what is shown, not the forecasts.")
    selected_team = st.selectbox("Team", ["All Teams"] + sorted(teams["name"].unique().tolist()), help=HELP["f_team"])
    sel_pos = st.multiselect("Positions", ["GKP", "DEF", "MID", "FWD"], default=["GKP", "DEF", "MID", "FWD"], help=HELP["f_pos"])
    min_minutes = st.number_input("Minimum season minutes", 0, value=0, step=90, help=HELP["f_min"])
    include_out = st.checkbox("Include injured / suspended / out", value=False, help=HELP["f_out"])
    only_wire = st.checkbox("Only unowned players", value=True, help=HELP["f_wire"])
    search_q = st.text_input("Search player", "", help=HELP["f_search"])


with st.sidebar.expander(":material/science: Model", expanded=False):
    st.session_state.setdefault("use_learning", True)
    st.session_state.setdefault("god_on", False)
    st.session_state.setdefault("use_career", False)
    st.session_state.setdefault("god_conv", 1.0)
    use_learning = st.checkbox("Use learned correction", key="use_learning", help=HELP["use_learning"])
    use_career = st.checkbox("Use career finishing baseline", key="use_career", help=HELP["use_career"])
    god_on = st.checkbox("Solve FPL active", key="god_on", help=HELP["god_on"])
    god_conv = st.slider("Solve FPL conviction ×", 0.0, 2.0, step=0.1, key="god_conv", help=HELP["god_conv"])


    def _slider(label, key, lo, hi, default, step, **kw):
        st.session_state.setdefault(key, default)
        return st.slider(label, lo, hi, step=step, key=key, **kw)


    def _kslider(label, key, lo, hi, default, step, **kw):
        """Keyed slider for use inside expanders, so the tuner can set it."""
        st.session_state.setdefault(key, default)
        return st.slider(label, lo, hi, step=step, key=key, **kw)


    p = dict(
        window=_slider("Recent games (split home/away)", "s_window", 1, 8, 3, 1, help=HELP["window"]),
        player_pull=_slider("Recent-form smoothing (0 = raw)", "s_player_pull", 0.0, 6.0, 2.0, 0.5, help=HELP["player_pull"]),
        minutes_pull=_slider("Minutes smoothing", "s_minutes_pull", 0.0, 3.0, 1.0, 0.5, help=HELP["minutes_pull"]),
        team_pull=_slider("Team smoothing (× data-driven)", "s_team_pull", 0.0, 3.0, 1.0, 0.25, help=HELP["team_pull"]),
        xg_weight=_slider("Trust xG/xA over actual G/A", "s_xg_weight", 0.0, 1.0, 0.75, 0.05, help=HELP["xg_weight"]),
        use_defcon=st.checkbox("Score DEFCON points", value=True, help=HELP["use_defcon"]),
        form_hl=3, minutes_hl=3, prior_strength=1.0, stratify=True, team_long=0.5, mins_ml_w=0.6, **MASTER_DEFAULTS,
    )
    p["use_career"] = bool(use_career)
with st.sidebar.expander(":material/build: Advanced model"):
    st.session_state.setdefault("s_form_hl", 3)
    st.session_state.setdefault("s_stratify", True)
    p["form_hl"] = st.slider("Recent-form half-life (GWs)", 1, 12, key="s_form_hl", help=HELP["form_hl"])
    p["minutes_hl"] = _kslider("Season minutes half-life (GWs)", "s_minutes_hl", 1, 8, 3, 1, help=HELP["minutes_hl"])
    p["prior_strength"] = _kslider("Season shrinkage (× data-driven)", "s_prior_strength", 0.25, 3.0, 1.0, 0.25, help=HELP["prior_strength"])
    st.session_state.setdefault("s_team_long", 0.5)
    st.session_state.setdefault("s_mins_ml_w", 0.6)
    p["team_long"] = st.slider("Long-memory team strength weight", 0.0, 1.0, step=0.1, key="s_team_long", help=HELP["team_long"])
    p["mins_ml_w"] = st.slider("Learned minutes model weight", 0.0, 1.0, step=0.1, key="s_mins_ml_w", help=HELP["mins_ml_w"])
    p["stratify"] = st.checkbox("Separate models for DEF/GK, MID, FWD", key="s_stratify", help=HELP["stratify"])
with st.sidebar.expander(":material/functions: Master xP equation"):
    st.session_state.setdefault("s_master", True)
    st.session_state.setdefault("s_extras", True)
    st.session_state.setdefault("s_ctx_pow", 1.0)
    p["master"] = st.checkbox("Use the master equation as the AER core", key="s_master", help=HELP["master"])
    p["extras"] = st.checkbox("Also score rules the equation leaves out", key="s_extras", help=HELP["extras"])
    p["ctx_pow"] = st.slider("Context strength (1 = as written)", 0.0, 1.5, step=0.25, key="s_ctx_pow", help=HELP["ctx_pow"])
    p["w_venue"] = _kslider("Venue weight", "s_w_venue", 0.0, 1.0, 0.5, 0.05, help=HELP["w_venue"])
    p["mom_cap"] = _kslider("Momentum cap (± share)", "s_mom_cap", 0.0, 0.2, 0.10, 0.01, help=HELP["mom_cap"])
    p["w1"] = st.slider("Morale: squad fitness weight", 0.0, 1.0, 0.0, 0.05, help=HELP["w1"])
    p["w2"] = st.slider("Morale: manager news weight (stability-grid edits)", 0.0, 0.3, 0.10, 0.01, help=HELP["w2"])
    p["w3"] = st.slider("Morale: betting-line shift weight", 0.0, 1.0, 0.5, 0.05, help=HELP["w3"])
    p["sigma_disc"] = _kslider("Discipline sensitivity (σ)", "s_sigma_disc", 0.0, 1.0, 0.3, 0.05, help=HELP["sigma_disc"])
    p["psi"] = _kslider("Defensive-error sensitivity (ψ)", "s_psi", 0.0, 0.5, 0.15, 0.05, help=HELP["psi"])
    p["k_reg"] = _kslider("Finishing regression K (shots)", "s_k_reg", 50, 600, 300, 25, help=HELP["k_reg"])
with st.sidebar.expander(":material/tune: Context effects", expanded=False):
    desperation_impact = _slider("Desperation impact ×", "s_desperation_impact", 0.0, 3.0, 1.0, 0.25, help=HELP["desperation"])
    st.session_state.setdefault("s_use_squad_avail", True)
    use_squad_avail = st.checkbox("Redistribute injured players' output", key="s_use_squad_avail", help=HELP["squad_avail"])
    stability_impact = 1.0  # stability comes only from the fixture grid; effect size is fitted from data
    leader_boost = _slider("Captain / veteran boost", "s_leader_boost", 0.0, 1.5, 0.5, 0.1, help=HELP["leader"])
    hmpr_impact = _slider("Historical rating impact ×", "s_hmpr_impact", 0.0, 3.0, 1.0, 0.25, help=HELP["hmpr"])
    p["hm_min"] = st.slider("History games: min minutes", 45, 90, 70, 5, help=HELP["hm_min"])
    ep_w = _slider("Blend FPL's ep_next (unvalidated)", "s_ep_w", 0.0, 0.5, 0.0, 0.05, help=HELP["ep_w"])

with st.sidebar.expander(":material/percent: Bookmaker odds", expanded=False):
    odds_key = st.text_input("The Odds API key (free at the-odds-api.com)", type="password", value="",
                                     help=HELP["odds_key"])
    odds_regions = st.text_input("Bookmaker regions", "us", help=HELP["odds_regions"])
    mkt_on = st.checkbox("Use bookmaker priors", value=False, key="mkt_on", help=HELP["mkt_on"])
    mkt_w = st.slider("Market weight", 0.0, 1.0, 0.7, 0.05, help=HELP["mkt_w"])
    mkt_margin = st.slider("Anytime-scorer margin to strip (%)", 0, 30, 10, help=HELP["mkt_margin"]) / 100.0
    fetch_odds_clicked = st.button("Fetch odds for this gameweek", help=HELP["fetch_odds"])
    st.session_state.setdefault("dd_scale5", "Sum of the five 0–1 indices (as specified)")
with st.sidebar.expander(":material/sort: Ranking options", expanded=False):
    st.caption("DPS and Theta Swole are calculated for every player. The DPS selected team sits above the Rankings table.")
    if st.session_state.get("rank_by") == "Zipf xVR":
        st.session_state["rank_by"] = "Haul points"
    if st.session_state.get("rank_by") not in ("WPA", "Green score", "Haul points", "Theta Swole", "AER", "DPS"):
        st.session_state["rank_by"] = "WPA"
    rank_by = st.radio("Rank players by", ["WPA", "Green score", "Haul points", "Theta Swole", "AER", "DPS"], key="rank_by", help=HELP["rank_by"])
    risk_kappa = _slider("Theta Swole: risk appetite", "s_risk_kappa", -0.3, 0.3, 0.0, 0.05, help=HELP["risk_kappa"])
    dd_scale = "Sum" if True else st.radio("xG + xA + Creativity + Threat + Influence", ["Sum of the five 0–1 indices (as specified)",
                                                                           "Average of the five (keeps DPS on a smaller scale)"],
                                key="dd_scale5", help=HELP["dd_scale"])
    dd_mode = "sum" if str(dd_scale).startswith("Sum") else "avg"

with st.sidebar.expander(":material/smart_toy: AI assistant", expanded=False):
    AI_PROVIDERS = ["Google Gemini — free (free key)", "Ollama — free, runs on your Mac", "Anthropic Claude — paid"]
    ai_provider = st.selectbox("AI provider", AI_PROVIDERS, key="ai_provider", help=HELP["ai_provider"])
    ai_key, ai_host = "", ""
    if ai_provider.startswith("Google"):
        ai_key = st.text_input("Gemini API key (free)", type="password", key="ai_key_gemini", help=HELP["ai_key"]).strip() \
            or _secret("GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not st.session_state.get("ai_key_gemini") and ai_key:
            st.caption("Using the app's built-in Gemini key.")
        st.session_state.setdefault("ai_model_gemini", "gemini-flash-latest")
        ai_model = st.text_input("Model", key="ai_model_gemini", help=HELP["ai_model"])
    elif ai_provider.startswith("Ollama"):
        st.session_state.setdefault("ai_host", "http://localhost:11434")
        ai_host = st.text_input("Ollama address", key="ai_host", help=HELP["ai_host"])
        st.session_state.setdefault("ai_model_ollama", "llama3.2")
        ai_model = st.text_input("Model", key="ai_model_ollama", help=HELP["ai_model"])
    else:
        ai_key = st.text_input("Anthropic API key", type="password", key="ai_key_claude", help=HELP["ai_key"]).strip() \
            or _secret("ANTHROPIC_API_KEY")
        AI_MODELS = {"Claude Sonnet 5.5 (balanced)": "claude-sonnet-5-5", "Claude Opus 5.5 (most capable)": "claude-opus-5-5",
                     "Claude Haiku 4.5 (fastest)": "claude-haiku-4-5-20251001"}
        ai_model = AI_MODELS[st.selectbox("Model", list(AI_MODELS), key="ai_model", help=HELP["ai_model"])]
    ai_web = st.checkbox("Allow web search", value=True, key="ai_web", help=HELP["ai_web"])
    ai_source = st.checkbox("Let it read the program's source code", value=False, key="ai_source", help=HELP["ai_source"],
                                    disabled=ai_provider.startswith("Ollama"))
    ai_autoset = st.checkbox("Let the AI change settings", value=True, key="ai_autoset", help=HELP["ai_autoset"])
    ai_ready = bool(ai_host.strip()) if ai_provider.startswith("Ollama") else bool(str(ai_key).strip())

with st.sidebar.expander(":material/history: Tune on history", expanded=False):


    def _reset_defaults():
        for k_ in list(st.session_state.keys()):
            if str(k_).startswith("s_"):
                st.session_state.pop(k_, None)
        st.session_state["_tuned_applied"] = True


    st.session_state.setdefault("past_seasons", default_past_season())
    st.session_state.setdefault("use_past", False)
    st.session_state.setdefault("use_tuned", True)
    past_txt = st.text_input("Past seasons to learn from", key="past_seasons", help=HELP["past_seasons"])
    use_past = st.checkbox("Learn from past seasons", key="use_past", help=HELP["use_past"])
    tune_mode = st.radio("Tuning depth", ["Quick (a few minutes)", "Thorough (~20 minutes)"], key="tune_mode", help=HELP["tune_mode"])
    tune_clicked = st.button(":material/history: Tune on all history", help=HELP["tune_btn"])
    if st.button(":material/cloud_download: Test past-season download", help=HELP["past_test"]):
        rows_t = []
        for s_ in [x_.strip() for x_ in str(st.session_state.get("past_seasons", "")).split(",") if x_.strip()]:
            for path_ in ("teams.csv", "fixtures.csv", "gws/merged_gw.csv", "gws/gw1.csv"):
                url_ = f"{PAST_BASE}/{s_}/{path_}"
                try:
                    r_ = requests.get(url_, timeout=60, headers={"User-Agent": "fpl-draft-aer"})
                    info_ = f"{len(r_.content) / 1e6:.1f} MB" if r_.status_code == 200 else ""
                    rows_t.append({"Season": s_, "File": path_, "Result": "✅ found" if r_.status_code == 200 else f"❌ HTTP {r_.status_code}",
                                   "Size": info_})
                except Exception as e_:
                    rows_t.append({"Season": s_, "File": path_, "Result": f"❌ {type(e_).__name__}", "Size": ""})
            try:
                load_past_raw.clear()
                gw_t, fx_t, tm_t = load_past_raw(s_)
                pl_t, h_t, fxp_t, _ = past_season_frames(gw_t, fx_t, tm_t)
                rows_t.append({"Season": s_, "File": "→ rebuilt", "Result": f"✅ {len(pl_t)} players, {h_t['round'].nunique()} gameweeks", "Size": ""})
            except Exception as e_:
                rows_t.append({"Season": s_, "File": "→ rebuilt", "Result": f"❌ {type(e_).__name__}: {str(e_)[:200]}", "Size": ""})
        st.session_state["past_test"] = rows_t
    if st.session_state.get("past_test"):
        st.dataframe(pd.DataFrame(st.session_state["past_test"]), hide_index=True)
        st.caption("If every file shows ❌ 404, that season isn't in the dataset yet; try the season before (e.g. 2024-25).")
    st.checkbox("Start from saved tuned settings", key="use_tuned", help=HELP["use_tuned"])
    st.button(":material/restart_alt: Default settings", on_click=_reset_defaults, help=HELP["reset_defaults"])
    if _saved_tune:
        st.caption(f"Saved tune ({_saved_tune.get('when', '?')}): squared error {_saved_tune.get('before', float('nan')):.3f} → "
                           f"{_saved_tune.get('after', float('nan')):.3f} over {', '.join(_saved_tune.get('sets', [])) or 'history'}.")
    seasons = tuple(sorted({s_.strip() for s_ in str(past_txt).split(",") if re.fullmatch(r"\d{4}-\d{2}", s_.strip())}, reverse=True))

try:
    _dl = pd.Timestamp(load_deadlines().get(int(selected_gw))) if load_deadlines().get(int(selected_gw)) else None
    _tn = ""
    if my_entry_sel is not None and len(_ents_side):
        _tn = str(_ents_side.loc[_ents_side["id"].astype(int) == int(my_entry_sel), "entry_name"].iloc[0])
    _chip = ""
    if _dl is not None:
        _left = (_dl - pd.Timestamp.now(tz="UTC")).total_seconds()
        _when = _dl.tz_convert("Europe/London").strftime("%a %d %b %H:%M")
        _chip = (f"GW{selected_gw} deadline<br><b>{int(_left // 86400)}d {int(_left % 86400 // 3600)}h {int(_left % 3600 // 60)}m</b><br>{_when} UK"
                 if _left > 0 else f"GW{selected_gw}<br><b>deadline passed</b><br>{_when} UK")
    render_hero((_html.escape(_tn) + " · " if _tn else "") + f"Gameweek {selected_gw} · expected points, win chances, best lineups", _chip)
except Exception:
    pass
if not started:
    st.warning("No finished gameweeks yet — forecasts use position priors and fixtures only.")
ctx = build_context(hist, players, fx)
career_df = load_career(tuple(sorted(players.loc[players["status"] != "u", "id"].astype(int).tolist()))) if use_career else None
if use_career and (career_df is None or len(career_df) == 0):
    st.warning("Couldn't load career goals/xG from the API; finishing uses this season's numbers only.")
ctx["career"] = career_df
if fetch_odds_clicked:
    if not (odds_key.strip() or _secret("ODDS_API_KEY")):
        st.session_state["odds_err"] = "Enter your Odds API key first (free at the-odds-api.com)."
    else:
        try:
            with st.spinner("Fetching bookmaker odds…"):
                evs_, hdr_ = fetch_gw_odds(odds_key.strip() or _secret("ODDS_API_KEY"), odds_regions.strip() or "us", fx[fx["event"] == selected_gw], teams)
            st.session_state["odds_pack"] = {"gw": selected_gw, "events": evs_, "hdr": hdr_, "ts": time.time()}
            st.session_state["odds_err"] = "" if evs_ else "No bookmaker events matched this gameweek's fixtures (odds may not be listed yet)."
            if evs_:
                log_market(selected_gw, build_market(evs_, players, teams, mkt_margin, selected_gw)[1])
        except Exception as e_:
            st.session_state["odds_err"] = str(e_)
mkt, mkt_info, mkt_meta = None, pd.DataFrame(), {}
pack_ = st.session_state.get("odds_pack")
if mkt_on and pack_ and pack_["gw"] == selected_gw and pack_["events"]:
    mkt, mkt_info, mkt_meta = build_market(pack_["events"], players, teams, mkt_margin, selected_gw)
mkt_live = bool(mkt and mkt.get("team"))
if p["use_defcon"] and len(ctx["h"]) and ctx["h"]["defensive_contribution"].sum() == 0:
    st.warning("The API returned no defensive-contribution data; DEFCON points will be ~0. Untick DEFCON if your league doesn't score it.")

p_key = tuple(sorted(p.items()))
tune_req = st.session_state.pop("tune_request", None)
if tune_clicked or tune_req:
    quick_ = (tune_req == "quick") or str(tune_mode).startswith("Quick")
    past_ctxs = []
    for s_ in seasons:
        try:
            past_ctxs.append((s_, past_context(s_)))
        except Exception as e_:
            st.sidebar.warning(f"Couldn't load season {s_}: {e_}")
    sets_ = history_sets(ctx, players, code, past_ctxs, quick_)
    if sum(len(x_[4]) for x_ in sets_) < 3:
        st.sidebar.warning("Not enough finished gameweeks to tune on yet. Add a past season above.")
    else:
        bar_ = st.sidebar.progress(0.0, text="Tuning on all history…")
        t0_ = time.time()
        res_ = tune_on_history(sets_, dict(p), 1 if quick_ else 2, lambda f_, t_: bar_.progress(min(float(f_), 1.0), text=t_))
        hold_ = history_sets(ctx, players, code, past_ctxs, quick_, offset=1)
        if hold_:
            res_["holdout_before"], _ = history_error(hold_, dict(p))
            res_["holdout_after"], _ = history_error(hold_, {**p, **res_["params"]})
        res_.update(when=time.strftime("%Y-%m-%d %H:%M"), mode="quick" if quick_ else "thorough", minutes=round((time.time() - t0_) / 60, 1),
                    sets=[f"{x_[0]} ({len(x_[4])} GWs)" for x_ in sets_], gws_scored=int(sum(len(x_[4]) for x_ in sets_)))
        save_tuned(res_)
        st.session_state["last_tune"] = res_
        pend_ = st.session_state.get("god_pending") or {}
        pend_.update({f"s_{k_}": v_ for k_, v_ in res_["params"].items()})
        st.session_state["god_pending"] = pend_
        _rerun()

seasons_used = seasons if use_past else ()
if len(started) >= 3 or seasons_used:
    train_df, wf_all, feat_cols, failed_seasons = learn_from_history(ctx, players, code, p_key, tuple(started), seasons_used)
    for f_ in failed_seasons:
        s_, why_ = (f_ if isinstance(f_, tuple) else (f_, "unknown reason"))
        st.warning(f"Couldn't use past season {s_}, so the model is learning from this season only. Reason: {why_}  \n"
                   "Use **🔎 Test past-season download** under 🎯 Tune on history in the sidebar for a step-by-step check.")
else:
    train_df, wf_all, feat_cols = pd.DataFrame(), pd.DataFrame(), []

gb1, gb2 = st.columns([1, 3])
if gb1.button(":material/savings: Solve FPL", help=HELP["solve_btn"], type="primary", use_container_width=True):
    pend_ = {"god_on": True, "use_learning": True}
    if _saved_tune:
        pend_.update({f"s_{k_}": v_ for k_, v_ in (_saved_tune.get("params") or {}).items()})
    else:
        st.session_state["tune_request"] = "quick"   # no saved tune yet: run a quick one on the next pass
    st.session_state["god_pending"] = pend_
    _rerun()
PAGES = {"My Team": ":material/stadium:", "Pick Team": ":material/swap_horiz:", "Rankings": ":material/person_search:",
         "League": ":material/emoji_events:", "Players": ":material/monitoring:", "News": ":material/newspaper:", "Ask AI": ":material/smart_toy:",
         "More": ":material/more_horiz:"}
PAGE_NAMES = {"Rankings": "Transfers", "Players": "Stats"}       # labels as in the FPL app; internal names unchanged
MORE = {"Points calculator": ":material/calculate:", "Signal finder": ":material/query_stats:", "Match analyzer": ":material/analytics:", "Accuracy": ":material/fact_check:", "Matchups": ":material/sports_soccer:", "Live cams": ":material/videocam:", "Guide": ":material/menu_book:"}


def _seg(label, options, key, icons, names=None):
    names = names or {}
    st.session_state.setdefault(key, options[0])
    if st.session_state.get(key) not in options:
        st.session_state[key] = options[0]
    try:
        v_ = st.segmented_control(label, options, key=key, format_func=lambda o: f"{icons[o]} {names.get(o, o)}", label_visibility="collapsed")
    except Exception:
        v_ = st.radio(label, options, key=key, format_func=lambda o: f"{icons[o]} {names.get(o, o)}", horizontal=True, label_visibility="collapsed")
    return v_ or options[0]


page = _seg("Screen", list(PAGES), "page_pick", PAGES, PAGE_NAMES)
if page == "More":
    page = _seg("More", list(MORE), "more_pick", MORE)


class _QuietDummy:
    """Stands in for any Streamlit element on a screen that isn't open: draws nothing, accepts everything."""
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return _quiet_fn(name)

    def __iter__(self):
        return iter([])


_QUIET_WIDGETS = {"slider", "number_input", "select_slider", "multiselect", "selectbox", "radio", "segmented_control", "pills", "checkbox",
                  "toggle", "text_input", "text_area", "button", "download_button", "form_submit_button", "color_picker", "date_input"}


def _quiet_value(name, a, k):
    ss, key = st.session_state, k.get("key")
    if key is not None and key in ss:
        return ss[key]
    if name in ("slider", "number_input", "select_slider"):
        return k.get("value", a[3] if len(a) >= 4 else (a[1] if len(a) >= 2 else 0))
    if name == "multiselect":
        return list(k.get("default") or [])
    if name in ("selectbox", "radio", "segmented_control", "pills"):
        opts = list(k.get("options", a[1] if len(a) > 1 else []))
        ix = k.get("index", a[2] if len(a) > 2 else 0)
        return opts[ix] if opts and isinstance(ix, int) and ix < len(opts) else None
    if name in ("checkbox", "toggle"):
        return k.get("value", a[1] if len(a) > 1 else False)
    if name in ("text_input", "text_area"):
        return k.get("value", a[1] if len(a) > 1 else "")
    return False


def _quiet_fn(name):
    if name == "columns":
        return lambda spec, *a, **k: [_QuietDummy() for _ in range(spec if isinstance(spec, int) else len(spec))]
    if name == "tabs":
        return lambda labels, *a, **k: [_QuietDummy() for _ in labels]
    if name in _QUIET_WIDGETS:
        return lambda *a, **k: _quiet_value(name, a, k)
    return lambda *a, **k: _QuietDummy()


class _QuietST:
    _PASS = {"session_state", "cache_data", "cache_resource", "query_params", "secrets", "context", "column_config", "rerun",
             "experimental_rerun", "fragment"}

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        if name in self._PASS:
            return getattr(self._real, name)
        if name == "sidebar":
            return _QuietDummy()
        return _quiet_fn(name)


_REAL_ST = st
_QUIET = _QuietST(st)
_ST_STACK = []


class _Section:
    """Runs a block's calculations always, but only draws it when `show` is true (other screens need its numbers)."""
    def __init__(self, show):
        self.show = bool(show)

    def __enter__(self):
        global st
        _ST_STACK.append(st)
        st = _REAL_ST if self.show else _QUIET
        return self

    def __exit__(self, *a):
        global st
        st = _ST_STACK.pop()
        return False

# =========================================================
# COLOURS — hue = how good (red -> amber -> green), strength = how sure (vivid = high confidence, faded = low)
# =========================================================
_NEUTRAL = (238, 240, 243)
_VALUE_STOPS = [(0.0, (194, 59, 42)), (0.25, (238, 125, 42)), (0.5, (232, 185, 35)), (0.75, (124, 194, 74)), (1.0, (11, 110, 58))]
_CONF_STOPS = [(0.0, (238, 240, 243)), (0.5, (140, 170, 220)), (1.0, (31, 95, 191))]


def _interp(stops, x):
    x = float(np.clip(x, 0, 1))
    for (x0, c0), (x1, c1) in zip(stops[:-1], stops[1:]):
        if x <= x1:
            t = (x - x0) / max(x1 - x0, 1e-9)
            return tuple(c0[i] + (c1[i] - c0[i]) * t for i in range(3))
    return stops[-1][1]


def _mix(rgb, strength):
    s_ = float(np.clip(strength, 0, 1))
    return tuple(_NEUTRAL[i] + (rgb[i] - _NEUTRAL[i]) * s_ for i in range(3))


def _rgb(c):
    return "rgb(%d,%d,%d)" % tuple(int(round(v)) for v in c)


def _css(rgb):
    lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0
    return f"background-color: {_rgb(rgb)}; color: {'#111' if lum > 0.6 else 'white'};"


def value_colors(s, invert=False, conf=None, strength=0.8):
    """Continuous red->green by rank among the rows shown. With `conf` (0-1 per row), colour strength follows confidence:
    a vivid green is a confident good forecast, a washed-out green a good but shaky one."""
    v = pd.to_numeric(pd.Series(s).reset_index(drop=True), errors="coerce")
    n = int(v.notna().sum())
    r = (v.rank(method="average") - 1) / max(n - 1, 1)
    if invert:
        r = 1 - r
    out = []
    for i, x in enumerate(r):
        if pd.isna(x):
            out.append("")
            continue
        k = strength if conf is None else 0.2 + 0.8 * (0.5 if pd.isna(conf[i]) else float(np.clip(conf[i], 0, 1)))
        out.append(_css(_mix(_interp(_VALUE_STOPS, x), k)))
    return out


def pct_colors(s, invert=False):
    """Descriptive (non-forecast) columns: same hue scale at a fixed, softer strength."""
    return value_colors(s, invert, None, 0.65)


def conf_colors(s):
    """Confidence % column: pale grey = unsure, deep blue = sure (absolute scale, not relative to the rows shown)."""
    return ["" if pd.isna(x) else _css(_interp(_CONF_STOPS, float(x) / 100.0)) for x in pd.to_numeric(pd.Series(s), errors="coerce")]


def confidence_score(d, av):
    """0-1 confidence in a player's forecast: sample size (how much he has played), how certain his minutes are
    (near-certain starter or near-certain non-starter both count as certain), and how tight his floor-ceiling range is for his AER."""
    sample = pd.to_numeric(d["conf"], errors="coerce").fillna(0).clip(0, 1)
    m = (pd.to_numeric(d["p60_used"], errors="coerce").fillna(0) * av.reindex(d.index).fillna(1.0)).clip(0, 1)
    start_cert = (2 * m - 1).abs()
    width = (pd.to_numeric(d["Ceiling"], errors="coerce") - pd.to_numeric(d["Floor"], errors="coerce")) / d["AER1"].clip(lower=1.0)
    spread = (1 - width.rank(pct=True)).fillna(0.5) if width.notna().any() else pd.Series(0.5, index=d.index)
    return (0.35 * sample + 0.35 * start_cert + 0.30 * spread).clip(0, 1)


# =========================================================
# LINEUP OPTIMISER (starting XI + 4 bench) and pitch view
# =========================================================
FORMATIONS = [(d_, m_, f_) for d_ in range(3, 6) for m_ in range(2, 6) for f_ in range(1, 4) if d_ + m_ + f_ == 10]


def best_lineup(sq, col="AER1"):
    """Highest-scoring legal XI (1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD) from a squad; bench = spare GK first, then outfielders by AER
    (the order FPL uses for automatic substitutions)."""
    sq = sq.sort_values(col, ascending=False)
    gk = sq[sq["element_type"] == 1]
    by = {k: sq[sq["element_type"] == k] for k in (2, 3, 4)}
    best = None
    for d_, m_, f_ in FORMATIONS:
        if gk.empty or len(by[2]) < d_ or len(by[3]) < m_ or len(by[4]) < f_:
            continue
        xi = pd.concat([gk.head(1), by[2].head(d_), by[3].head(m_), by[4].head(f_)])
        tot = float(xi[col].sum())
        if best is None or tot > best[0] + 1e-9:
            best = (tot, f"{d_}-{m_}-{f_}", xi)
    if best is None:   # squad can't field a legal formation: best 11 with a keeper if there is one
        xi = pd.concat([gk.head(1), sq[sq["element_type"] != 1].head(11 - min(1, len(gk)))])
        best = (float(xi[col].sum()), "incomplete squad", xi)
    rest = sq.drop(best[2].index)
    bench = pd.concat([rest[rest["element_type"] == 1].head(1), rest[rest["element_type"] != 1], rest[rest["element_type"] == 1].iloc[1:]])
    return best[2], bench, best[1]


SQUAD_LIMITS = {1: 2, 2: 5, 3: 5, 4: 3}   # a legal 15: 2 GK, 5 DEF, 5 MID, 3 FWD


def dps_team(pool, col="DPS"):
    """DPS selected team: the legal XI with the highest total DPS from `pool`, plus a bench (second keeper, then the next-best
    outfielders by DPS) that keeps the squad within 2 GK / 5 DEF / 5 MID / 3 FWD. Returns (xi, bench, formation) or None."""
    pool = pool[(pool["n_fix"] > 0) & (pool["AER1"] > 0) & pool[col].notna()]
    if pool.empty:
        return None
    xi, rest, form_ = best_lineup(pool, col)
    if len(xi) < 11 or form_ == "incomplete squad":
        return None
    counts = xi["element_type"].value_counts().to_dict()
    picks = []
    for i_, r_ in rest[rest["element_type"] != 1].sort_values(col, ascending=False).iterrows():
        et_ = int(r_["element_type"])
        if counts.get(et_, 0) < SQUAD_LIMITS[et_]:
            picks.append(i_)
            counts[et_] = counts.get(et_, 0) + 1
        if len(picks) == 3:
            break
    bench = pd.concat([rest[rest["element_type"] == 1].sort_values(col, ascending=False).head(1), rest.loc[picks]])
    return xi, bench, form_


def _phi(x):
    return 0.5 * (1.0 + erf(float(x) / np.sqrt(2.0)))


def _fast_xi(et, val):
    """Indices of the best legal XI (numpy version of best_lineup, for speed)."""
    order = np.argsort(-val, kind="stable")
    by = {k: order[et[order] == k] for k in (1, 2, 3, 4)}
    best, idx_best = -np.inf, None
    for d_, m_, f_ in FORMATIONS:
        if len(by[1]) < 1 or len(by[2]) < d_ or len(by[3]) < m_ or len(by[4]) < f_:
            continue
        idx = np.concatenate([by[1][:1], by[2][:d_], by[3][:m_], by[4][:f_]])
        tot = float(val[idx].sum())
        if tot > best + 1e-12:
            best, idx_best = tot, idx
    return order[:11] if idx_best is None else idx_best


def team_mu_sd(et, val, sd, app):
    """Projected points (best XI + expected autosub cover) and standard deviation for a squad."""
    xi = _fast_xi(et, val)
    inxi = np.zeros(len(val), bool)
    inxi[xi] = True
    mu = float(val[xi].sum())
    miss = float((1 - app[xi[et[xi] != 1]]).sum())
    bo = np.where(~inxi & (et != 1))[0]
    bo = bo[np.argsort(-val[bo], kind="stable")][:3]
    cdf, term = 0.0, float(np.exp(-miss))
    for k_, j_ in enumerate(bo):
        cdf += term
        mu += max(0.0, 1.0 - cdf) * float(val[j_])
        term *= miss / (k_ + 1)
    gk_xi, bg = xi[et[xi] == 1], np.where(~inxi & (et == 1))[0]
    if len(gk_xi) and len(bg):
        mu += (1 - float(app[gk_xi[0]])) * float(val[bg].max())
    return mu, float(np.sqrt((sd[xi] ** 2).sum()))


@st.cache_data(show_spinner=False, max_entries=8)
def wpa_table(full, aer_by_gw, my_ids, opp_by_gw, cand_ids, decay=0.85):
    """Win Probability Added. For each candidate: add him, drop your least valuable player at his position (lowest discounted AER over
    the horizon), re-pick the best XI each gameweek, and compare the win probability against that gameweek's real opponent (normal
    approximation of the points margin). WPA = sum over gameweeks of decay^i x change in P(win); Pts added = the same for projected points.
    Later gameweeks' spreads are scaled from the first gameweek's floor-ceiling range."""
    gws = list(aer_by_gw)
    ids = full.index.to_numpy()
    pos = {int(i_): k_ for k_, i_ in enumerate(ids)}
    et = full["element_type"].to_numpy(int)
    app = full["appear"].fillna(1.0).clip(0, 1).to_numpy(float)
    a1 = aer_by_gw[gws[0]].reindex(full.index).fillna(0).clip(lower=0).to_numpy(float)
    spread = (pd.to_numeric(full["Ceiling"], errors="coerce") - pd.to_numeric(full["Floor"], errors="coerce")).to_numpy(float)
    sd1 = np.clip(np.where(np.isfinite(spread), spread, 2 * a1 + 2) / 2.56, 0.3, None)
    vals = {g: aer_by_gw[g].reindex(full.index).fillna(0).clip(lower=0).to_numpy(float) for g in gws}
    sds = {g: sd1 * np.sqrt((vals[g] + 1) / (a1 + 1)) for g in gws}
    w = {g: decay ** k_ for k_, g in enumerate(gws)}
    my = np.array([pos[int(i_)] for i_ in my_ids if int(i_) in pos], int)
    if len(my) < 11:
        return None

    def stats(ix, g):
        return team_mu_sd(et[ix], vals[g][ix], sds[g][ix], app[ix])

    base = {g: stats(my, g) for g in gws}
    opp = {}
    for g, o_ids in opp_by_gw.items():
        oi = np.array([pos[int(i_)] for i_ in o_ids if int(i_) in pos], int)
        if g in vals and len(oi) >= 11:
            opp[g] = stats(oi, g)
    pwin0 = {g: _phi((base[g][0] - opp[g][0]) / max(np.hypot(base[g][1], opp[g][1]), 1e-6)) for g in opp}
    hval = sum(w[g] * vals[g] for g in gws)
    my_set = set(my.tolist())
    rows = {}
    for c in cand_ids:
        ci = pos.get(int(c))
        if ci is None or ci in my_set:
            continue
        same = my[et[my] == et[ci]]
        if not len(same):
            continue
        drop = int(same[np.argmin(hval[same])])
        new = np.append(my[my != drop], ci)
        wpa_ = pts_ = 0.0
        for g in gws:
            mu_, sd_ = stats(new, g)
            pts_ += w[g] * (mu_ - base[g][0])
            if g in opp:
                wpa_ += w[g] * (_phi((mu_ - opp[g][0]) / max(np.hypot(sd_, opp[g][1]), 1e-6)) - pwin0[g])
        rows[int(c)] = (wpa_ if opp else np.nan, pts_, int(ids[drop]))
    out = pd.DataFrame.from_dict(rows, orient="index", columns=["WPA", "PtsAdded", "DropId"])
    return {"table": out, "pwin": pwin0, "base": base, "opp": opp}


def team_projection(xi, bench, col="AER1"):
    """(XI points, expected automatic-substitution points, standard deviation). Autosubs: the number of outfield starters who don't
    play is treated as Poisson, and bench player k counts when at least k are missing; the bench keeper covers the starting keeper."""
    base = float(xi[col].sum())
    app = xi["appear"].fillna(1.0).clip(0, 1)
    miss = float((1 - app[xi["element_type"] != 1]).sum())
    sub, cdf, term = 0.0, 0.0, float(np.exp(-miss))
    for k_, (_, r_) in enumerate(bench[bench["element_type"] != 1].iterrows()):
        cdf += term
        sub += max(0.0, 1.0 - cdf) * float(r_[col])
        term *= miss / (k_ + 1)
    bgk, sgk = bench[bench["element_type"] == 1], app[xi["element_type"] == 1]
    if len(bgk) and len(sgk):
        sub += float(1 - sgk.iloc[0]) * float(bgk[col].iloc[0])
    spread = (pd.to_numeric(xi["Ceiling"], errors="coerce") - pd.to_numeric(xi["Floor"], errors="coerce")).fillna(2 * xi[col] + 2)
    return base, sub, float(np.sqrt(((spread / 2.56) ** 2).sum()))


# club kits: (main colour, second colour, pattern) — drawn as a fallback under FPL's official shirt image
CLUB_KIT = {"ARS": ("#EF0107", "#FFFFFF", "sleeves"), "AVL": ("#670E36", "#95BFE5", "sleeves"), "BOU": ("#DA291C", "#111111", "stripes"),
            "BRE": ("#E30613", "#FFFFFF", "stripes"), "BHA": ("#0057B8", "#FFFFFF", "stripes"), "BUR": ("#6C1D45", "#99D6EA", "sleeves"),
            "CHE": ("#034694", "#034694", "solid"), "CRY": ("#1B458F", "#C4122E", "stripes"), "EVE": ("#003399", "#003399", "solid"),
            "FUL": ("#FFFFFF", "#111111", "solid"), "LEE": ("#FFFFFF", "#1D428A", "solid"), "LIV": ("#C8102E", "#C8102E", "solid"),
            "MCI": ("#6CABDD", "#6CABDD", "solid"), "MUN": ("#DA291C", "#DA291C", "solid"), "NEW": ("#111111", "#FFFFFF", "stripes"),
            "NFO": ("#DD0000", "#DD0000", "solid"), "SUN": ("#EB172B", "#FFFFFF", "stripes"), "TOT": ("#FFFFFF", "#132257", "solid"),
            "WHU": ("#7A263A", "#1BB1E7", "sleeves"), "WOL": ("#FDB913", "#231F20", "solid"), "LEI": ("#003090", "#003090", "solid"),
            "IPS": ("#3A64A3", "#3A64A3", "solid"), "SOU": ("#D71920", "#FFFFFF", "stripes"), "SHU": ("#EE2737", "#FFFFFF", "stripes"),
            "LUT": ("#F78F1E", "#002D62", "solid"), "COV": ("#59CBE8", "#59CBE8", "solid"), "MID": ("#E11B22", "#E11B22", "solid"),
            "NOR": ("#FFF200", "#00A650", "sleeves"), "WBA": ("#122F67", "#FFFFFF", "stripes"), "WAT": ("#FBEE23", "#ED2127", "solid"),
            "HUL": ("#F5A12D", "#111111", "stripes"), "STK": ("#E03A3E", "#FFFFFF", "stripes"), "BIR": ("#0000FF", "#0000FF", "solid"),
            "WRE": ("#D71920", "#D71920", "solid")}
_SHIRT_PATH = "M30 12 L42 7 Q50 14 58 7 L70 12 L92 26 L83 42 L73 37 L73 93 L27 93 L27 37 L17 42 L8 26 Z"


def jersey_svg(team_code, gk=False):
    main, second, style = ("#2ECC71", "#145A32", "solid") if gk else CLUB_KIT.get(str(team_code), ("#B8B8C8", "#B8B8C8", "solid"))
    pid = f"k{team_code}{'g' if gk else ''}"
    defs, fill = "", main
    if style == "stripes":
        defs = (f"<defs><pattern id='{pid}' width='16' height='100' patternUnits='userSpaceOnUse'><rect width='16' height='100' fill='{main}'/>"
                f"<rect x='8' width='8' height='100' fill='{second}'/></pattern></defs>")
        fill = f"url(#{pid})"
    sleeves = (f"<path d='M8 26 L30 12 L32 30 L17 42 Z M92 26 L70 12 L68 30 L83 42 Z' fill='{second}'/>" if style == "sleeves" else "")
    return (f"<svg viewBox='0 0 100 100' class='pc-kit' aria-hidden='true'>{defs}<path d='{_SHIRT_PATH}' fill='{fill}' stroke='rgba(0,0,0,.35)' "
            f"stroke-width='2' stroke-linejoin='round'/>{sleeves}<path d='M42 7 Q50 16 58 7' fill='none' stroke='rgba(0,0,0,.35)' stroke-width='2'/></svg>")


def shirt_html(r):
    """FPL's official shirt image layered over a drawn kit in club colours (the drawing shows if the image can't load)."""
    gk = int(r.get("element_type", 0) or 0) == 1
    code_ = r.get("team_shirt")
    img_ = (f"<img src='https://fantasy.premierleague.com/dist/img/shirts/standard/shirt_{int(code_)}{'_1' if gk else ''}-110.webp' alt='' loading='lazy'>"
            if pd.notna(code_) else "")
    return f"<div class='pc-shirt'>{jersey_svg(r.get('team_code', ''), gk)}{img_}</div>"


_PITCH_CSS = """<style>
.fpl-wrap{max-width:660px;margin:0 auto 6px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.fpl-pitch{position:relative;overflow:hidden;border-radius:14px;padding:14px 6px 16px;
 background:radial-gradient(ellipse at 50% 50%,rgba(255,255,255,.07),rgba(0,0,0,.18) 85%),repeating-linear-gradient(180deg,#3a9a4f 0 7.14%,#348f48 7.14% 14.28%);
 box-shadow:inset 0 0 0 3px rgba(255,255,255,.75),0 8px 22px rgba(0,0,0,.22)}
.fpl-mk{position:absolute;border:2px solid rgba(255,255,255,.55);pointer-events:none}
.fpl-mk.half{left:0;right:0;top:50%;height:0;border-width:2px 0 0}
.fpl-mk.circle{left:50%;top:50%;width:30%;aspect-ratio:1;transform:translate(-50%,-50%);border-radius:50%}
.fpl-mk.box-t{left:21%;right:21%;top:-2px;height:14%;border-top:0}.fpl-mk.box-b{left:21%;right:21%;bottom:-2px;height:14%;border-bottom:0}
.fpl-row{position:relative;z-index:1;display:flex;justify-content:center;gap:clamp(4px,1.4vw,12px);margin:10px 0;flex-wrap:nowrap}
.pc{width:clamp(62px,16vw,104px);text-align:center;position:relative}
.pc-shirt{position:relative;width:62%;margin:0 auto -6px;aspect-ratio:1}
.pc-shirt .pc-kit{position:absolute;inset:8%;width:84%;height:84%;filter:drop-shadow(0 3px 3px rgba(0,0,0,.3))}
.pc-shirt img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;filter:drop-shadow(0 3px 3px rgba(0,0,0,.3))}
.pc-name{position:relative;background:#fff;color:#37003C;font-weight:800;font-size:clamp(9.5px,2.5vw,12px);padding:3px 3px 2px;border-radius:6px 6px 0 0;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis;box-shadow:0 2px 6px rgba(0,0,0,.25)}
.pc-opp{background:#ecebf3;color:#37003C;font-weight:700;font-size:clamp(8.5px,2.2vw,10.5px);padding:2px 2px 3px;white-space:nowrap;overflow:hidden}
.pc-stat{display:flex;border-radius:0 0 6px 6px;overflow:hidden;box-shadow:0 2px 6px rgba(0,0,0,.25)}
.pc-stat div{flex:1;padding:3px 0 4px;font-size:clamp(8.5px,2.2vw,10.5px);font-weight:800;line-height:1.2}
.pc-stat small{display:block;font-size:.72em;font-weight:600;opacity:.85;line-height:1.1;margin-top:1px}
.pc{margin-bottom:2px}
.pc-flag{position:absolute;top:0;right:4%;z-index:2;background:#FFBF00;color:#37003C;font-size:9px;font-weight:800;border-radius:8px;padding:0 5px;line-height:15px}
.pc-flag.out{background:#E90052;color:#fff}
.fpl-bench{margin-top:10px;border-radius:14px;padding:8px 6px 10px;background:linear-gradient(180deg,#e9f7ef,#d6efe0)}
.fpl-bench-title{color:#37003C;font-weight:800;font-size:11px;text-transform:uppercase;letter-spacing:.06em;text-align:center}
.fpl-slot{color:#37003C;font-size:10px;font-weight:800;text-align:center;margin-bottom:2px;opacity:.75}
.fpl-legend{font-size:11px;opacity:.75;margin:6px 4px 0;text-align:center}
/* big player card (like the FPL app's player profile) */
.bpc{border-radius:18px;overflow:hidden;background:#fff;color:#37003C;box-shadow:0 6px 18px rgba(0,0,0,.18);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;max-width:420px}
.bpc-top{position:relative;padding:12px 12px 0;background:linear-gradient(135deg,#04F5FF 0%,#00FF87 45%,#963CFF 100%);display:flex;align-items:flex-end;gap:10px;min-height:120px}
.bpc-top .pc-shirt{width:110px;margin:0}
.bpc-tags{display:flex;flex-direction:column;gap:4px;margin-left:auto;padding-bottom:10px}
.bpc-tag{background:#37003C;color:#fff;border-radius:6px;padding:2px 8px;text-align:center;font-size:10px;line-height:1.2}
.bpc-tag b{display:block;font-size:13px}
.bpc-name{text-align:center;font-weight:900;font-size:20px;padding:8px 6px 2px}
.bpc-sub{text-align:center;font-size:12px;opacity:.8;padding-bottom:8px}
.bpc-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:2px;background:#fff;padding:0 2px 2px}
.bpc-cell{background:#ecebf3;text-align:center;padding:5px 2px;font-size:10.5px;line-height:1.25}
.bpc-cell b{display:block;font-size:15px}
.bpc-dark .bpc-cell{background:#37003C;color:#fff}
.bpc-unit{font-size:9px;opacity:.75}
</style>"""
FDR_BG = {1: "#00FF87", 2: "#7CE3A6", 3: "#E7E7E7", 4: "#FF5A79", 5: "#80072D"}
FDR_FG = {1: "#37003C", 2: "#37003C", 3: "#37003C", 4: "#ffffff", 5: "#ffffff"}


def _flag_html(r):
    stt = str(r.get("status", "a"))
    if stt not in ("d", "i", "s", "u"):
        return ""
    ch = r.get("chance_of_playing_next_round", np.nan)
    return f"<div class='pc-flag{'' if stt == 'd' else ' out'}'>{(str(int(ch)) + '%') if pd.notna(ch) else ('?' if stt == 'd' else 'out')}</div>"


def _card(r, col="AER1", show_dps=False):
    """Pitch card: shirt, name, fixture, then expected points (pts) and haul chance (%). Points colour: red low -> green high, faded = less sure."""
    pts = float(r[col]) if pd.notna(r.get(col, np.nan)) else 0.0
    x = (pts - 1.0) / 6.0
    cf = float(r.get("Confidence", 0.5)) if pd.notna(r.get("Confidence", np.nan)) else 0.5
    rgb = _mix(_interp(_VALUE_STOPS, x), 0.25 + 0.75 * cf)
    fg = "#111" if (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0 > 0.6 else "white"
    ep = r.get("EliteP", np.nan)
    second = (f"<div style='background:#37003C;color:#fff'>{float(r['DPS']):.1f}<small>DPS</small></div>" if show_dps and pd.notna(r.get("DPS", np.nan))
              else f"<div style='background:#37003C;color:#fff'>{(f'{float(ep) * 100:.0f}%' if pd.notna(ep) else '–')}<small>haul</small></div>")
    unit = "pts" if col == "AER1" else ""
    return (f"<div class='pc'>{_flag_html(r)}{shirt_html(r)}<div class='pc-name'>{_html.escape(str(r.get('web_name', '?')))}</div>"
            f"<div class='pc-opp'>{_html.escape(str(r.get('Opponent', '') or ''))}</div>"
            f"<div class='pc-stat'><div style='background:{_rgb(rgb)};color:{fg}'>{pts:.1f}<small>{unit or col}</small></div>{second}</div></div>")


def pitch_html(xi, bench, col="AER1", show_dps=False):
    rows = []
    for et in (1, 2, 3, 4):   # keeper at the top, forwards at the bottom, as in the FPL app
        grp = xi[xi["element_type"] == et].sort_values(col, ascending=False)
        rows.append("<div class='fpl-row'>" + "".join(_card(r, col, show_dps) for _, r in grp.iterrows()) + "</div>")
    labels, n_ = [], 0
    for _, r in bench.iterrows():
        if int(r["element_type"]) == 1:
            labels.append("GK")
        else:
            n_ += 1
            labels.append(str(n_))
    bench_cards = "".join(f"<div><div class='fpl-slot'>{lab}</div>{_card(r, col, show_dps)}</div>" for lab, (_, r) in zip(labels, bench.iterrows()))
    marks = "".join(f"<div class='fpl-mk {c_}'></div>" for c_ in ("half", "circle", "box-t", "box-b"))
    return (_PITCH_CSS + "<div class='fpl-wrap'><div class='fpl-pitch'>" + marks + "".join(rows) + "</div>"
            + "<div class='fpl-bench'><div class='fpl-bench-title'>Substitutes</div><div class='fpl-row'>" + bench_cards + "</div></div>"
            + "<div class='fpl-legend'>Left number = expected points this gameweek (pts). Right = chance of a haul (a top-20% week for his position).</div></div>")


def big_card_html(pid, fd, h, fx_all, gw, code_map):
    """Full player card: shirt, position and ownership tags, last three gameweeks, next three fixtures (coloured by difficulty), season stats,
    and this gameweek's forecast with units."""
    r = fd.loc[pid]
    tid = int(r["team"])
    hp = h[(h["id"] == pid) & (h["round"] < gw)].sort_values("round").tail(3)
    last = []
    for _, g_ in hp.iterrows():
        o_ = code_map.get(int(g_["hm_opp"]), "") if pd.notna(g_.get("hm_opp")) else "DGW"
        v_ = "H" if g_.get("venue") == 1 else ("A" if g_.get("venue") == 0 else "")
        last.append(f"<div class='bpc-cell'>GW{int(g_['round'])} · {o_}{f'({v_})' if v_ else ''}<b>{int(g_['total_points'])}<span class='bpc-unit'> pts</span></b></div>")
    while len(last) < 3:
        last.insert(0, "<div class='bpc-cell'>–<b>–</b></div>")
    nxt = []
    for g2 in range(gw, gw + 3):
        f_ = fx_all[(fx_all["event"] == g2) & ((fx_all["team_h"] == tid) | (fx_all["team_a"] == tid))]
        if f_.empty:
            nxt.append(f"<div class='bpc-cell'>GW{g2}<b>blank</b></div>")
            continue
        f0 = f_.iloc[0]
        home_ = int(f0["team_h"]) == tid
        opp_ = code_map.get(int(f0["team_a"] if home_ else f0["team_h"]), "?")
        d_ = int(f0.get("team_h_difficulty" if home_ else "team_a_difficulty", 3) or 3)
        nxt.append(f"<div class='bpc-cell' style='background:{FDR_BG.get(d_, '#E7E7E7')};color:{FDR_FG.get(d_, '#37003C')}'>GW{g2}<b>{opp_}({'H' if home_ else 'A'})</b></div>")
    hs = h[(h["id"] == pid) & (h["round"] < gw)]
    season = [("FPL points", f"{int(hs['total_points'].sum())}", ""), ("Goals", f"{int(hs['goals_scored'].sum())}", ""),
              ("Assists", f"{int(hs['assists'].sum())}", ""), ("Minutes", f"{int(hs['minutes'].sum())}", ""),
              ("Season xG", f"{hs['expected_goals'].sum():.2f}", ""), ("Season xA", f"{hs['expected_assists'].sum():.2f}", "")]
    start_ = min(float(r.get("p60_used", np.nan)), float(r.get("appear", 1.0))) if pd.notna(r.get("p60_used", np.nan)) else np.nan
    ep_ = r.get("EliteP", np.nan)
    model = [(f"AER GW{gw}", f"{float(r['AER1']):.1f}", "pts"), ("xG this GW", f"{float(r.get('xG', 0)):.2f}", "goals"),
             ("xA this GW", f"{float(r.get('xA', 0)):.2f}", "assists"),
             ("Starts (60+ min)", f"{start_ * 100:.0f}" if pd.notna(start_) else "–", "%"),
             ("Haul chance", f"{float(ep_) * 100:.0f}" if pd.notna(ep_) else "–", "%"),
             ("Points if he hauls", f"{float(r['HaulSize']):.1f}" if pd.notna(r.get("HaulSize", np.nan)) else "–", "pts")
]
    cell = lambda t_: f"<div class='bpc-cell'>{t_[0]}<b>{t_[1]}<span class='bpc-unit'> {t_[2]}</span></b></div>"
    own_ = r.get("selected_by_percent", np.nan)
    tags = (f"<div class='bpc-tag'>Position<b>{r.get('position', '')}</b></div><div class='bpc-tag'>Team<b>{r.get('team_code', '')}</b></div>"
            + (f"<div class='bpc-tag'>Selected by<b>{float(own_):.1f}%</b></div>" if pd.notna(pd.to_numeric(own_, errors='coerce')) else ""))
    return (_PITCH_CSS + f"<div class='bpc'><div class='bpc-top'>{shirt_html(r)}<div class='bpc-tags'>{tags}</div></div>"
            f"<div class='bpc-name'>{_html.escape(str(r.get('first_name', ''))[:1] + '. ' if r.get('first_name') else '')}{_html.escape(str(r.get('web_name', '')))}</div>"
            f"<div class='bpc-sub'>{_html.escape(str(r.get('team_full_name', '')))} · {_html.escape(str(r.get('Opponent', '') or ''))} · {r.get('Status', '')}</div>"
            f"<div class='bpc-grid'>{''.join(last)}{''.join(nxt)}</div>"
            f"<div class='bpc-grid bpc-dark'>{''.join(cell(t_) for t_ in season)}</div>"
            f"<div class='bpc-grid'>{''.join(cell(t_) for t_ in model)}</div></div>")


# =========================================================
# MATCH PREDICTIONS — score probabilities from the same team model AER uses (plus bookmaker odds when fetched)
# =========================================================
DC_RHO = -0.08      # Dixon-Coles low-score correction: real matches end 0-0 / 1-1 a little more often than independent Poisson says


def score_matrix(lh, la, kmax=10, rho=DC_RHO):
    """P(home scores i, away scores j): independent Poisson goals (Maher 1982) with the Dixon & Coles (1997) correction for 0-0, 1-0, 0-1, 1-1."""
    k = np.arange(kmax + 1)
    fact = np.array([factorial(int(x)) for x in k], float)
    ph, pa = np.exp(-lh) * lh ** k / fact, np.exp(-la) * la ** k / fact
    M = np.outer(ph, pa)
    M[0, 0] *= max(1 - lh * la * rho, 0)
    M[0, 1] *= max(1 + lh * rho, 0)
    M[1, 0] *= max(1 + la * rho, 0)
    M[1, 1] *= max(1 - rho, 0)
    return M / M.sum()


def fit_poisson_ratings(long, cutoff, xw, hl=12.0, ridge=3.0):
    """Every team's attack and defence rated at once from all matches before `cutoff`, adjusting for who each team played: a Poisson
    regression  log(goals) = base + home advantage + own attack + opponent's leakiness  (Maher 1982; Dixon & Coles 1997), with older
    matches fading (half-life `hl` gameweeks) and ridge shrinkage toward average (`ridge`). Goals are blended with expected goals (weight xw).
    Returns (base, home, attack[21], leak[21]) or None if there are too few matches."""
    d = long[long["round"] < cutoff].dropna(subset=["gf", "opp"])
    if d["fid"].nunique() < 15:
        return None
    y = np.where(d["xg"].notna(), xw * d["xg"].fillna(0) + (1 - xw) * d["gf"], d["gf"]).astype(float)
    t, o = d["team"].to_numpy(int), d["opp"].to_numpy(int)
    n = len(d)
    X = np.zeros((n, 42))
    X[:, 0] = 1.0
    X[:, 1] = d["home"].to_numpy(float)
    X[np.arange(n), 1 + t] = 1.0            # attack of the scoring team (columns 2-21)
    X[np.arange(n), 21 + o] = 1.0           # leakiness of the conceding team (columns 22-41)
    w = 0.5 ** ((cutoff - 1 - d["round"].to_numpy(float)) / hl)
    R = np.diag([0.0, 0.0] + [ridge] * 40)
    th = np.zeros(42)
    th[0] = np.log(max(float(np.average(y, weights=w)), 0.2))
    for _ in range(40):
        lam = np.exp(np.clip(X @ th, -5, 3))
        step = np.linalg.solve(X.T @ (X * (w * lam)[:, None]) + R, X.T @ (w * (y - lam)) - R @ th)
        th += step
        if np.abs(step).max() < 1e-7:
            break
    att, leak = np.zeros(21), np.zeros(21)
    att[1:21], leak[1:21] = th[2:22], th[22:42]
    return float(th[0]), float(th[1]), att, leak


def _ratings_lambdas(rt, h, a):
    base, home, att, leak = rt
    return float(np.exp(base + home + att[h] + leak[a])), float(np.exp(base + att[a] + leak[h]))


@st.cache_data(show_spinner=False, max_entries=4)
def best_rating_settings(long, fin, xw):
    """How fast old matches should fade, how hard to pull toward average and how much to trust expected goals over goals, chosen by how
    well each setting predicted this season's finished matches from earlier ones (log loss of the actual result)."""
    best, scores = (12.0, 3.0, xw), {}
    gws = [g for g in sorted(int(x) for x in fin["event"].unique()) if g >= 4]
    for xw_ in sorted({0.0, 0.5, float(xw)}):
        for hl in (6.0, 12.0, 30.0):
            for ridge in (1.0, 3.0, 8.0):
                ll, n = 0.0, 0
                for g in gws:
                    rt = fit_poisson_ratings(long, g, xw_, hl, ridge)
                    if rt is None:
                        continue
                    for _, f in fin[fin["event"] == g].iterrows():
                        M = score_matrix(*_ratings_lambdas(rt, int(f["team_h"]), int(f["team_a"])))
                        hs, as_ = int(f["team_h_score"]), int(f["team_a_score"])
                        pr = np.tril(M, -1).sum() if hs > as_ else (np.trace(M) if hs == as_ else np.triu(M, 1).sum())
                        ll -= np.log(max(pr, 1e-6))
                        n += 1
                if n:
                    scores[(hl, ridge, xw_)] = ll / n
    if scores:
        best = min(scores, key=scores.get)
    return best


def _predicted_score(M):
    """The likeliest scoreline within the likeliest result (as a pundit would call it), plus the single likeliest exact score."""
    res = [np.tril(M, -1).sum(), np.trace(M), np.triu(M, 1).sum()]
    which = int(np.argmax(res))
    cells = [(M[i, j], i, j) for i in range(6) for j in range(6)
             if (which == 0 and i > j) or (which == 1 and i == j) or (which == 2 and i < j)]
    q, i, j = max(cells)
    top = max((M[x, y], x, y) for x in range(6) for y in range(6))
    return f"{i}–{j}", float(q), f"{top[1]}–{top[2]}", float(top[0])


def player_shares(h, players, cutoff, n_rounds=6):
    """Each player's share of his team's recent attacking output (expected goal involvements) and, for defenders and keepers, his share
    of the team's defender + keeper minutes, from the `n_rounds` gameweeks before `cutoff`."""
    att = attack_share(h, players, cutoff, n_rounds)
    hh = h[(h["round"] < cutoff) & (h["round"] >= cutoff - n_rounds) & (h["element_type"] <= 2)]
    mins = hh.groupby("id")["minutes"].sum()
    team_ = players.set_index("id")["team"]
    tot = mins.groupby(team_.reindex(mins.index).to_numpy()).transform("sum")
    dfs = (mins / tot.replace(0, np.nan)).reindex(att.index).fillna(0.0)
    return pd.DataFrame({"att": att, "def": dfs})


def team_absence(weights, shares, players):
    """{team: (attacking share missing, defensive share missing)} from {player id: weight} (1 = definitely out, 0.5 = 50/50)."""
    out = {}
    tm = players.set_index("id")["team"]
    for pid_, w_ in (weights or {}).items():
        if pid_ in shares.index and pid_ in tm.index and w_ > 0:
            t_ = int(tm[pid_])
            a_, d_ = out.get(t_, (0.0, 0.0))
            out[t_] = (a_ + w_ * float(shares.at[pid_, "att"]), d_ + w_ * float(shares.at[pid_, "def"]))
    return {k_: (round(min(a_, 0.9), 4), round(min(d_, 0.9), 4)) for k_, (a_, d_) in out.items()}


def _absence_factor(miss, own, opp, coefs):
    """Multiplier on a side's expected goals: missing attackers cut his own team's goals, missing defenders/keeper on the other side add to them."""
    a_loss, d_gain = coefs
    ma = (miss or {}).get(own, (0.0, 0.0))[0]
    md = (miss or {}).get(opp, (0.0, 0.0))[1]
    return float(np.clip((1 - a_loss * ma) * (1 + d_gain * md), 0.5, 1.6))


@st.cache_data(show_spinner=False, max_entries=4)
def absence_effect_fit(long, fin, h, players, p):
    """How much missing players really move match outcomes, measured on this season's finished matches. A 'missing' player is a regular
    (averaged 60+ minutes over his previous three gameweeks) who played 0 minutes in that match: the same information as a confirmed team
    sheet. Each past match is re-predicted from earlier matches only, with and without the adjustment, for a grid of strengths; the
    strength with the lowest log loss is used. Returns ((attack loss, defence gain), table of results, matches tested)."""
    xw = float(p.get("xg_weight", 0.75))
    hl, ridge, xw = best_rating_settings(long, fin, xw)
    cases = []
    for g in sorted(int(x) for x in fin["event"].unique()):
        if g < 4:
            continue
        rt = fit_poisson_ratings(long, g, xw, hl, ridge)
        if rt is None:
            continue
        sh = player_shares(h, players, g)
        prev = h[(h["round"] < g) & (h["round"] >= g - 3)]
        reg = prev.groupby("id")["min_pm"].agg(["mean", "size"])
        reg = set(reg[(reg["mean"] >= 60) & (reg["size"] >= 2)].index)
        now = h[h["round"] == g].set_index("id")["minutes"]
        missing = {int(i_): 1.0 for i_ in reg if float(now.get(i_, 0.0)) == 0.0}
        miss = team_absence(missing, sh, players)
        for _, f in fin[fin["event"] == g].iterrows():
            ht, at = int(f["team_h"]), int(f["team_a"])
            lh, la = _ratings_lambdas(rt, ht, at)
            hs, as_ = int(f["team_h_score"]), int(f["team_a_score"])
            cases.append((lh, la, ht, at, miss, 0 if hs > as_ else (1 if hs == as_ else 2)))
    if len(cases) < 20:
        return (0.0, 0.0), pd.DataFrame(), len(cases)
    rows = []
    for a_loss in (0.0, 0.25, 0.5, 0.75, 1.0):
        for d_gain in (0.0, 0.15, 0.3, 0.5):
            ll = br = hit = 0.0
            for lh, la, ht, at, miss, out in cases:
                M = score_matrix(lh * _absence_factor(miss, ht, at, (a_loss, d_gain)), la * _absence_factor(miss, at, ht, (a_loss, d_gain)))
                pv = np.array([np.tril(M, -1).sum(), np.trace(M), np.triu(M, 1).sum()])
                ll -= np.log(max(pv[out], 1e-6))
                br += float(((pv - np.eye(3)[out]) ** 2).sum())
                hit += float(np.argmax(pv) == out)
            n_ = len(cases)
            rows.append({"Attack loss": a_loss, "Defence gain": d_gain, "Log loss": ll / n_, "Brier": br / n_, "Right result %": 100 * hit / n_})
    tab = pd.DataFrame(rows)
    best = tab.loc[tab["Log loss"].idxmin()]
    return (float(best["Attack loss"]), float(best["Defence gain"])), tab, len(cases)


@st.cache_data(show_spinner=False, max_entries=16)
def fixture_predictions(long, fx_g, cutoff, p, mkt_team=None, mkt_w=0.0, fin=None, miss=None, coefs=(0.0, 0.0)):
    """Expected goals for each side from the season-long Poisson team ratings (falls back to the AER team model very early in the
    season), blended with bookmaker-implied goals when odds were fetched."""
    xw = float(p.get("xg_weight", 0.75))
    hl, ridge, xw = best_rating_settings(long, fin, xw) if fin is not None and len(fin) else (12.0, 3.0, xw)
    rt = fit_poisson_ratings(long, cutoff, xw, hl, ridge)
    team = fit_team_model(long, cutoff, p) if rt is None else None
    rows = []
    for _, f in fx_g.iterrows():
        h, a = int(f["team_h"]), int(f["team_a"])
        if rt is not None:
            lh, la = _ratings_lambdas(rt, h, a)
            src_ = "season team ratings"
        else:
            lh = team["att_h"][h] * team["def_a"][a] / team["base_h"]
            la = team["att_a"][a] * team["def_h"][h] / team["base_a"]
            src_ = "early-season team model"
        if mkt_team and (f["id"], h) in mkt_team and mkt_w > 0:
            mh, ma = mkt_team[(f["id"], h)]
            if pd.notna(mh) and pd.notna(ma):
                lh, la, src_ = mkt_w * mh + (1 - mkt_w) * lh, mkt_w * ma + (1 - mkt_w) * la, src_ + " + bookmaker odds"
        lh0, la0 = lh, la
        if miss and any(coefs):          # team news: who's missing on each side
            lh, la = lh * _absence_factor(miss, h, a, coefs), la * _absence_factor(miss, a, h, coefs)
        lh, la = float(np.clip(lh, 0.1, 5)), float(np.clip(la, 0.1, 5))
        M = score_matrix(lh, la)
        sc, psc, top, ptop = _predicted_score(M)
        flat = sorted(((M[i, j], i, j) for i in range(6) for j in range(6)), reverse=True)
        rows.append({"fid": f["id"], "home": h, "away": a, "xg_h": lh, "xg_a": la,
                     "p_home": float(np.tril(M, -1).sum()), "p_draw": float(np.trace(M)), "p_away": float(np.triu(M, 1).sum()),
                     "score": sc, "p_score": psc, "top_score": top, "p_top": ptop,
                     "next_scores": ", ".join(f"{i}–{j} ({q * 100:.0f}%)" for q, i, j in flat[:3]),
                     "cs_h": float(M[:, 0].sum()), "cs_a": float(M[0, :].sum()), "btts": float(M[1:, 1:].sum()),
                     "over25": float(sum(M[i, j] for i in range(M.shape[0]) for j in range(M.shape[1]) if i + j >= 3)),
                     "source": src_, "hl": hl, "ridge": ridge, "xw": xw, "dxg_h": lh - float(np.clip(lh0, 0.1, 5)),
                     "dxg_a": la - float(np.clip(la0, 0.1, 5))})
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False, max_entries=4)
def prediction_backtest(long, fin, p):
    """Every finished fixture predicted only from the matches before its gameweek: how often the likeliest result happened, how much
    probability went on what actually happened, and how a 'no information' guess (this season's home/draw/away rates so far) compares.
    (The fade/shrink setting is chosen on these same matches, so treat the figures as slightly optimistic.)"""
    rows = []
    for g in sorted(int(x) for x in fin["event"].unique()):
        if g < 4:
            continue
        fg = fin[fin["event"] == g]
        prev = fin[fin["event"] < g]
        pr = fixture_predictions(long, fg[["id", "team_h", "team_a"]], g, p, fin=fin)
        if pr.empty or prev.empty:
            continue
        hw = float((prev["team_h_score"] > prev["team_a_score"]).mean())
        dr = float((prev["team_h_score"] == prev["team_a_score"]).mean())
        base = np.array([hw, dr, 1 - hw - dr])
        for _, r_ in pr.merge(fg[["id", "team_h_score", "team_a_score"]], left_on="fid", right_on="id").iterrows():
            hs, as_ = int(r_["team_h_score"]), int(r_["team_a_score"])
            out = 0 if hs > as_ else (1 if hs == as_ else 2)
            pv = np.array([r_["p_home"], r_["p_draw"], r_["p_away"]])
            y = np.eye(3)[out]
            rows.append({"GW": g, "hit": int(np.argmax(pv) == out), "base_hit": int(np.argmax(base) == out), "p_actual": float(pv[out]),
                         "brier": float(((pv - y) ** 2).sum()), "base_brier": float(((base - y) ** 2).sum()),
                         "exact": int(r_["score"] == f"{hs}–{as_}")})
    return pd.DataFrame(rows)



# =========================================================
# DPS — "process points": expected FPL points from stats that repeat, with no goals/assists/bonus actually scored
# =========================================================
DPS_STATS = {"xg": "expected_goals", "xa": "expected_assists", "dc": "defensive_contribution", "sv": "saves", "bonus": "bonus",
             "yc": "yellow_cards"}


@st.cache_data(show_spinner=False, max_entries=48)
def dps_rates(h, cutoff, hl=6.0):
    """Each player's per-90 rate for every process stat, from games of 30+ minutes before `cutoff` (recent games count most), pulled toward
    his position's norm in proportion to how unreliable that stat is. Reliability is measured from the data: the share of game-to-game
    variation that is real difference between players (empirical Bayes; Efron & Morris 1975). A stat that barely repeats is pulled hard;
    one that repeats strongly is trusted. Returns (rates by player, single-game reliability by stat and position)."""
    cols = list(DPS_STATS.values())
    d = h[(h["round"] < cutoff) & (h["minutes"] >= 30)][["id", "element_type", "round", "minutes"] + cols].copy()
    if d.empty:
        return pd.DataFrame(), {}
    d["w"] = 0.5 ** ((cutoff - 1 - d["round"]) / hl) * d["minutes"] / 90.0
    out = pd.DataFrame(index=pd.Index(sorted(d["id"].unique()), name="id"))
    out["element_type"] = d.groupby("id")["element_type"].first()
    rel = {}
    for k_, c_ in DPS_STATS.items():
        d["r"] = d[c_] * 90.0 / d["minutes"]
        d["wr"] = d["w"] * d["r"]
        g_ = d.groupby("id")
        W, W2 = g_["w"].sum(), (d["w"] ** 2).groupby(d["id"]).sum()
        m_ = g_["wr"].sum() / W
        n_eff = (W ** 2 / W2).clip(lower=1)
        d["dev2"] = d["w"] * (d["r"] - d["id"].map(m_)) ** 2
        shr = pd.Series(np.nan, index=out.index)
        for et_ in (1, 2, 3, 4):
            ids_ = out.index[out["element_type"] == et_]
            if not len(ids_):
                continue
            mu = float(np.average(m_[ids_], weights=W[ids_]))
            sig_w = float(d.loc[d["id"].isin(ids_), "dev2"].sum() / max(d.loc[d["id"].isin(ids_), "w"].sum(), 1e-9))
            var_b = float(np.average((m_[ids_] - mu) ** 2, weights=W[ids_]))
            tau2 = max(var_b - float(np.average(sig_w / n_eff[ids_], weights=W[ids_])), 1e-6 + 0.01 * var_b)
            r_i = tau2 / (tau2 + sig_w / n_eff[ids_])
            shr[ids_] = mu + r_i * (m_[ids_] - mu)
            rel[(k_, et_)] = tau2 / (tau2 + sig_w) if sig_w > 0 else 1.0
        out[k_] = shr
    return out, rel


def dps_points(rows, rates, rt, fx_g):
    """Process points for each row (index = player id; needs element_type, team, xMins, p_app, p60_used, n_fix, optional av).
    Uses the season team ratings for the opponent (attack boost / clean-sheet chance); no outcome stats."""
    r = rows.copy()
    rt_ = rates.reindex(r.index)
    pos_mean = rates.groupby("element_type")[list(DPS_STATS)].mean() if len(rates) else pd.DataFrame()
    for k_ in DPS_STATS:
        fill_ = r["element_type"].map(pos_mean[k_]) if k_ in pos_mean else 0.0
        r[k_] = rt_[k_].fillna(fill_).fillna(0.0) if k_ in rt_ else fill_
    fix_ = {}
    for _, f in fx_g.iterrows():
        fix_.setdefault(int(f["team_h"]), (int(f["team_a"]), 1))
        fix_.setdefault(int(f["team_a"]), (int(f["team_h"]), 0))
    et = r["element_type"].astype(int).to_numpy()
    tm = r["team"].astype(int).to_numpy()
    att_f, lam_ag, opp_att = np.ones(len(r)), np.full(len(r), 1.35), np.ones(len(r))
    has_fx = np.array([t_ in fix_ for t_ in tm])
    if rt is not None:
        base, home, att, leak = rt
        for i_, t_ in enumerate(tm):
            if t_ in fix_:
                o_, hm_ = fix_[t_]
                att_f[i_] = np.exp(leak[o_] + home * (hm_ - 0.5))
                opp_att[i_] = np.exp(att[o_] + home * ((1 - hm_) - 0.5))
                lam_ag[i_] = np.exp(base + att[o_] + leak[t_] + (home if hm_ == 0 else 0.0))
    av = r["av"].to_numpy(float) if "av" in r.columns else np.ones(len(r))
    m = pd.to_numeric(r["xMins"], errors="coerce").fillna(0).to_numpy(float) / 90.0
    p60 = np.clip(pd.to_numeric(r["p60_used"], errors="coerce").fillna(0).to_numpy(float) * av, 0, 1)
    nf = np.maximum(pd.to_numeric(r["n_fix"], errors="coerce").fillna(1).to_numpy(float), 1)
    goals = r["xg"].to_numpy(float) * m * att_f * GOAL_PTS[et]
    assists = r["xa"].to_numpy(float) * m * att_f * 3
    cs = p60 * np.exp(-lam_ag) * CS_PTS[et] * nf
    defcon = 2 * p60 * nb_tail(r["dc"].to_numpy(float) * 0.95, DEFCON_LIMIT[et]) * nf
    saves = np.where(et == 1, exp_floor_div(r["sv"].to_numpy(float) * m * opp_att, 3), 0.0)
    lg = np.clip(lam_ag * m, 1e-6, None)
    pmf = np.exp(-lg[:, None]) * lg[:, None] ** POISSON_K / POISSON_FACT
    gc = np.where(et <= 2, -(pmf * (POISSON_K // 2)).sum(1), 0.0)
    bonus = r["bonus"].to_numpy(float) * m
    cards = -r["yc"].to_numpy(float) * m
    app = pd.to_numeric(r["p_app"], errors="coerce").fillna(0).to_numpy(float)
    total = np.where(has_fx, app + goals + assists + cs + defcon + saves + gc + bonus + cards, 0.0)
    return pd.DataFrame({"DPS": total, "dps_goals": goals, "dps_assists": assists, "dps_cs": cs, "dps_defcon": defcon,
                         "dps_bonus": bonus}, index=r.index)


@st.cache_data(show_spinner=False, max_entries=4)
def dps_validation(train, h, long, fin, fx_all, players, p):
    """Process points for every past player-gameweek, built only from what was known before that gameweek, scored against what each
    player actually got, alongside the model's structural forecast and plain recent form."""
    if train is None or train.empty:
        return pd.DataFrame()
    t = train[train["season"] == "this season"] if "season" in train.columns else train[train["GW"] >= 1]
    xw = float(p.get("xg_weight", 0.75))
    hl, ridge, xw = best_rating_settings(long, fin, xw) if len(fin) else (12.0, 3.0, xw)
    tm_ = players.set_index("id")["team"]
    parts = []
    for g in sorted(int(x) for x in t["GW"].unique()):
        if g < 3:
            continue
        rows_g = t[t["GW"] == g].drop_duplicates("id").set_index("id")
        rows_g = rows_g.assign(team=tm_.reindex(rows_g.index)).dropna(subset=["team"])
        rates_g, _ = dps_rates(h, g)
        if not len(rates_g):
            continue
        rt_g = fit_poisson_ratings(long, g, xw, hl, ridge)
        dp_ = dps_points(rows_g, rates_g, rt_g, fx_all[fx_all["event"] == g])
        parts.append(pd.DataFrame({"GW": g, "id": rows_g.index, "dps": dp_["DPS"].to_numpy(), "xPts": rows_g["xPts"].to_numpy(),
                                   "form": rows_g["form"].to_numpy(), "actual": rows_g["actual"].to_numpy(),
                                   "element_type": rows_g["element_type"].to_numpy()}))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _fit_stats(pred, act):
    pred, act = np.asarray(pred, float), np.asarray(act, float)
    ok = np.isfinite(pred) & np.isfinite(act)
    pred, act = pred[ok], act[ok]
    var = ((act - act.mean()) ** 2).sum()
    return {"R²": 1 - ((act - pred) ** 2).sum() / var if var else np.nan, "Rank corr": rank_corr(pd.Series(pred), pd.Series(act)),
            "Avg miss (pts)": float(np.abs(act - pred).mean()), "Bias (pts)": float((pred - act).mean())}


METRIC_UNITS = {
    "AER": ("AER (pts)", "Expected FPL points over the gameweeks summed. Unit: points."),
    "Base": ("Base (pts)", "Structural forecast before the learned correction. Unit: points."),
    "Floor": ("Floor (pts)", "A bad week: 10% of similar forecasts scored this or less. Unit: points."),
    "Ceiling": ("Ceiling (pts)", "A great week: 10% of similar forecasts scored this or more. Unit: points."),
    "Theta Swole": ("Theta (pts vs repl.)", "Expected points above a replacement-level player at the same position. Unit: points."),
    "Upgrade vs my XI": ("Upgrade (pts)", "Points gained over your weakest starter at his position. Unit: points."),
    "Pts added": ("Pts added (pts)", "Projected points your team gains by claiming him. Unit: points."),
    "WPA %": ("WPA (% pts)", "Percentage points added to your chance of winning your next matchups."),
    "Elite %": ("Haul chance (%)", "Chance of a top-20% week for his position (points well above an ordinary player in his minutes)."),
    "Haul pts": ("Haul pts (pts)", "Expected points from big weeks: haul chance × the points he usually scores in a haul week. Unit: points."),
    "Haul size": ("Haul size (pts)", "Points he usually scores in a top-20% week (his own, pulled toward his position's norm). Unit: points."),
    "DPS": ("DPS (pts)", "Process points: expected FPL points from repeatable stats only (expected goals and assists, minutes, clean-sheet chance, "
            "DEFCON, saves, bonus rate), each pulled toward the position norm by how unreliable it is, and adjusted for the opponent. Unit: points."),
    "AER − DPS": ("AER − DPS (pts)", "Above 0 = his forecast is running ahead of his underlying process (likely to cool); below 0 = due. Unit: points."),
    "Green score": ("Green score (0–100)", "Strength across seven areas (expected points, upside, minutes, attack, defence, bonus, value to "
                    "your team), each counted once: 0 = nothing above the median, 100 = the very top in every area."),
    "Green cells": ("Green areas", "How many of the seven areas he is in the top quarter for (forwards are scored on six: no defence)."),
    "xBonus": ("xBonus (pts)", "Expected bonus points from simulating each match's bonus race. Unit: points."),
    "P(3 bonus) %": ("3-bonus chance (%)", "Chance of the maximum 3 bonus points."),
    "BPS ceil": ("BPS ceiling (/90)", "A great day's bonus-points-system score per 90 minutes."),
    "Mins": ("Mins (exp.)", "Expected minutes."),
    "xG": ("xG (goals)", "Expected goals this gameweek."), "xA": ("xA (assists)", "Expected assists this gameweek."),
    "CS %": ("Clean sheet (%)", "Chance his team keeps a clean sheet."), "Confidence %": ("Confidence (%)", "How sure the forecast is."),
}

# =========================================================
# RANKINGS
# =========================================================
with _Section(page == "Rankings"):
    pool = players.copy()
    pool["label"] = pool["web_name"] + " (" + pool["team_code"] + ")"
    RSUB = {"Ranked players": ":material/format_list_numbered:", "BPS": ":material/military_tech:", "DPS team": ":material/bolt:",
            "Team stability": ":material/tune:", "Playing time": ":material/schedule:"}
    rank_sub = _seg("Rankings view", list(RSUB), "rank_sub", RSUB)
    _on = page == "Rankings"
    with _Section(_on and rank_sub == "Playing time"):
        st.subheader(f"Playing-time overrides · GW{selected_gw}")
        st.caption("Scales a player's chance of playing: 0.5 = expect heavy rotation, 1.2 = safer than history suggests.")
        for pid_f, val_f in st.session_state.pop("focus_pending", []):            # changes made by the AI, applied before the widgets exist
            lab_f = pool.loc[pool["id"] == pid_f, "label"]
            if len(lab_f):
                cur_f = list(st.session_state.get("focus_pick", []))
                if lab_f.iloc[0] not in cur_f:
                    cur_f.append(lab_f.iloc[0])
                st.session_state["focus_pick"] = cur_f
                st.session_state[f"mf_{pid_f}_{selected_gw}"] = float(val_f)
        picked = st.multiselect("Adjust players:", sorted(pool["label"].unique()), key="focus_pick", help=HELP["focus_pick"])
        focus = {}
        for lab in picked:
            pid = int(pool.loc[pool["label"] == lab, "id"].iloc[0])
            focus[pid] = st.slider(f"Focus — {lab}", 0.0, 1.5, 1.0, 0.05, key=f"mf_{pid}_{selected_gw}", help=HELP["focus"])

    with _Section(_on and rank_sub == "Team stability"):
        # --- Team stability editor (per fixture) ---
        stab_now = team_stability(ctx, players, selected_gw, p)
        des_now = team_desperation(ctx["long"], selected_gw)
        recipe = st.session_state.setdefault("recipe_z", {})
        st.subheader(f"Team stability · GW{selected_gw}")
        st.caption("-1 = chaos (legal trouble, sanctions, manager turmoil, injury crisis, collapsing form) · 0 = normal · "
                   "+1 = driven, in form, outperforming, settled XI, big club. Auto scores come from results, xG, starters' form, "
                   "lineup continuity, club size and discipline. **Off-field news can't be read from the API — that's what your edits are for.** "
                   "Drag any slider — every xG, xA, clean-sheet %, Goals Against and AER value recalculates instantly. Sliders start at the auto score. "
                   "'Stakes' next to each team is computed from the table (title / top-5 / survival leverage) and is not edited here.")
        # --- per-team stability ingredients ---
        team_names = {int(i): n for i, n in zip(teams["id"], teams["name"])}
        ids_sorted = sorted(range(1, 21), key=lambda t: team_names.get(t, ""))
        if st.session_state.get("_recipe_gw") != selected_gw:      # stability is gameweek-specific: start each gameweek from the measured values
            recipe.clear()
            st.session_state["_recipe_gw"] = selected_gw

        def _reset_recipe(tid):
            st.session_state.setdefault("recipe_z", {}).pop(tid, None)
            for k_ in [k for k in list(st.session_state.keys()) if str(k).startswith(f"rz_{tid}_")]:
                st.session_state.pop(k_, None)

        if "recipe_team" not in st.session_state:
            st.session_state["recipe_team"] = ids_sorted[0]
        if selected_team != "All Teams" and st.session_state.get("_last_sel_team") != selected_team:
            match_ = [t for t in ids_sorted if team_names[t] == selected_team]
            if match_:
                st.session_state["recipe_team"] = match_[0]
        st.session_state["_last_sel_team"] = selected_team
        with st.expander(":material/science: Stability recipe — set each ingredient for a team", expanded=False):
            tsel = st.selectbox("Team", ids_sorted, format_func=lambda t: team_names.get(t, str(t)), key="recipe_team", help=HELP["recipe_team"])
            st.caption("Each slider is how strong that ingredient is for this team, in standard deviations versus the league (0 = league average). "
                       "It starts at the measured value. Moving a slider up can only raise the team's stability; moving it down can only lower it.")
            zmeas = stab_now.loc[tsel, STAB_KEYS].astype(float)
            zmap = {k_: float(np.clip(round(zmeas[k_], 2), -2.5, 2.5)) for k_ in STAB_KEYS}
            sc_ = st.columns(4)
            zs = {}
            for i_, k_ in enumerate(STAB_KEYS):
                zs[k_] = sc_[i_ % 4].slider(STAB_LABELS[k_], -2.5, 2.5, zmap[k_], 0.05, key=f"rz_{tsel}_{k_}_{zmap[k_]:.2f}",
                                            help=f"{INGREDIENT_HELP[k_]} Measured for this team: {zmeas[k_]:+.2f} (0 = league average).")
            ov_ = {k_: zs[k_] for k_ in STAB_KEYS if abs(zs[k_] - zmap[k_]) > 1e-9}
            if ov_:
                recipe[tsel] = ov_
            else:
                recipe.pop(tsel, None)
            wv_ = np.array([STAB_DEFAULT_W[k_] for k_ in STAB_KEYS], float)
            wv_ = wv_ / wv_.sum()
            zu_ = np.array([zs[k_] for k_ in STAB_KEYS], float)
            score_ = float(np.tanh(1.3 * (zu_ * wv_).sum()))
            base_score_ = float(np.tanh(1.3 * (np.array([zmap[k_] for k_ in STAB_KEYS]) * wv_).sum()))
            m1, m2 = st.columns([1, 3])
            m1.metric(f"{team_names[tsel]} stability", f"{score_:+.2f}", f"{score_ - base_score_:+.2f} vs measured")
            m2.dataframe(pd.DataFrame({"Ingredient": [STAB_LABELS[k_] for k_ in STAB_KEYS], "Measured": [zmap[k_] for k_ in STAB_KEYS],
                                       "Yours": zu_.round(2), "Weight": wv_.round(2), "Contribution": (zu_ * wv_).round(2)}),
                         use_container_width=True, hide_index=True)
            st.button("Reset this team", on_click=_reset_recipe, args=(tsel,), help=HELP["recipe_reset"])
        auto_map = recipe_scores(stab_now, recipe).to_dict()

        bl, bm = st.columns([1, 4])
        if bl.button(":material/restart_alt: Reset to auto", help=HELP["stab_reset"]):
            st.session_state["stab_reset"] = st.session_state.get("stab_reset", 0) + 1
        reset_n = st.session_state.get("stab_reset", 0)
        gws_shown = list(range(selected_gw, min(38, selected_gw + horizon - 1) + 1))
        boxes = st.tabs([f"GW{g}" for g in gws_shown]) if len(gws_shown) > 1 else [st.container()]
        stab_pairs = {}
        for g, box in zip(gws_shown, boxes):
            fxg = fx[fx["event"] == g]
            with box:
                if fxg.empty:
                    st.info("No fixtures this gameweek.")
                    continue
                widths = [1.3, 3, 3, 1.3, 1.6]
                for col_, title_ in zip(st.columns(widths), ["Home", "Home stability", "Away stability", "Away", "Home edge"]):
                    col_.markdown(f"**{title_}**")
                for fid, th, ta in zip(fxg["id"], fxg["team_h"], fxg["team_a"]):
                    ah, aa = round(float(auto_map[th]), 2), round(float(auto_map[ta]), 2)
                    ov_s = st.session_state.get("stab_override", {})                 # values set by the AI, applied before the sliders exist
                    for t_s, a_s in ((th, ah), (ta, aa)):
                        if f"{g}_{int(t_s)}" in ov_s:
                            st.session_state[f"stab_{g}_{fid}_{t_s}_{reset_n}_{a_s:.2f}"] = float(ov_s.pop(f"{g}_{int(t_s)}"))
                    c1, c2, c3, c4, c5 = st.columns(widths)
                    c1.markdown(f"**{code[th]}**  \nauto {ah:+.2f} · stakes {des_now[int(th)]:+.2f}")
                    vh = c2.slider(f"{code[th]} stability", -1.0, 1.0, ah, 0.05, format="%.2f",
                                   key=f"stab_{g}_{fid}_{th}_{reset_n}_{ah:.2f}", label_visibility="collapsed", help=HELP["stab_fixture"])
                    va = c3.slider(f"{code[ta]} stability", -1.0, 1.0, aa, 0.05, format="%.2f",
                                   key=f"stab_{g}_{fid}_{ta}_{reset_n}_{aa:.2f}", label_visibility="collapsed", help=HELP["stab_fixture"])
                    c4.markdown(f"**{code[ta]}**  \nauto {aa:+.2f} · stakes {des_now[int(ta)]:+.2f}")
                    edited = abs(vh - ah) > 1e-9 or abs(va - aa) > 1e-9
                    c5.markdown(f"**{vh - va:+.2f}**" + ("  ✏️ edited" if edited else ""))
                    stab_pairs[(fid, th)], stab_pairs[(fid, ta)] = float(vh), float(va)
        with st.expander("Why these auto scores? (component breakdown, z-scores across the league)"):
            comp = stab_now.copy()
            comp["auto"] = recipe_scores(stab_now, recipe)
            comp["Stakes"] = des_now[1:21]
            comp.index = comp.index.map(code)
            comp = comp.rename(columns={**STAB_LABELS, "auto": "Stability (auto)", "matches": "Matches"})
            comp = comp.sort_values("Stability (auto)", ascending=False).round(2)
            sty_c = comp.style
            for c_ in list(STAB_LABELS.values()) + ["Stability (auto)", "Stakes"]:
                sty_c = sty_c.apply(lambda s: pct_colors(s), subset=[c_], axis=0)
            st.dataframe(sty_c, use_container_width=True)

    with _Section(_on and rank_sub == "Ranked players"):
        # --- learning setup (only GWs before the target are ever used) ---
        train_use = train_df[train_df["GW"] < selected_gw] if len(train_df) else train_df
        wf_use = wf_all[wf_all["GW"] < selected_gw] if len(wf_all) else wf_all
        model_ok = (use_learning and len(train_use) >= GW_TABLE_MIN_ROWS and train_use["GW"].nunique() >= 2)
        if model_ok:
            learned, c_use = fit_final_model(train_use, tuple(feat_cols), selected_gw, p_key, tuple(started))
        else:
            learned, c_use = None, (fit_stab_coefs(train_use) if len(train_use) else COEF_PRIOR.copy())
        alpha = best_alpha(wf_use) if model_ok else 0.0
        coefs_live = np.asarray(c_use)[:6] * stability_impact
        hcoefs_live = np.asarray(c_use)[6:9] * hmpr_impact
        dcoefs_live = np.asarray(c_use)[9:11] * desperation_impact
        god_model = (np.zeros(len(COND_COLS)), 1.0, 0.0)
        god_live = bool(god_on and model_ok and len(wf_use))
        if god_live:
            god_model = fit_god(wf_use.assign(aer=np.clip(wf_use["xPts"] + alpha * wf_use["adj"], 0, None)))
        if len(wf_use) >= 300:
            pred_b = god_expanding(wf_use, alpha, god_conv) if god_live else np.clip(wf_use["xPts"] + alpha * wf_use["adj"], 0, None)
            bands = make_bands(pred_b, wf_use["actual"])
        elif len(train_use) >= 300:
            bands = make_bands(train_use["xPts"], train_use["actual"])
        else:
            bands = None

        sfit_live = sfit_array(players) if selected_gw == next_gw else None
        full_df = None
        total_aer = total_base = opp_chain = first_T = None
        first_av = first_aer = first_Tf = None
        aer_by_gw = {}   # per-gameweek AER (WPA + Player trends)
        for i in range(max(horizon, wpa_h)):
            g = selected_gw + i
            if g > 38:
                break
            fxg = fx[fx["event"] == g]
            av_full = availability(players, g, next_gw)
            mins_ml_g = minutes_predict(minutes_model_at(ctx["seq"], selected_gw, (len(ctx["seq"]), int(ctx["seq"]["round"].max()) if len(ctx["seq"]) else 0)),
                                        ctx["h"], players, fxg, g) if len(ctx["seq"]) else None
            T_feat = feature_table(ctx, players, selected_gw, g, fxg, p, focus, code, mins_ml=mins_ml_g, mins_w=p["mins_ml_w"])  # auto stability: learner input
            T = feature_table(ctx, players, selected_gw, g, fxg, p, focus, code, stab_pairs, coefs_live, leader_boost,
                              (COEF_PRIOR * stability_impact)[:6], hcoefs_live,
                              mkt=(mkt if (i == 0 and mkt_live) else None), mkt_w=mkt_w, mins_ml=mins_ml_g, mins_w=p["mins_ml_w"],
                              sfit=(sfit_live if i == 0 else None), dprob=((mkt or {}).get("dprob") if (i == 0 and mkt_live) else None),
                              dcoefs=dcoefs_live, avail=(av_full if use_squad_avail else None))
            base_i = T["xPts"].copy()
            aer_i = base_i.copy()
            if learned is not None:
                adj = learned.predict(T_feat[list(feat_cols)].fillna(0), T_feat["element_type"])
                aer_i = (base_i + alpha * pd.Series(adj, index=T.index)).clip(lower=0)
            if god_live:
                aer_i = pd.Series(apply_god(aer_i.to_numpy(float), T[COND_COLS].fillna(0).to_numpy(float), god_model, god_conv), index=T.index)
            if i == 0 and ep_map.get(selected_gw) and ep_w > 0:
                ep_s_ = pd.to_numeric(players.set_index("id")[ep_map[selected_gw]], errors="coerce").reindex(T.index)
                aer_i = ((1 - ep_w) * aer_i + ep_w * ep_s_).fillna(aer_i)
            av = av_full.reindex(T.index)
            aer_i, base_i = aer_i * av, base_i * av
            aer_by_gw[g] = aer_i.copy()
            if i >= horizon:        # extra gameweeks are only for WPA
                continue
            if i == 0:
                first_T, first_av, first_aer, first_Tf = T.copy(), av, aer_i.copy(), T_feat.copy()
                total_aer, total_base, opp_chain = aer_i.copy(), base_i.copy(), T["Opponent"].copy()
            else:
                total_aer, total_base = total_aer + aer_i, total_base + base_i
                opp_chain = opp_chain + " → " + T["Opponent"]

        cc, cd_ = np.asarray(c_use) * stability_impact, COEF_PRIOR * stability_impact
        st.caption(
            f"**Your edits:** moving a team's stability by +1.0 changes its goals {cd_[0]*100:+.0f}%, assists {cd_[2]*100:+.0f}%, clean sheets {cd_[4]*100:+.0f}%, "
            f"and its opponent's goals/clean sheets {cd_[1]*100:+.0f}% / {cd_[5]*100:+.0f}% (captains and veterans react more). "
            f"**Auto score:** the data-fitted extra effect beyond the team model is goals {cc[0]*100:+.0f}%, assists {cc[2]*100:+.0f}%, "
            f"clean sheets {cc[4]*100:+.0f}% per +1.0 — near 0 means results and xG already captured it. The learner also sees the auto score.")

        ch_ = np.asarray(c_use)[6:9] * hmpr_impact
        st.caption(f"**Historical match rating (HMPR):** for games a player started and played {p['hm_min']}+ minutes, it measures points above their own average "
                   f"against (1) this opponent, (2) opponents using the same usual system, (3) same-difficulty teams, then carries "
                   f"{ch_[0]*100:.0f}% / {ch_[1]*100:.0f}% / {ch_[2]*100:.0f}% of each into AER (fitted from past gameweeks, shrunk toward a prior). "
                   "Only this season's matches exist in the API, so 'same opponent' is usually 0–1 games.")
        st.caption(f"**Desperation (stakes):** per +0.1 of stakes above league average, a team's goals and assists move {dcoefs_live[0] * 10:+.1f}% and "
                   f"clean-sheet odds in its matches move {dcoefs_live[1] * 10:+.1f}% (fitted from past gameweeks, shrunk toward a prior). "
                   + ("**Squad availability** is on: flagged-out attackers' output is partly redistributed to teammates."
                      if use_squad_avail else "Squad availability redistribution is off."))
        ceil_s = floor_s = None
        q_models = fit_final_quantiles(train_use, tuple(feat_cols), selected_gw, p_key, tuple(started)) if model_ok else None
        if q_models is not None:
            okq = wf_use[wf_use["q90"].notna()] if ("q90" in wf_use.columns and len(wf_use)) else wf_use.iloc[0:0]
            d90 = float(np.quantile(okq["actual"] - okq["q90"], 0.9)) if len(okq) >= 300 else 0.0
            d10 = float(np.quantile(okq["actual"] - okq["q10"], 0.1)) if len(okq) >= 300 else 0.0
            Xq = first_Tf[list(feat_cols)].fillna(0)
            hi_, lo_ = q_models[1].predict(Xq, first_Tf["element_type"]) + d90, q_models[0].predict(Xq, first_Tf["element_type"]) + d10
            av1, a1 = first_av.to_numpy(float), first_aer.to_numpy(float)
            ceil_s = pd.Series(np.maximum(hi_ * av1, a1 + 0.25), index=first_aer.index)
            floor_s = pd.Series(np.where(av1 <= 0.9, 0.0, np.clip(np.minimum(lo_, a1), 0, None)), index=first_aer.index)
        if god_on and not god_live:
            st.caption("⚡ Solve FPL is on but waiting for enough finished gameweeks (and the learned correction) to fit.")
        if god_live:
            b_, sl_, ic_ = god_model
            top_ = ", ".join(f"{COND_LABELS[c_]} {b_[i_] * god_conv * 100:+.0f}%" for i_, c_ in enumerate(COND_COLS) if b_[i_] > 0.005) \
                or "none beyond what AER already contains"
            st.caption(f"⚡ **Solve FPL ON.** Conditions multiplier (fitted on past out-of-sample forecasts, per +1 standard deviation): {top_}. "
                       f"Spread calibration slope {sl_:.2f} ({'averages were too compressed, so they are stretched' if sl_ > 1.02 else 'averages were too spread out, so they are tightened' if sl_ < 0.98 else 'spread was already right'}). "
                       f"Conviction ×{god_conv:.1f}. The Accuracy tab shows whether it actually beats plain AER out of sample.")
        q10, q90, p5, p8 = lookup_bands(bands, first_aer.to_numpy())
        T = first_T
        meta = players.set_index("id")
        df = meta.drop(columns=[c for c in T.columns if c in meta.columns]).join(T)  # model columns win on name clashes (form, element_type)
        df["AER"], df["Base"], df["Opp"] = total_aer, total_base, opp_chain
        df["Floor"], df["Ceiling"] = pd.Series(q10, index=first_aer.index), pd.Series(q90, index=first_aer.index)
        df["P5"], df["P8"] = pd.Series(p5, index=first_aer.index), pd.Series(p8, index=first_aer.index)
        if ceil_s is not None:
            df["Floor"], df["Ceiling"] = floor_s, ceil_s
        for c in ["xMins", "xG", "xA", "p_app", "p_goal", "p_ast", "p_cs", "p_dc", "p_oth", "hm_adj"]:
            df[c] = df[c] * first_av
        df["Focus"] = pd.Series(df.index, index=df.index).map(focus).fillna(1.0)
        df["Status"] = df["status"].map({"a": "Available", "i": "Injured", "s": "Suspended",
                                         "u": "Unavailable", "d": "Doubtful"}).fillna("Active")
        df["AER1"] = first_aer.reindex(df.index)                                        # first target gameweek only
        df["appear"] = (df["p_app"] - df["p60_used"] * first_av.reindex(df.index)).clip(0, 1)   # P(plays any minutes)
        df["Confidence"] = confidence_score(df, first_av)
        # DPS for every player (index percentiles among all players with minutes, so values don't shift with filters)
        ep_col_ = ep_map.get(selected_gw)

        def _old_add_dps(frame, ref):
            """DPS = (DPS formula + FPL xP) / 2 (formula alone where FPL publishes no xP).
            Percentile indices are taken within position among `ref`."""
            idx5 = [ict_pct(ref[c], ref["position"]).reindex(frame.index).fillna(0).to_numpy(float)
                    for c in ("xG", "xA", "creativity_6", "threat_6", "influence_6")]
            ce = frame["Ceiling"].fillna(2.0 * frame["AER1"] + 2.0)
            dd = dps_formula(frame["AER1"].to_numpy(float), frame["form"].to_numpy(float), *idx5, ce.to_numpy(float), dd_mode)
            fpl_xp = (pd.to_numeric(frame[ep_col_], errors="coerce") if (ep_col_ and ep_col_ in frame.columns)
                      else pd.Series(np.nan, index=frame.index))
            dps_final = np.where(fpl_xp.notna(), (dd + fpl_xp.to_numpy(float)) / 2.0, dd)
            return frame.assign(DPS=dps_final, DPS_raw=dd, FPL_xP=fpl_xp)

        # DPS = process points (see dps_points): expected points from repeatable stats only, opponent-adjusted with the season team ratings
        rates_now, dps_rel = dps_rates(ctx["h"], selected_gw)
        _hl_r, _rg_r, _xw_r = best_rating_settings(ctx["long"], ctx["fin"], float(p["xg_weight"])) if len(ctx["fin"]) else (12.0, 3.0, float(p["xg_weight"]))
        rt_now = fit_poisson_ratings(ctx["long"], selected_gw, _xw_r, _hl_r, _rg_r)
        _dp = dps_points(df.assign(av=first_av.reindex(df.index).fillna(1.0)), rates_now, rt_now, fx[fx["event"] == selected_gw])
        df["DPS"] = _dp["DPS"]
        for c_ in ("dps_goals", "dps_assists", "dps_cs", "dps_defcon", "dps_bonus"):
            df[c_] = _dp[c_]
        df["DPS_raw"] = df["DPS"]
        df["FPL_xP"] = pd.to_numeric(df[ep_col_], errors="coerce") if (ep_col_ and ep_col_ in df.columns) else np.nan
        df["HotCold"] = df["AER1"] - df["DPS"]

        # ---- Theta Swole: projected points above a replacement-level player at the same position ----
        #   theta = [AER + beta * (DPS not explained by AER)] - replacement level for his position + risk appetite * (Ceiling - Floor)
        theta_beta = 0.0
        dps_val = dps_validation(train_df[train_df["GW"] < selected_gw] if len(train_df) else train_df, ctx["h"], ctx["long"], ctx["fin"], fx, players, p)
        if model_ok and len(wf_use) and len(dps_val):
            _vv = dps_val.merge(wf_use.assign(aer=np.clip(wf_use["xPts"] + alpha * wf_use["adj"], 0, None))[["id", "GW", "aer"]], on=["id", "GW"])
            if len(_vv):
                _vv["resid"] = _vv.groupby("GW", group_keys=False).apply(lambda g_: pd.Series(dps_resid(g_["dps"].to_numpy(), g_["aer"].to_numpy()), index=g_.index))
                theta_beta = fit_theta_beta(_vv[["resid", "actual", "aer"]])
        _ok = (df["E90"] > 0) & (df["n_fix"] > 0) & (df["AER1"] > 0)
        df["DPS_signal"] = theta_beta * dps_resid(df["DPS_raw"].to_numpy(float), df["AER1"].to_numpy(float), _ok.to_numpy())
        df["E_adj"] = df["AER"] + df["DPS_signal"]
        _league = load_league(int(league_id))
        n_teams = int(len(_league["entries"])) or 10
        repl = replacement_levels(df, "E_adj", n_teams)
        df["Repl"] = df["element_type"].map(repl)
        df["Theta"] = df["E_adj"] - df["Repl"] + risk_kappa * (df["Ceiling"] - df["Floor"]).fillna(0)
        # how much each player would improve YOUR best XI at his position (needs your team picked on the My Team page)
        df["Upgrade"] = np.nan
        _my_ids = [i_ for i_ in squad_for(_league, st.session_state.get("my_entry"), selected_gw, next_gw) if i_ in df.index]
        if _my_ids:
            _xi_m, _bench_m, _ = best_lineup(df.loc[_my_ids], "AER1")
            _weak = {}
            for et_ in (1, 2, 3, 4):
                s_ = _xi_m.loc[_xi_m["element_type"] == et_, "E_adj"]
                b_ = _bench_m.loc[_bench_m["element_type"] == et_, "E_adj"]
                _weak[et_] = float(s_.min()) if len(s_) else (float(b_.max()) if len(b_) else 0.0)
            df["Upgrade"] = df["E_adj"] - df["element_type"].map(_weak)
        # ---- WPA: win probability added over the next matchups (needs your team picked) ----
        df["WPA"], df["PtsAdded"], df["Drop"] = np.nan, np.nan, ""
        wpa_info = None
        if _my_ids and aer_by_gw:
            opp_by_gw = {}
            mt_w = _league["matches"]
            if not mt_w.empty and {"event", "league_entry_1", "league_entry_2"} <= set(mt_w.columns):
                for g_ in aer_by_gw:
                    r_ = mt_w[(mt_w["event"] == g_) & ((mt_w["league_entry_1"] == my_entry_sel) | (mt_w["league_entry_2"] == my_entry_sel))]
                    if len(r_):
                        o_le = int(r_["league_entry_2"].iloc[0] if int(r_["league_entry_1"].iloc[0]) == my_entry_sel else r_["league_entry_1"].iloc[0])
                        opp_by_gw[g_] = squad_for(_league, o_le, g_, next_gw)
            owned_ = set(_league["owner"]) | set(taken_ids or [])
            hval_ = sum(aer_by_gw[g_].reindex(df.index).fillna(0) for g_ in aer_by_gw)
            cands_ = [i_ for i_ in df.index if i_ not in owned_ and i_ not in _my_ids and hval_.get(i_, 0) > 0.05]
            wpa_info = wpa_table(df, aer_by_gw, _my_ids, opp_by_gw, cands_, wpa_decay)
            if wpa_info is not None and len(wpa_info["table"]):
                wt_ = wpa_info["table"]
                df.loc[wt_.index, "WPA"] = wt_["WPA"]
                df.loc[wt_.index, "PtsAdded"] = wt_["PtsAdded"]
                df.loc[wt_.index, "Drop"] = wt_["DropId"].map(df["web_name"]).fillna("")
        # ---- BPS profile (average / floor / ceiling per 90, within position) and the bonus race simulation ----
        BPS_COLS = ["bps_avg", "bps_floor", "bps_ceil", "bps_vol", "bps_n", "z_bps_avg", "z_bps_floor", "z_bps_ceil",
                    "pct_bps_avg", "pct_bps_floor", "pct_bps_ceil"]
        for c_ in BPS_COLS + ["xBonus", "P3bonus", "Pbonus"]:
            df[c_] = np.nan
        bps_err = None
        try:
            prof_ = bps_profile(ctx["h"], selected_gw)
            if len(prof_):
                pr_ = prof_.reindex(df.index)
                for c_ in BPS_COLS:
                    df[c_] = pr_[c_].to_numpy()
                med_ = prof_.groupby("element_type")[["bps_avg", "bps_floor", "bps_ceil", "bps_vol"]].median()
                for c_ in ["bps_avg", "bps_floor", "bps_ceil", "bps_vol"]:
                    df[c_] = df[c_].fillna(df["element_type"].map(med_[c_]))
                for c_ in ["z_bps_avg", "z_bps_floor", "z_bps_ceil"]:
                    df[c_] = df[c_].fillna(0.0)
                start_p = (df["p60_used"] * first_av.reindex(df.index).fillna(1.0)).clip(0, 1)
                slim_ = pd.DataFrame({"team": df["team"].astype(int), "start_p": start_p, "sub_p": (df["appear"] - start_p).clip(0, 1),
                                      "scale": np.sqrt(((df["AER1"] + 1) / (pd.to_numeric(df["ppg"], errors="coerce").fillna(0) + 1)).clip(0.6, 1.6)),
                                      "bps_avg": df["bps_avg"], "bps_floor": df["bps_floor"], "bps_ceil": df["bps_ceil"]}, index=df.index).fillna(0)
                bs_ = bonus_sim(slim_, fx[fx["event"] == selected_gw][["id", "team_h", "team_a"]])
                df["xBonus"], df["P3bonus"], df["Pbonus"] = bs_["xBonus"], bs_["P3"], bs_["Pany"]
        except Exception as e_b:
            bps_err = f"{type(e_b).__name__}: {e_b}"
        # ---- Zipf xVR: expected points from the top-20% haul tail, Zipf-weighted by rank ----
        df["ZipfXVR"], df["EliteP"], df["EliteSize"], df["HaulPts"], df["HaulSize"] = 0.0, np.nan, np.nan, np.nan, np.nan
        zipf_info = {"law": pd.DataFrame(), "model": None, "tr_coef": 0.0, "tr_z": 0.0, "tr_used": False, "kappa": np.nan, "s": np.nan}
        try:
            _exp = train_df[["id", "GW", "xPts"]] if len(train_df) and "xPts" in train_df.columns else None
            if _exp is not None and "season" in train_df.columns:
                _exp = train_df.loc[train_df["season"] == "this season", ["id", "GW", "xPts"]]
            _h_before = ctx["h"][ctx["h"]["round"] < selected_gw]
            Zt, zlaw = zipf_table(_h_before, _exp, "position")
            if len(Zt):
                zh = zipf_history(Zt)
                zrows = zipf_training_rows(train_df[train_df["GW"] < selected_gw] if len(train_df) else train_df, Zt, zh, ctx["h"])
                zrows = add_theta_feats(zrows, wf_use, alpha, god_conv, dd_mode, n_teams) if len(zrows) else zrows
                zb_ = zipf_backtest(zrows)
                # the original definition (points over each player's own forecast), kept only for comparison
                try:
                    Zo, _ = zipf_table(_h_before, _exp, "forecast")
                    zbo_ = zipf_backtest(zipf_training_rows(train_df[train_df["GW"] < selected_gw] if len(train_df) else train_df, Zo, zipf_history(Zo), ctx["h"]))
                    if len(zbo_) and len(zb_):
                        zipf_info["defs"] = pd.DataFrame({
                            "Over each player's own forecast (old)": zbo_[["AUC (picking the top 20%)", "Top-20 pts by Zipf xVR", "Hauls among xVR top 20"]].mean(),
                            "Over the position baseline (new)": zb_[["AUC (picking the top 20%)", "Top-20 pts by Zipf xVR", "Hauls among xVR top 20"]].mean()})
                except Exception:
                    pass
                # pick the elite model's inputs by out-of-sample hit rate: the standard set, without the BPS profile, or with Theta's ingredients.
                # A change from the standard set needs +0.002 AUC on average and to win at least half the gameweeks.
                converge, use_bps, variant = False, True, "standard inputs"
                if len(zb_) and "AUC with Theta Swole inputs" in zb_.columns:
                    base_ = zb_["AUC (picking the top 20%)"]
                    best_gain = 0.0
                    for name_, col_ in (("with Theta Swole's inputs", "AUC with Theta Swole inputs"), ("without the BPS profile", "AUC without BPS profile")):
                        d_ = (zb_[col_] - base_).dropna()
                        if len(d_) >= 3 and d_.mean() > max(0.002, best_gain) and (d_ > 0).mean() >= 0.5:
                            best_gain, variant = float(d_.mean()), name_
                    converge, use_bps = variant.startswith("with Theta"), not variant.startswith("without")
                    d_auc = (zb_["AUC with Theta Swole inputs"] - base_).dropna()
                    zipf_info.update(auc_base=float(zb_["AUC (picking the top 20%)"].mean()),
                                     auc_conv=float(zb_["AUC with Theta Swole inputs"].mean()),
                                     auc_theta=float(zb_["AUC of Theta Swole alone"].mean()), conv_wins=float((d_auc > 0).mean()) if len(d_auc) else np.nan,
                                     hist_rho=float(zb_["Rank corr Theta Swole vs xVR"].mean()))
                zipf_info.update(converge=converge, use_bps=use_bps, variant=variant,
                                 auc_nobps=float(zb_["AUC without BPS profile"].mean()) if len(zb_) else np.nan)
                zm = zipf_model(zrows, theta=converge, bps=use_bps)
                cmod = credit_fit(zrows) if len(zrows) else None
                live = zh[zh["GW"] == zh["GW"].max()].set_index("id")
                feat_live = df.assign(elite_rate=live["elite_rate"].reindex(df.index).fillna(0.2))
                # Theta Swole's ingredients for this gameweek, built exactly as in the training rows
                _xp = pd.to_numeric(df["xPts"], errors="coerce").fillna(0)
                _repl = replacement_levels(df.assign(_xp=_xp), "_xp", n_teams)
                _okr = (df["E90"] > 0) & (df["n_fix"] > 0) & (df["AER1"] > 0)
                feat_live = feat_live.assign(theta_rel=_xp - df["element_type"].map(_repl),
                                             dps_resid=dps_resid(df["DPS_raw"].to_numpy(float), df["AER1"].to_numpy(float), _okr.to_numpy()),
                                             spread=(df["Ceiling"] - df["Floor"]).fillna(0), upside=(df["Ceiling"] - df["AER1"]).fillna(0))
                if zm is not None:
                    pz = pd.Series(logit_predict(zm, _zipf_X(feat_live, bps=use_bps, theta=converge).to_numpy()), index=df.index)
                else:          # too little history for the model: decayed elite rate alone
                    pz = feat_live["elite_rate"].astype(float)
                # transfers in: only if a past season shows they predict the top 20% beyond everything else (z > 2)
                if seasons_used and "code" in df.columns:
                    coef_, z_, n_ = zipf_transfer_check(seasons_used[0], p_key)
                    zipf_info.update(tr_coef=coef_, tr_z=z_)
                    tin_ = load_transfers_in()
                    if z_ > 2 and coef_ > 0 and len(tin_):
                        lt_ = np.log1p(df["code"].map(tin_).fillna(0).clip(lower=0))
                        zt_ = (lt_ - lt_.mean()) / (lt_.std() + 1e-9)
                        lo_ = np.log(pz.clip(1e-6, 1 - 1e-6) / (1 - pz.clip(1e-6, 1 - 1e-6))) + coef_ * zt_
                        pz = 1 / (1 + np.exp(-lo_))
                        zipf_info["tr_used"] = True
                el_ = Zt[Zt["elite"] > 0]
                cpos_ = el_.groupby("element_type")["credit"].mean()
                c_prior = df["element_type"].map(cpos_).fillna(float(el_["credit"].mean()) if len(el_) else 1.0)
                n_el = live["n_elite"].reindex(df.index).fillna(0)
                c_mean = live["credit_mean"].reindex(df.index)
                c_model = (pd.Series(credit_predict(cmod, _zipf_X(feat_live).to_numpy()), index=df.index) if cmod is not None else c_prior)
                # his own elite weeks blended with the learned size, in log space so one freak haul can't dominate (heavy tail, regresses)
                c_shr = np.expm1((np.log1p(c_mean.fillna(0).clip(lower=0)) * n_el + 3.0 * np.log1p(np.clip(c_model, 0, None))) / (n_el + 3.0))
                kappa_ = float(el_["total_points"].clip(lower=0).mean() / max(el_["credit"].mean(), 1e-9)) if len(el_) else 1.0
                avail_ = first_av.reindex(df.index).fillna(1.0) * (df["n_fix"] > 0)
                df["EliteP"] = (pz * avail_).clip(0, 1)
                df["EliteSize"] = c_shr * kappa_
                df["ZipfXVR"] = df["EliteP"] * df["EliteSize"]          # research index (Accuracy page); not shown as a ranking
                # Haul points, in real FPL points: haul chance x the points he scores in a typical haul week (his own haul weeks, pulled
                # toward his position's typical haul by three pseudo-weeks, so one big week doesn't decide it)
                _hp = el_.assign(pts=el_["total_points"].clip(lower=0))
                _pos_h = _hp.groupby("element_type")["pts"].mean()
                _own_h = _hp.groupby("id")["pts"].agg(["mean", "count"])
                _nh = _own_h["count"].reindex(df.index).fillna(0)
                _prior_h = df["element_type"].map(_pos_h).fillna(float(_hp["pts"].mean()) if len(_hp) else 6.0)
                df["HaulSize"] = (_own_h["mean"].reindex(df.index).fillna(0) * _nh + 3.0 * _prior_h) / (_nh + 3.0)
                # the points from big weeks are part of his expected points, so they can't exceed AER (the two models are fitted separately)
                df["HaulPts"] = np.minimum(df["EliteP"] * df["HaulSize"], df["AER1"].clip(lower=0))
                # how alike are the two ratings right now? (players with a fixture and minutes this season)
                _el = (df["n_fix"] > 0) & (df["AER1"] > 0) & (df["E90"] > 0)
                if _el.sum() >= 20:
                    _t, _x = df.loc[_el, "Theta"], df.loc[_el, "ZipfXVR"]
                    _top_t, _top_x = set(_t.nlargest(20).index), set(_x.nlargest(20).index)
                    zipf_info.update(rho=float(rank_corr(_t, _x)), top20_overlap=len(_top_t & _top_x),
                                     rho_pos={POS_GROUP.get(int(et_), "MID"): float(rank_corr(df.loc[_el & (df["element_type"].isin(ets_)), "Theta"],
                                                                                         df.loc[_el & (df["element_type"].isin(ets_)), "ZipfXVR"]))
                                              for et_, ets_ in ((1, (1, 2)), (3, (3,)), (4, (4,)))})
                zipf_info.update(law=zlaw, model=zm, kappa=kappa_, s=float(zlaw["Zipf exponent s"].median()) if len(zlaw) else np.nan,
                                 rows=zrows, cmod=cmod)
        except Exception as e_z:
            zipf_info["error"] = f"{type(e_z).__name__}: {e_z}"
        full_df = df.copy()                                                              # every player, before filters (My Team tab)

        if only_wire and taken_ids is not None:
            df = df[~df.index.isin(taken_ids)]
        if not include_out:
            df = df[df["status"].isin(["a", "d"])]
        df = df[df["position"].isin(sel_pos) & (pd.to_numeric(df["minutes"], errors="coerce").fillna(0) >= min_minutes)]
        if selected_team != "All Teams":
            df = df[df["team_full_name"] == selected_team]
        if search_q:
            df = df[df["web_name"].str.contains(search_q, case=False, na=False)
                    | df["first_name"].str.contains(search_q, case=False, na=False)
                    | df["second_name"].str.contains(search_q, case=False, na=False)]
        # Green score (0-100): seven areas, each counted once, so it isn't AER repeated under different names. In each area a player scores
        # how far he sits into the green half of the players shown (0 at the median, 1 at the very top); the score is the average over the
        # areas that apply to his position, faded by confidence like the colours. Green areas = how many areas are in the top quarter.
        GREEN_AREAS = {"Expected points": ["AER"], "Upside": ["Ceiling", "EliteP"], "Minutes": ["xMins"], "Attack": ["xG", "xA"],
                       "Defence": ["cs_team", "pdc"], "Bonus": ["xBonus"], "Value to your team": ["WPA", "PtsAdded"]}
        if len(df):
            _area = pd.DataFrame(index=df.index)
            for name_, cols_ in GREEN_AREAS.items():
                cols_ = [c_ for c_ in cols_ if c_ in df.columns and pd.to_numeric(df[c_], errors="coerce").notna().sum() > 1]
                if name_ == "Value to your team" and cols_:
                    cols_ = cols_[:1]                       # WPA if available, otherwise Pts added
                if cols_:
                    _area[name_] = df[cols_].apply(pd.to_numeric, errors="coerce").rank(pct=True).mean(axis=1)
            if "Defence" in _area.columns:
                _area.loc[df["element_type"] == 4, "Defence"] = np.nan        # forwards score nothing for clean sheets or DEFCON
            _green = (2 * (_area - 0.5)).clip(lower=0)
            df["GreenScore"] = (100 * _green.mean(axis=1) * (0.5 + 0.5 * df["Confidence"].fillna(0.5))).round(1)
            df["GreenCells"] = (_area >= 0.75).sum(axis=1)
            df["GreenAreas"] = _area.notna().sum(axis=1)
        else:
            df["GreenScore"], df["GreenCells"], df["GreenAreas"] = np.nan, 0, 0
        if rank_by == "WPA":
            rank_col = "WPA" if df["WPA"].notna().any() else ("PtsAdded" if df["PtsAdded"].notna().any() else "Theta")
        else:
            rank_col = {"AER": "AER", "DPS": "DPS", "Haul points": "HaulPts", "Green score": "GreenScore"}.get(rank_by, "Theta")
        rank_label = {"GreenScore": "Green score (strong across the most areas)", "HaulPts": "Haul points", "WPA": "WPA", "PtsAdded": "Pts added (no head-to-head fixtures found)", "Theta": "Theta Swole", "AER": "AER", "DPS": "DPS"}[rank_col]
        df = df.sort_values([rank_col, "AER"], ascending=[False, False], na_position="last")
        df["Rank"] = np.arange(1, len(df) + 1)

        ha = np.where(df["home"] == 1, "H", np.where((df["home"] == 0) & (df["n_fix"] > 0), "A", np.where(df["n_fix"] > 0, "H/A", "–")))
        num = lambda col: pd.to_numeric(df[col], errors="coerce") if col in df.columns else np.nan
        view = pd.DataFrame({
            # what matters first: who, when, how many points, floor/ceiling, playing time, form, returns, availability
            "Rank": df["Rank"], "Player": df["web_name"], "Pos": df["position"], "Team": df["team_code"], "Opp": df["Opp"], "H/A": ha,
            "Green score": df["GreenScore"], "Green cells": df["GreenCells"].astype(int).astype(str) + "/" + df["GreenAreas"].astype(int).astype(str),
            "WPA %": (df["WPA"] * 100).round(1), "Pts added": df["PtsAdded"].round(2), "Drop": df["Drop"],
            "Haul pts": df["HaulPts"].round(2), "Elite %": (df["EliteP"] * 100).round(0), "Haul size": df["HaulSize"].round(1),
            "BPS avg": df["bps_avg"].round(1), "BPS floor": df["bps_floor"].round(1), "BPS ceil": df["bps_ceil"].round(1),
            "xBonus": df["xBonus"].round(2), "P(3 bonus) %": (df["P3bonus"] * 100).round(0),
            "Theta Swole": df["Theta"].round(2), "Upgrade vs my XI": df["Upgrade"].round(2), "Replacement": df["Repl"].round(2),
            "DPS signal": df["DPS_signal"].round(2), "DPS": df["DPS"].round(2), "AER − DPS": df["HotCold"].round(2), "FPL xP": df["FPL_xP"].round(1),
            "AER": df["AER"].round(2), "Confidence %": (df["Confidence"] * 100).round(0),
            "Floor": df["Floor"].round(1), "Ceiling": df["Ceiling"].round(1),
            "5+ %": (df["P5"] * 100).round(0), "8+ %": (df["P8"] * 100).round(0),
            "Mins": df["xMins"].round(0), "Form": df["form"].round(1),
            "xG": df["xG"].round(2), "xA": df["xA"].round(2), "CS %": (df["cs_team"] * 100).round(0),
            "DEFCON %": (df["pdc"] * 100).round(0),
            "Status": df["Status"], "Chance %": num("chance_of_playing_next_round"), "News": df["news"] if "news" in df.columns else "",
            "Season Pts": num("total_points"), "PPG": num("points_per_game"),
            # model drivers
            "Base": df["Base"].round(2), "HMPR": df["hm_adj"].round(2),
            "Stability": df["stab_self"].round(2), "Opp Stability": df["stab_opp"].round(2), "Leader": (df["leader"] * 100).round(0),
            "Stakes": df["des_self"].round(2), "Opp Stakes": df["des_opp"].round(2),
            "Team Missing %": (df["miss_self"] * 100).round(0), "Opp Missing %": (df["miss_opp"] * 100).round(0),
            "vs Team": df["u_team"].round(2), "vs System": df["u_form"].round(2), "vs Tier": df["u_diff"].round(2),
            "Opp System": df["OppSystem"], "Matchup": df["mm_idx"].round(2), "Finisher role %": (df["role_f"] * 100).round(0),
            "Context ×": df["om_att"].round(2), "Finish ×": df["a_fin"].round(2), "Assist ×": df["b_ast"].round(2),
            "Momentum ×": df["m_mom"].round(2),
            "History (T/S/D)": (df["n_team"].round().astype(int).astype(str) + "/" + df["n_form"].round().astype(int).astype(str)
                                + "/" + df["n_diff"].round().astype(int).astype(str)),
            # detail
            "Mins/Game": df["mins_6"].round(0), "xG/90": df["g90"].round(2), "xA/90": df["a90"].round(2),
            "Creativity": df["creativity_6"].round(1), "Threat": df["threat_6"].round(1), "Influence": df["influence_6"].round(1),
            "DEFCON/90": df["dc90"].round(1), "Goals Against": df["xGC"].round(2),
            "Pts Mins": df["p_app"].round(2), "Pts Goals": df["p_goal"].round(2), "Pts Assists": df["p_ast"].round(2),
            "Pts CS": df["p_cs"].round(2), "Pts DEFCON": df["p_dc"].round(2), "Pts Other": df["p_oth"].round(2),
            "Games Used": df["n_w"].round(1), "Sample": df["conf"].round(2), "Focus": df["Focus"],
        }).reset_index(drop=True)
        if mkt_live:
            pos_cs = list(view.columns).index("CS %") + 1
            view.insert(pos_cs, "Mkt CS %", (df["mk_cs"] * 100).round(0).to_numpy())
            view.insert(pos_cs + 1, "Mkt Goal %", (df["mk_goal"] * 100).round(0).to_numpy())
        fdr_list = df["FDR"].round().astype(int).tolist()
        fdr_cols = {1: "#0b6e3a", 2: "#2fb463", 3: "#e8b923", 4: "#ee7d2a", 5: "#c23b2a"}

        def style_opp(_s):
            return [f"background-color: {fdr_cols.get(f, '#e8b923')}; color: {'black' if f in (3, 4) else 'white'};" for f in fdr_list]

        def col_style(invert):
            return lambda s: pct_colors(s, invert)

        conf_arr = df["Confidence"].to_numpy(float)

        def forecast_style(invert=False):
            return lambda s: value_colors(s, invert, conf_arr)

        ESSENTIAL = ["Rank", "Player", "Pos", "Team", "Opp", "Green score", "WPA %", "Pts added", "Drop", "Haul pts", "Elite %", "BPS ceil", "xBonus", "Theta Swole", "DPS", "AER", "Confidence %", "Floor",
                     "Ceiling", "Mins", "Form", "xG", "xA", "CS %", "Status", "Chance %", "News"]
        st.session_state.setdefault("cols_mode", "Essentials")
        _c1, _c2 = st.columns([1, 2])
        cols_mode = _c1.radio("Columns", ["Essentials", "Everything"], key="cols_mode", horizontal=True, label_visibility="collapsed",
                              help="Essentials keeps the table readable; Everything shows every model column.")
        if _c2.toggle("Sort by Green score (most and deepest green first)", key="green_sort",
                      help="Re-orders the table by Green score without changing the 'Rank players by' setting.") and "Green score" in view.columns:
            _gs = pd.to_numeric(view["Green score"], errors="coerce").fillna(-1).to_numpy()
            _gn = pd.to_numeric(view["Green cells"].astype(str).str.split("/").str[0], errors="coerce").fillna(0).to_numpy()
            _ord = np.lexsort((-_gn, -_gs))                       # one order for the table, its colours and its fixture shading
            view = view.iloc[_ord].reset_index(drop=True)
            fdr_list = [fdr_list[i_] for i_ in _ord]
            conf_arr = conf_arr[_ord]
        view_show = view[[c for c in ESSENTIAL if c in view.columns]] if cols_mode == "Essentials" else view
        sty = view_show.style.apply(style_opp, subset=["Opp"], axis=0)
        # model forecasts: hue = how good, strength = how confident
        for c in ["Green score", "WPA %", "Pts added", "Haul pts", "Elite %", "Haul size", "xBonus", "P(3 bonus) %", "Theta Swole", "Upgrade vs my XI", "DPS", "AER", "Base", "Floor", "Ceiling", "5+ %", "8+ %", "Mins", "xG", "xA", "CS %", "DEFCON %",
                  "Pts Goals", "Pts Assists", "Pts DEFCON"]:
            if c in view_show.columns:
                sty = sty.apply(forecast_style(False), subset=[c], axis=0)
        if "Goals Against" in view_show.columns:
            sty = sty.apply(forecast_style(True), subset=["Goals Against"], axis=0)
        # inputs and descriptive stats: same hue, fixed softer strength
        for c in ["BPS avg", "BPS floor", "BPS ceil", "FPL xP", "Mins/Game", "Form", "Creativity", "Threat", "Influence", "Stability", "Leader", "HMPR", "vs Team", "vs System",
                  "vs Tier", "Stakes", "Opp Missing %", "xG idx", "xA idx", "Creativity idx", "Threat idx", "Influence idx", "70+ Pts/Game",
                  "Matchup", "Mkt CS %", "Mkt Goal %", "Context ×", "Finish ×", "Assist ×"]:
            if c in view_show.columns:
                sty = sty.apply(col_style(False), subset=[c], axis=0)
        if "Opp Stability" in view_show.columns:
            sty = sty.apply(col_style(True), subset=["Opp Stability"], axis=0)
        sty = sty.apply(conf_colors, subset=["Confidence %"], axis=0).format(na_rep="–")

        st.markdown("---")
        st.caption(f"**Theta Swole** = projected points above a replacement-level player at the same position: (AER + DPS signal) − replacement. "
                   f"Replacement = the best player just outside the league's starting pool ({n_teams} teams × typical starters per position), so a "
                   f"+2.0 midfielder and a +2.0 defender are equally valuable. **DPS signal** = the part of DPS that AER doesn't already contain, "
                   f"weighted by how much it predicted points in past gameweeks (β = {theta_beta:.2f}{'; DPS currently adds nothing beyond AER' if theta_beta < 0.005 else ''}). "
                   + (f"Risk appetite {risk_kappa:+.2f} × (Ceiling − Floor) is added. " if abs(risk_kappa) > 1e-9 else "")
                   + ("**Upgrade vs my XI** = how many points he'd add over your weakest starter at his position."
                      if df["Upgrade"].notna().any() else "Pick your team in the sidebar to see **Upgrade vs my XI**."))
        _zs = zipf_info.get("s", np.nan)
        _zl = zipf_info.get("law", pd.DataFrame())
        st.caption("**Haul points** (pts) = haul chance × the points he usually scores in a haul week: the expected points that come from big weeks. "
                   "**Haul chance** (Elite %) comes from the Zipf research model: each past gameweek, everyone who played is ranked by points over "
                   "what an ordinary player in the same position would score in the same minutes; the top 20% earn credit = points ÷ rank^s × (1 + minutes fraction)"
                   + (f", with the Zipf exponent s fitted from the data (median {_zs:.2f}; the top 20% scored "
                      f"{_zl['Share of points scored by the top 20%'].mean() * 100:.0f}% of all points)" if len(_zl) else "")
                   + ". **Elite %** is each player's chance of making the next top 20%, from xG, xA, threat, creativity, BPS (average, floor, "
                   "ceiling and volatility within his position), minutes, form, position and past elite rate. **Elite size** is how big his "
                   "top-20% week tends to be, learned from the same signals (BPS ceiling matters most here) and blended with his own elite weeks. "
                   "(Elite % model fitted on earlier gameweeks"
                   + ("" if zipf_info.get("model") is not None else "; too little history yet, so it uses past elite rate alone") + "). "
                   + ("FPL transfers in are included (last season showed they add predictive power). " if zipf_info.get("tr_used") else
                      (f"FPL transfers in are not used (last season's test: z = {zipf_info.get('tr_z', 0):.1f}, below the 2.0 needed). "
                       if seasons_used else "FPL transfers in are only used once a past season confirms they help (turn on past seasons). "))
                   + (f"Elite model inputs in use: **{zipf_info.get('variant', 'standard inputs')}** (chosen by out-of-sample hit rate). ")
                   + ("**Converged with Theta Swole:** the elite model also uses Theta's ingredients (points above replacement, DPS signal, "
                      f"floor–ceiling spread and upside), because that picked the top 20% better out of sample (AUC {zipf_info.get('auc_base', float('nan')):.3f} → "
                      f"{zipf_info.get('auc_conv', float('nan')):.3f}). " if zipf_info.get("converge") else
                      ("Theta Swole's ingredients were tested in the elite model but didn't improve it out of sample, so they're left out. "
                       if "auc_conv" in zipf_info else ""))
                   + (f"Theta Swole and Zipf xVR agree on ordering at ρ = {zipf_info['rho']:.2f}; {zipf_info.get('top20_overlap', 0)} of their top 20 are the same players. "
                      if "rho" in zipf_info else "")
                   + "**Green score** (0–100) = strength across seven areas, each counted once: expected points, upside, minutes, attack, "
                   "defence (not for forwards), bonus and value to your team. Accuracy → Zipf xVR shows whether the haul model picks the top 20%.")
        st.caption("**DPS (process points, pts)** = expected FPL points from stats that repeat: expected goals and assists, minutes, clean-sheet chance, "
                   "DEFCON, saves and bonus rate, each pulled toward the position norm by how unreliable the data shows it to be, adjusted for the opponent "
                   "with the season team ratings. No goals, assists or bonus actually scored are used. **AER − DPS** above 0 = output running ahead of the "
                   "process (likely to cool); below 0 = due.")
        st.caption("Old DPS definition, kept for reference: (DPS formula + FPL xP) ÷ 2. **DPS formula** = ((AER + Form) × (xG + xA + creativity + threat + influence)) ÷ √Ceiling, "
                   "for the first target gameweek, with the five terms as 0–1 percentile indices within position. **FPL xP** is FPL's own expected points for this gameweek. "
                   "FPL only publishes it for the current and next gameweek; for other gameweeks DPS is the formula alone.")
        if False:
            st.caption(f"ℹ️ FPL publishes no expected points for GW{selected_gw}, so DPS here is the formula part only.")
    with _Section(_on and rank_sub == "BPS"):
        st.subheader(f"BPS profile by position · GW{selected_gw}")
        st.caption("BPS per 90 minutes from games of 30+ minutes, recent games counting most: **average**, **floor** (a bad day, 10th percentile) "
                   "and **ceiling** (a great day, 90th percentile), each pulled toward the position's norm when a player has few games. Players are "
                   "only compared with their own position. **xBonus** and **P(3 bonus)** come from simulating every fixture's bonus race 4,000 times "
                   "with each player's BPS profile, playing-time chances and fixture. Ceiling and floor also feed Zipf xVR (see its caption).")
        if bps_err:
            st.warning(f"BPS profile unavailable: {bps_err}")
        st.session_state.setdefault("bps_sort", "Ceiling")
        if st.session_state.get("bps_sort") == "Zipf xVR":
            st.session_state["bps_sort"] = "Haul points"
        sort_b = st.radio("Sort by", ["Ceiling", "Average", "Floor", "Expected bonus", "Haul points"], key="bps_sort", horizontal=True)
        col_b = {"Ceiling": "bps_ceil", "Average": "bps_avg", "Floor": "bps_floor", "Expected bonus": "xBonus", "Haul points": "HaulPts"}[sort_b]
        tabs_b = st.tabs(["Goalkeepers", "Defenders", "Midfielders", "Forwards"])
        for et_b, tb_b in zip((1, 2, 3, 4), tabs_b):
            with tb_b:
                d_b = df[(df["element_type"] == et_b) & (df["bps_n"].fillna(0) >= 1)].sort_values(col_b, ascending=False)
                if d_b.empty:
                    st.info("No players with 30+ minute games in the current filters.")
                    continue
                t_b = pd.DataFrame({"Player": d_b["web_name"], "Team": d_b["team_code"], "Opp": d_b["Opp"],
                                    "Ceiling": d_b["bps_ceil"].round(1), "Average": d_b["bps_avg"].round(1), "Floor": d_b["bps_floor"].round(1),
                                    "Ceiling pct": (d_b["pct_bps_ceil"] * 100).round(0), "Average pct": (d_b["pct_bps_avg"] * 100).round(0),
                                    "Floor pct": (d_b["pct_bps_floor"] * 100).round(0), "Volatility": d_b["bps_vol"].round(2),
                                    "xBonus": d_b["xBonus"].round(2), "P(3 bonus) %": (d_b["P3bonus"] * 100).round(0),
                                    "P(any bonus) %": (d_b["Pbonus"] * 100).round(0), "Haul pts": d_b["HaulPts"].round(2),
                                    "AER": d_b["AER1"].round(2), "Games": d_b["bps_n"].astype(int)}).reset_index(drop=True)
                sty_b = t_b.style
                for c_ in ["Ceiling", "Average", "Floor", "xBonus", "P(3 bonus) %", "P(any bonus) %", "Haul pts", "AER"]:
                    sty_b = sty_b.apply(lambda s_: pct_colors(s_), subset=[c_], axis=0)
                st.dataframe(sty_b.format(na_rep="–"), use_container_width=True, hide_index=True, height=520)
    with _Section(_on and rank_sub == "DPS team"):
        st.subheader(f"DPS selected team · GW{selected_gw}")
        dteam_ = dps_team(df)
        if dteam_ is None:
            st.info("Not enough players in the current filters to field a legal XI (1 GK, 3–5 DEF, 2–5 MID, 1–3 FWD). Widen the Positions or Team filters.")
        else:
            dxi_, dbench_, dform_ = dteam_
            dbase_, dsub_, dsd_ = team_projection(dxi_, dbench_)
            k1_, k2_, k3_ = st.columns(3)
            k1_.metric("Formation", dform_)
            k2_.metric("Projected points", f"{dbase_ + dsub_:.1f}", f"XI {dbase_:.1f} + bench cover {dsub_:.1f}", delta_color="off",
                       help="Expected FPL points of this XI, plus expected automatic-substitution points from the bench.")
            k3_.metric("Total DPS (XI)", f"{float(dxi_['DPS'].sum()):.1f}")
            st.markdown(pitch_html(dxi_, dbench_, show_dps=True), unsafe_allow_html=True)
            st.caption("The XI with the highest total DPS under FPL formation rules, chosen from the players the filters currently show "
                       "(with 'Only unowned players' ticked, that is the players available to you). The bench is a second keeper plus the next-best "
                       "outfielders by DPS, keeping a legal 2/5/5/3 squad. Card number = projected points; the small line shows DPS.")
    with _Section(_on and rank_sub == "Ranked players"):
        if wpa_info is not None and wpa_info["pwin"]:
            st.caption("**WPA %** = how many percentage points claiming him adds to your chance of winning your next "
                       f"{len(aer_by_gw)} matchup(s), later weeks weighted ×{wpa_decay:.2f} each, after dropping the player named in **Drop** and "
                       "re-picking your best XI each week. **Pts added** = the same for projected points. Your win chance now: "
                       + ", ".join(f"GW{g_} {p_ * 100:.0f}%" for g_, p_ in wpa_info["pwin"].items()) + ".")
        elif _my_ids:
            st.caption("No head-to-head fixtures found for your team in these gameweeks, so the table ranks by **Pts added** (projected points your "
                       "best XI gains, over the WPA horizon, after dropping the player named in **Drop**).")
        else:
            st.caption("Pick **Your team** in the sidebar to rank by WPA. Until then the table ranks by Theta Swole.")
        st.subheader(f"Ranked by {rank_label} — GW{selected_gw}" + (f"–{min(38, selected_gw + horizon - 1)}" if horizon > 1 else "")
                     + f" ({len(view)} players)")
        learn_msg = (f"Learned correction ON (strength {alpha:.2f}, trained on GW{int(train_use['GW'].min())}–GW{int(train_use['GW'].max())})"
                     if learned is not None else "Learned correction OFF (needs ≥3 finished gameweeks of data)")
        st.caption(f"{learn_msg}. **Colour = how good** among the players shown (red → amber → green). **Colour strength = how sure**: on forecast "
                   "columns a vivid cell is a confident forecast and a faded one is shaky. **Confidence %** (grey → deep blue) combines how much he has "
                   "played, how certain his minutes are and how tight his floor–ceiling range is. Input and stat columns use the same hues, always softer. "
                   "Floor/Ceiling = 10th/90th percentile of what similar forecasts actually scored; 5+/8+ % = how often they reached it. "
                   "Detail columns describe the first gameweek and use home stats for home fixtures, away stats for away fixtures.")
        try:
            cfg = {"Rank": st.column_config.NumberColumn("Rank", pinned=True), "Player": st.column_config.TextColumn("Player", pinned=True)}
            for c_, (lab_, hlp_) in METRIC_UNITS.items():
                if c_ in view_show.columns:
                    cfg[c_] = st.column_config.Column(lab_, help=hlp_)
        except Exception:
            cfg = {}
        try:
            st.dataframe(sty, use_container_width=True, height=680, hide_index=True, column_config=cfg)
        except TypeError:
            st.dataframe(sty, use_container_width=True, height=680)

# =========================================================
# MY TEAM — best starting XI + bench, projected against this gameweek's head-to-head opponent
# =========================================================
with _Section(page == "My Team"):
    st.subheader(f"My team · GW{selected_gw}")
    lg_ = load_league(int(league_id))
    ents_ = lg_["entries"]
    if ents_.empty or "entry_id" not in ents_.columns or "id" not in ents_.columns:
        st.info("Couldn't load this league's teams from the FPL Draft API. Check the league ID in the sidebar.")
    else:
        ents_ = ents_.copy()
        mgr_ = (ents_["player_first_name"].astype(str) if "player_first_name" in ents_.columns else "") + " " + \
               (ents_["player_last_name"].astype(str) if "player_last_name" in ents_.columns else "")
        ents_["label"] = ents_["entry_name"].astype(str) + "  ·  " + pd.Series(mgr_, index=ents_.index).astype(str).str.strip()
        labels_ = dict(zip(ents_["id"].astype(int), ents_["label"]))
        my_le = my_entry_sel
        st.session_state.setdefault("team_by", "AER (expected points)")
        team_by = st.radio("Choose lineup by", ["AER (expected points)", "DPS"], key="team_by", horizontal=True, help=HELP["team_by"])
        lineup_col = "DPS" if team_by == "DPS" else "AER1"
        eidx_ = ents_.set_index(ents_["id"].astype(int))

        def squad_ids(le_id):
            return squad_for(lg_, le_id, selected_gw, next_gw)

        def set_lineup_ids(le_id):
            eid_ = int(eidx_.loc[le_id, "entry_id"])
            for g_ in (int(selected_gw), int(next_gw) - 1, int(next_gw) - 2):
                pk_ = load_picks(eid_, g_) if g_ >= 1 else []
                if pk_:
                    return [e_ for e_, pos_ in pk_ if pos_ <= 11]
            return []

        def team_block(le_id):
            ids_ = [i_ for i_ in squad_ids(le_id) if i_ in full_df.index]
            if not ids_:
                return None
            sq_ = full_df.loc[ids_]
            xi_, bench_, form_ = best_lineup(sq_, lineup_col)
            base_, sub_, sd_ = team_projection(xi_, bench_)
            return {"sq": sq_, "xi": xi_, "bench": bench_, "form": form_, "base": base_, "sub": sub_, "sd": sd_, "total": base_ + sub_,
                    "name": str(eidx_.loc[le_id, "entry_name"])}

        mine = team_block(my_le)
        mt_ = lg_["matches"]
        opp_le, match_ = None, None
        if not mt_.empty and {"event", "league_entry_1", "league_entry_2"} <= set(mt_.columns):
            sel_ = mt_[(mt_["event"] == selected_gw) & ((mt_["league_entry_1"] == my_le) | (mt_["league_entry_2"] == my_le))]
            if len(sel_):
                match_ = sel_.iloc[0]
                opp_le = int(match_["league_entry_2"] if int(match_["league_entry_1"]) == my_le else match_["league_entry_1"])
        theirs = team_block(opp_le) if (opp_le is not None and opp_le in eidx_.index) else None

        if mine is None:
            st.info("Pick **Your team** in the sidebar." if my_le is None else "Couldn't find your squad for this gameweek.")
        else:
            st.markdown(f"**Best lineup by {'DPS' if lineup_col == 'DPS' else 'AER'}: {mine['form']}**")
            st.markdown(pitch_html(mine["xi"], mine["bench"]), unsafe_allow_html=True)
            c1_, c2_, c3_ = st.columns(3)
            c1_.metric(f"{mine['name']} — projected", f"{mine['total']:.1f} pts",
                       f"XI {mine['base']:.1f} + bench cover {mine['sub']:.1f}", delta_color="off",
                       help="Best starting XI's expected points, plus expected automatic-substitution points from the bench.")
            if theirs is not None:
                diff_ = mine["total"] - theirs["total"]
                sdd_ = float(np.sqrt(mine["sd"] ** 2 + theirs["sd"] ** 2)) or 1.0
                pwin_ = 0.5 * (1 + erf(diff_ / (sdd_ * np.sqrt(2))))
                c2_.metric(f"{theirs['name']} — projected", f"{theirs['total']:.1f} pts",
                           f"XI {theirs['base']:.1f} + bench cover {theirs['sub']:.1f}", delta_color="off",
                           help="Your opponent's best possible lineup, so this is their ceiling if they pick optimally.")
                c3_.metric("Your win chance", f"{pwin_ * 100:.0f}%", f"{diff_:+.1f} pts projected margin",
                           help="Approximate: treats each team's total as normal, with spread from every starter's floor–ceiling range.")
            else:
                c2_.info("No head-to-head opponent found for you this gameweek (classic-scoring league, or fixtures not published).")
            if match_ is not None and bool(match_.get("finished", False)):
                p1_, p2_ = match_.get("league_entry_1_points"), match_.get("league_entry_2_points")
                me_pts, op_pts = (p1_, p2_) if int(match_["league_entry_1"]) == my_le else (p2_, p1_)
                st.success(f"Final score: {mine['name']} {me_pts} – {op_pts} {theirs['name'] if theirs else 'Opponent'}")
            if horizon > 1:
                st.caption(f"Lineup and projections are for GW{selected_gw} only (the 'Gameweeks to sum' slider affects the Rankings tab).")


            set_ids = [i_ for i_ in set_lineup_ids(my_le) if i_ in mine["sq"].index]
            if len(set_ids) >= 11:
                cur_ = float(mine["sq"].loc[set_ids, "AER1"].sum())
                gain_ = mine["base"] - cur_
                ins_ = [n_ for i_, n_ in mine["xi"]["web_name"].items() if i_ not in set_ids]
                outs_ = [mine["sq"].loc[i_, "web_name"] for i_ in set_ids if i_ not in mine["xi"].index]
                if ins_:
                    msg_ = (f"Your current starting XI projects {cur_:.1f} pts; this lineup projects {mine['base']:.1f} (**{gain_:+.1f}**). "
                            f"Start {', '.join(map(str, ins_))}; bench {', '.join(map(str, outs_))}.")
                    (st.warning if gain_ > 0.05 else st.info)(msg_)
                else:
                    st.success(f"Your current starting XI ({cur_:.1f} pts) already matches this lineup.")

            show_ = mine["sq"].assign(Role=np.where(mine["sq"].index.isin(mine["xi"].index), "Start", "Bench"))
            tbl_ = pd.DataFrame({"Role": show_["Role"], "Player": show_["web_name"], "Pos": show_["position"], "Team": show_["team_code"],
                                 "Opp": show_["Opponent"], "AER": show_["AER1"].round(2), "DPS": show_["DPS"].round(2),
                                 "Confidence %": (show_["Confidence"] * 100).round(0),
                                 "Floor": show_["Floor"].round(1), "Ceiling": show_["Ceiling"].round(1), "Plays %": (show_["appear"] * 100).round(0),
                                 "Status": show_["Status"]}).sort_values(["Role", "AER"], ascending=[False, False])
            conf_t = tbl_["Confidence %"].to_numpy(float) / 100.0
            sty_t = (tbl_.style.apply(lambda s_: value_colors(s_, False, conf_t), subset=["AER", "DPS", "Floor", "Ceiling"], axis=0)
                     .apply(conf_colors, subset=["Confidence %"], axis=0).format(na_rep="–"))
            st.dataframe(sty_t, use_container_width=True, hide_index=True)
            if theirs is not None:
                with st.expander(f"Opponent's best lineup by {'DPS' if lineup_col == 'DPS' else 'AER'}: {theirs['name']} ({theirs['form']})"):
                    st.markdown(pitch_html(theirs["xi"], theirs["bench"]), unsafe_allow_html=True)
            st.caption("The best lineup maximises projected points under FPL formation rules (1 GK, 3–5 DEF, 2–5 MID, 1–3 FWD). "
                       "Bench cover assumes FPL's automatic substitutions and ignores the formation limits they must respect, so it is slightly generous.")


# =========================================================
# PICK TEAM — tap a player, then tap who to switch him with (as in the FPL app); transfers below
# =========================================================
PT_LIMIT = {1: 2, 2: 5, 3: 5, 4: 3}
PT_NAME = {1: "Goalkeepers", 2: "Defenders", 3: "Midfielders", 4: "Forwards"}
PT_XI = {1: (1, 1), 2: (3, 5), 3: (2, 5), 4: (1, 3)}       # starters allowed per position
PT_METRIC = {"AER (expected points)": "AER1", "Theta Swole": "Theta", "Haul points": "HaulPts", "DPS": "DPS", "Green score": "GreenScore"}
st.markdown(_PITCH_CSS + """<style>
.st-key-pt_pitch { border-radius: 16px; padding: 12px 8px 4px;
  background: radial-gradient(ellipse at 50% 45%, rgba(255,255,255,.08), rgba(0,0,0,.16) 85%), repeating-linear-gradient(180deg, #3a9a4f 0 12.5%, #348f48 12.5% 25%);
  box-shadow: inset 0 0 0 3px rgba(255,255,255,.7); }
.st-key-pt_bench { border-radius: 14px; padding: 10px 8px 4px; margin-top: 8px; background: linear-gradient(180deg, #e9f7ef, #d4eedd); }
.st-key-pt_pitch .pc, .st-key-pt_bench .pc { width: 100%; max-width: 110px; margin: 0 auto; }
.st-key-pt_pitch .stButton, .st-key-pt_bench .stButton { margin-top: 4px; }
.st-key-pt_pitch .stButton > button, .st-key-pt_bench .stButton > button { min-height: 1.6rem !important; padding: .05rem .3rem !important; margin-top: 0;
  font-size: .72rem !important; border-radius: 8px !important; max-width: 110px; margin-left: auto; margin-right: auto; display: block; }
.st-key-pt_pitch [data-testid="stMarkdownContainer"], .st-key-pt_bench [data-testid="stMarkdownContainer"] { overflow: visible; }
.st-key-pt_pitch .stButton > button[kind="secondary"], .st-key-pt_bench .stButton > button[kind="secondary"] {
  background: rgba(255,255,255,.9) !important; color: #37003C !important; border: none !important; }
.st-key-pt_pitch .stButton > button p, .st-key-pt_bench .stButton > button p { font-size: .72rem !important; }
.st-key-pt_bench [data-testid="stCaptionContainer"] p { color: #37003C !important; text-align: center; }
</style>""", unsafe_allow_html=True)


def _keyed_container(key):
    try:
        return st.container(key=key)
    except TypeError:          # older Streamlit: no styling hook, still works
        return st.container()


if page == "Pick Team":
    st.subheader(f"Pick team · GW{selected_gw}")
    if full_df is None or not len(full_df):
        st.info("Forecasts aren't ready yet.")
    else:
        fd_ = full_df
        ss_ = st.session_state
        lg_pt = load_league(int(league_id))
        my_sq = [int(i_) for i_ in squad_for(lg_pt, my_entry_sel, selected_gw, next_gw) if i_ in fd_.index] if my_entry_sel is not None else []
        owned_pt = set(lg_pt["owner"]) | set(taken_ids or [])
        if ss_.get("pt_metric") not in PT_METRIC:
            ss_["pt_metric"] = "AER (expected points)"
        mcol = PT_METRIC.get(ss_["pt_metric"], "AER1")
        mcol = mcol if mcol in fd_.columns else "AER1"

        def _et(i_):
            return int(fd_.at[i_, "element_type"])

        def _val(i_):
            v_ = fd_.at[i_, mcol] if i_ in fd_.index else np.nan
            return float(v_) if pd.notna(v_) else -1e9

        def _xi_ok(xi_):
            if len(xi_) != 11:
                return False
            c_ = {e_: sum(_et(i_) == e_ for i_ in xi_) for e_ in (1, 2, 3, 4)}
            return all(lo_ <= c_[e_] <= hi_ for e_, (lo_, hi_) in PT_XI.items())

        def _pt_best_xi():
            sq_ = [i_ for i_ in ss_.get("pt_squad", []) if i_ in fd_.index]
            ss_["pt_sel"] = None
            if len(sq_) >= 11:
                xi_, bench_, form_ = best_lineup(fd_.loc[sq_].assign(_m=[_val(i_) for i_ in sq_]), "_m")
                if form_ != "incomplete squad":
                    ss_["pt_xi"], ss_["pt_bench"] = [int(i_) for i_ in xi_.index], [int(i_) for i_ in bench_.index]
                    return
            ss_["pt_xi"], ss_["pt_bench"] = [], sq_

        def _pt_set_squad(ids_):
            sq_, cnt_ = [], {1: 0, 2: 0, 3: 0, 4: 0}
            for i_ in dict.fromkeys(int(x_) for x_ in ids_):
                if i_ in fd_.index and cnt_[_et(i_)] < PT_LIMIT[_et(i_)]:
                    sq_.append(i_)
                    cnt_[_et(i_)] += 1
            ss_["pt_squad"] = sq_
            _pt_best_xi()

        def _pt_best_squad():
            pool_ = [i_ for i_ in fd_.index if (i_ not in owned_pt or i_ in my_sq) and fd_.at[i_, "n_fix"] > 0]
            best_ = []
            for e_ in (1, 2, 3, 4):
                best_ += sorted([i_ for i_ in pool_ if _et(i_) == e_], key=_val, reverse=True)[:PT_LIMIT[e_]]
            _pt_set_squad(best_)

        def _pt_switch_result(a_, b_):
            xi_, bench_ = list(ss_["pt_xi"]), list(ss_["pt_bench"])
            if (a_ in xi_) != (b_ in xi_):                                  # starter <-> substitute
                s_, n_ = (a_, b_) if a_ in xi_ else (b_, a_)
                new_xi = [n_ if i_ == s_ else i_ for i_ in xi_]
                return (new_xi, [s_ if i_ == n_ else i_ for i_ in bench_]) if _xi_ok(new_xi) else None
            if a_ in bench_ and b_ in bench_ and _et(a_) != 1 and _et(b_) != 1:   # reorder the outfield bench
                ia_, ib_ = bench_.index(a_), bench_.index(b_)
                bench_[ia_], bench_[ib_] = bench_[ib_], bench_[ia_]
                return xi_, bench_
            return None

        def _pt_tap(pid_):
            sel_ = ss_.get("pt_sel")
            if sel_ is None or sel_ not in ss_["pt_squad"]:
                ss_["pt_sel"] = pid_
            elif sel_ == pid_:
                ss_["pt_sel"] = None
            else:
                res_ = _pt_switch_result(sel_, pid_)
                if res_:
                    ss_["pt_xi"], ss_["pt_bench"] = res_
                    ss_["pt_sel"] = None
                else:
                    ss_["pt_sel"] = pid_

        def _pt_transfer(out_, in_):
            ss_["pt_squad"] = [in_ if i_ == out_ else i_ for i_ in ss_["pt_squad"]]
            ss_["pt_xi"] = [in_ if i_ == out_ else i_ for i_ in ss_["pt_xi"]]
            ss_["pt_bench"] = [in_ if i_ == out_ else i_ for i_ in ss_["pt_bench"]]
            ss_["pt_sel"] = None

        # keep the saved team consistent with the current data (players can leave the game, data refreshes)
        sq_now = [int(i_) for i_ in dict.fromkeys(ss_.get("pt_squad", [])) if i_ in fd_.index]
        if not sq_now and my_sq:
            _pt_set_squad(my_sq)
        elif sq_now != ss_.get("pt_squad"):
            _pt_set_squad(sq_now)
        xi_now = [i_ for i_ in ss_.get("pt_xi", []) if i_ in ss_["pt_squad"]]
        bench_now = [i_ for i_ in ss_.get("pt_bench", []) if i_ in ss_["pt_squad"] and i_ not in xi_now]
        bench_now += [i_ for i_ in ss_["pt_squad"] if i_ not in xi_now and i_ not in bench_now]
        bench_now = [i_ for i_ in bench_now if _et(i_) == 1] + [i_ for i_ in bench_now if _et(i_) != 1]   # keeper first, as in FPL
        ss_["pt_xi"], ss_["pt_bench"] = xi_now, bench_now
        if not _xi_ok(xi_now) and len(ss_["pt_squad"]) >= 11:
            _pt_best_xi()
        if ss_.get("pt_metric_last") != ss_["pt_metric"]:        # a new rating re-picks the XI by that rating
            ss_["pt_metric_last"] = ss_["pt_metric"]
            _pt_best_xi()

        t1_, t2_, t3_, t4_ = st.columns([2.2, 1, 1, 1])
        t1_.selectbox("Rate players by", list(PT_METRIC), key="pt_metric",
                      help="Card numbers show this rating, and the XI is re-picked by it. Projected points always count expected points.")
        t2_.button("My squad", on_click=_pt_set_squad, args=(my_sq,), disabled=not my_sq, use_container_width=True,
                   help="Reload your real squad and pick its best XI.")
        t3_.button("Best XI", on_click=_pt_best_xi, use_container_width=True, help="Pick the highest-rated legal XI from this squad.")
        t4_.button("Best squad", on_click=_pt_best_squad, use_container_width=True,
                   help="Build the highest-rated legal 15 (2 GK, 5 DEF, 5 MID, 3 FWD) from your squad plus free agents.")

        xi_ids, bench_ids = ss_["pt_xi"], ss_["pt_bench"]
        if len(ss_["pt_squad"]) < 15:
            st.warning(f"Your squad has {len(ss_['pt_squad'])} players. Press **Best squad** or use Transfers below to fill it.")
        if xi_ids:
            xi_df, bench_df = fd_.loc[xi_ids], fd_.loc[bench_ids]
            base_p, sub_p, sd_p = team_projection(xi_df, bench_df)
            m1_, m2_, m3_, m4_ = st.columns(4)
            m1_.metric("Projected points", f"{base_p + sub_p:.1f}", f"XI {base_p:.1f} + bench {sub_p:.1f}", delta_color="off")
            m2_.metric("Formation", "-".join(str(sum(_et(i_) == e_ for i_ in xi_ids)) for e_ in (2, 3, 4)))
            _mu = {"AER1": "pts", "Theta": "pts vs repl.", "HaulPts": "pts", "DPS": "index", "GreenScore": "0–100"}.get(mcol, "")
            m3_.metric(f"Team {ss_['pt_metric'].split(' (')[0]} ({_mu})", f"{sum(max(_val(i_), 0) for i_ in xi_ids):.1f}")
            th_ = globals().get("theirs")
            if isinstance(th_, dict):
                sdd_ = float(np.sqrt(sd_p ** 2 + th_["sd"] ** 2)) or 1.0
                m4_.metric(f"Win chance vs {th_['name']}", f"{_phi((base_p + sub_p - th_['total']) / sdd_) * 100:.0f}%",
                           f"{base_p + sub_p - th_['total']:+.1f} pts", delta_color="off")
        sel_ = ss_.get("pt_sel")
        if sel_ is not None and sel_ in fd_.index:
            st.info(f"**{fd_.at[sel_, 'web_name']}** selected: press **Switch** under the player to swap with, or **Cancel**.")
            st.markdown(big_card_html(sel_, fd_, ctx["h"], fx, selected_gw, code), unsafe_allow_html=True)
        else:
            st.caption("Press **Select** under a player, then **Switch** under who to swap him with. Only legal formations are offered.")

        def _card_btn(pid_, col_):
            r_ = fd_.loc[pid_]
            col_.markdown(_card(r_, "AER1"), unsafe_allow_html=True)
            if sel_ is None:
                lab_, kind_, dis_ = "Select", "secondary", False
            elif sel_ == pid_:
                lab_, kind_, dis_ = "Cancel", "primary", False
            else:
                ok_ = _pt_switch_result(sel_, pid_) is not None
                lab_, kind_, dis_ = ("Switch", "primary", False) if ok_ else ("–", "secondary", True)
            col_.button(lab_, key=f"pt_btn_{pid_}", on_click=_pt_tap, args=(pid_,), type=kind_, disabled=dis_, use_container_width=True)

        def _row(ids_, slots_=5):
            if not ids_:
                return
            pad_ = max(slots_ - len(ids_), 0) / 2 + 0.01
            cols_ = st.columns([pad_] + [1] * len(ids_) + [pad_])
            for k_, pid_ in enumerate(ids_):
                _card_btn(pid_, cols_[k_ + 1])

        with _keyed_container("pt_pitch"):
            for e_ in (1, 2, 3, 4):
                _row(sorted([i_ for i_ in xi_ids if _et(i_) == e_], key=_val, reverse=True))
        with _keyed_container("pt_bench"):
            st.caption("Substitutes · GK, then 1st, 2nd, 3rd sub")
            _row(bench_ids, 5)
        st.caption("FPL Draft doesn't let other apps change your real lineup, so copy this into the FPL Draft app before the deadline.")

        st.markdown("#### Transfers")
        if ss_["pt_squad"]:
            o1_, o2_ = st.columns([2, 3])
            out_pick = o1_.selectbox("Player out", ss_["pt_squad"], key="pt_out",
                                     format_func=lambda i_: f"{fd_.at[i_, 'web_name']} ({fd_.at[i_, 'team_code']}, {fd_.at[i_, 'position']})")
            pool_mode = o2_.radio("Look at", ["Free agents", "All players"], key="pt_pool", horizontal=True)
            e_o = _et(out_pick)
            cands = [i_ for i_ in fd_.index if _et(i_) == e_o and i_ not in ss_["pt_squad"] and fd_.at[i_, "n_fix"] > 0
                     and (pool_mode == "All players" or i_ not in owned_pt)]
            cands = sorted(cands, key=_val, reverse=True)[:10]
            if not cands:
                st.info("No available players at that position.")
            for c_ in cands:
                r_ = fd_.loc[c_]
                cc1, cc2 = st.columns([5, 1])
                cc1.markdown(f"**{r_['web_name']}** · {r_['team_code']} · {r_['Opponent']} — {ss_['pt_metric'].split(' (')[0]} **{_val(c_):.2f}** · "
                             f"AER {r_['AER1']:.2f}" + (f" · WPA {r_['WPA'] * 100:+.1f}%" if pd.notna(r_.get("WPA")) else ""))
                cc2.button("Bring in", key=f"pt_in_{c_}", on_click=_pt_transfer, args=(int(out_pick), int(c_)), use_container_width=True)


# =========================================================
# LEAGUE — head-to-head table, this gameweek's fixtures and your results
# =========================================================
if page == "League":
    lg_l = load_league(int(league_id))
    ents_l, mt_l = lg_l["entries"], lg_l["matches"]
    st.subheader("League")
    if ents_l.empty or mt_l.empty or not {"league_entry_1", "league_entry_2"} <= set(mt_l.columns):
        st.info("No head-to-head data for this league (it may use classic scoring).")
    else:
        nm_l = dict(zip(ents_l["id"].astype(int), ents_l["entry_name"]))
        mg_l = {int(r_["id"]): f"{r_.get('player_first_name', '')} {r_.get('player_last_name', '')}".strip() for _, r_ in ents_l.iterrows()}
        fin_l = mt_l[mt_l["finished"].astype(bool)] if "finished" in mt_l.columns else mt_l.iloc[0:0]
        rows_l = []
        for le_ in nm_l:
            a_ = fin_l[fin_l["league_entry_1"] == le_]
            b_ = fin_l[fin_l["league_entry_2"] == le_]
            pf_ = list(a_["league_entry_1_points"]) + list(b_["league_entry_2_points"])
            pa_ = list(a_["league_entry_2_points"]) + list(b_["league_entry_1_points"])
            w_ = sum(x_ > y_ for x_, y_ in zip(pf_, pa_))
            d_ = sum(x_ == y_ for x_, y_ in zip(pf_, pa_))
            rows_l.append({"Team": nm_l[le_], "Manager": mg_l.get(le_, ""), "P": len(pf_), "W": w_, "D": d_, "L": len(pf_) - w_ - d_,
                           "PF": int(sum(pf_)), "PA": int(sum(pa_)), "Pts": 3 * w_ + d_, "_me": le_ == my_entry_sel})
        tab_l = pd.DataFrame(rows_l).sort_values(["Pts", "PF"], ascending=False).reset_index(drop=True)
        tab_l.insert(0, "#", np.arange(1, len(tab_l) + 1))
        me_rows = tab_l["_me"].to_numpy()
        st.dataframe(tab_l.drop(columns="_me").style.apply(
            lambda r_: ["background-color: rgba(150,60,255,.22); font-weight: 700" if me_rows[r_.name] else "" for _ in r_], axis=1),
            use_container_width=True, hide_index=True)
        gw_m = mt_l[mt_l["event"] == selected_gw]
        st.markdown(f"**Gameweek {selected_gw} fixtures**")
        for _, r_ in gw_m.iterrows():
            h_, a_ = nm_l.get(int(r_["league_entry_1"]), "?"), nm_l.get(int(r_["league_entry_2"]), "?")
            score_ = (f"{int(r_['league_entry_1_points'])} – {int(r_['league_entry_2_points'])}" if bool(r_.get("finished", False)) else "v")
            mine_ = my_entry_sel in (int(r_["league_entry_1"]), int(r_["league_entry_2"]))
            st.markdown(f"{'**' if mine_ else ''}{h_}  {score_}  {a_}{'**' if mine_ else ''}")
        if my_entry_sel is not None and len(fin_l):
            mine_l = fin_l[(fin_l["league_entry_1"] == my_entry_sel) | (fin_l["league_entry_2"] == my_entry_sel)].sort_values("event", ascending=False)
            res_ = []
            for _, r_ in mine_l.iterrows():
                me1_ = int(r_["league_entry_1"]) == my_entry_sel
                mp_, op_ = (r_["league_entry_1_points"], r_["league_entry_2_points"]) if me1_ else (r_["league_entry_2_points"], r_["league_entry_1_points"])
                res_.append({"GW": int(r_["event"]), "Opponent": nm_l.get(int(r_["league_entry_2"] if me1_ else r_["league_entry_1"]), "?"),
                             "Score": f"{int(mp_)} – {int(op_)}", "Result": "W" if mp_ > op_ else ("D" if mp_ == op_ else "L")})
            st.markdown("**Your results**")
            st.dataframe(pd.DataFrame(res_), use_container_width=True, hide_index=True)

# =========================================================
# PLAYER TRENDS — weekly evolution and season profile for selected players
# =========================================================
TREND_METRICS = {"FPL points": "total_points", "Minutes": "minutes", "Goals": "goals_scored", "Assists": "assists", "xG": "expected_goals",
                 "xA": "expected_assists", "xGI": "expected_goal_involvements", "Bonus": "bonus", "BPS": "bps", "Threat": "threat",
                 "Creativity": "creativity", "Influence": "influence", "ICT index": "ict_index", "DEFCON actions": "defensive_contribution",
                 "Clearances/blocks/interceptions": "clearances_blocks_interceptions", "Tackles": "tackles", "Recoveries": "recoveries",
                 "Saves": "saves", "xG conceded": "expected_goals_conceded", "Yellow cards": "yellow_cards"}
PCT_METRICS = {"Points/90": "total_points", "xG/90": "expected_goals", "xA/90": "expected_assists", "Threat/90": "threat",
               "Creativity/90": "creativity", "Influence/90": "influence", "BPS/90": "bps", "DEFCON/90": "defensive_contribution"}


def _chart(data, x, y, color, height=240):
    try:
        st.line_chart(data, x=x, y=y, color=color, height=height)
    except TypeError:
        st.line_chart(data.pivot_table(index=x, columns=color, values=y))


if page == "Players":
    st.subheader("Player cards")
    if full_df is not None and len(full_df):
        _pc_opts = full_df.sort_values("AER1", ascending=False).index.tolist()
        st.session_state.setdefault("tr_cards", _pc_opts[:2])
        _pc_sel = st.multiselect("Players", _pc_opts, key="tr_cards", max_selections=3,
                                 format_func=lambda i_: f"{full_df.at[i_, 'web_name']} ({full_df.at[i_, 'team_code']}, {full_df.at[i_, 'position']})")
        if _pc_sel:
            _pcc = st.columns(len(_pc_sel))
            for _k, _pid in enumerate(_pc_sel):
                _pcc[_k].markdown(big_card_html(_pid, full_df, ctx["h"], fx, selected_gw, code), unsafe_allow_html=True)
            st.caption("Top: last three gameweeks' points, then the next three fixtures (green = easy, red = hard). Purple: season totals. "
                       "Bottom: this gameweek's forecast. AER = expected points; Starts = chance of playing 60+ minutes; Haul chance = chance of a "
                       "top-20% week for his position; Points if he hauls = what he typically scores in those weeks.")
    st.subheader("Player trends")
    h_all = ctx["h"]
    fin_gws = sorted(int(g_) for g_ in ctx["fin"]["event"].unique())
    pl_idx = players.set_index("id")
    lab_tr = {int(i_): f"{r_['web_name']} ({r_['team_code']}, {r_['position']})" for i_, r_ in pl_idx.iterrows()}
    default_tr = [int(i_) for i_ in (df.index[:2] if full_df is not None and len(df) else [])]
    st.session_state.setdefault("tr_players", default_tr)
    st.session_state.setdefault("tr_metrics", ["Minutes", "xG", "xA", "BPS", "Threat", "Creativity", "DEFCON actions"])
    order_tr = sorted(lab_tr, key=lambda i_: -float(pd.to_numeric(pl_idx.loc[i_, "total_points"], errors="coerce") or 0)
                      if "total_points" in pl_idx.columns else 0)
    sel_tr = st.multiselect("Players", order_tr, key="tr_players", format_func=lambda i_: lab_tr.get(i_, str(i_)), max_selections=6,
                            help=HELP["tr_players"])
    c_a, c_b = st.columns([3, 2])
    met_tr = c_a.multiselect("Metrics", list(TREND_METRICS), key="tr_metrics", help=HELP["tr_metrics"])
    view_tr = c_b.radio("Show", ["Per gameweek", "3-GW rolling average", "Season cumulative", "Per 90 minutes (3-GW rolling)"],
                        key="tr_view", help=HELP["tr_view"])
    inc_last = st.checkbox("Include last season", value=False, key="tr_last", help=HELP["tr_last"], disabled=not seasons)
    if not sel_tr:
        st.info("Pick one or more players above.")
    else:
        # weekly rows: this season (+ last season, matched by name, at GW - 38)
        cols_tr = sorted(set(TREND_METRICS.values()))
        frames_tr = []
        for pid_ in sel_tr:
            hh_ = h_all[h_all["id"] == pid_].set_index("round")[cols_tr].reindex(fin_gws)
            frames_tr.append(hh_.assign(Player=lab_tr[pid_].split(" (")[0], id=pid_, GW=hh_.index))
        if inc_last and seasons:
            try:
                ctx_l, pl_l, _ = past_context(seasons[0])
                key_l = pl_l.assign(k=(pl_l["first_name"].map(_norm) + " " + pl_l["second_name"].map(_norm)))
                gws_l = sorted(int(g_) for g_ in ctx_l["fin"]["event"].unique())
                for pid_ in sel_tr:
                    r_ = pl_idx.loc[pid_]
                    k_ = (_norm(r_["first_name"]).split() or [""])[0] + " " + (_norm(r_["second_name"]).split() or [""])[-1]
                    hit = key_l[key_l["k"] == k_]
                    if len(hit) == 1:
                        hl_ = ctx_l["h"][ctx_l["h"]["id"] == int(hit["id"].iloc[0])].set_index("round")[cols_tr].reindex(gws_l)
                        frames_tr.append(hl_.assign(Player=lab_tr[pid_].split(" (")[0], id=pid_, GW=hl_.index - 38))
            except Exception as e_:
                st.caption(f"Couldn't load last season ({e_}).")
        wk = pd.concat(frames_tr, ignore_index=True).sort_values(["Player", "GW"])

        def transform(col):
            g_ = wk.groupby("Player")
            if view_tr.startswith("3-GW"):
                return g_[col].transform(lambda s_: s_.rolling(3, min_periods=1).mean())
            if view_tr.startswith("Season"):
                return g_[col].transform(lambda s_: s_.fillna(0).cumsum())
            if view_tr.startswith("Per 90"):
                if col == "minutes":
                    return g_[col].transform(lambda s_: s_.rolling(3, min_periods=1).mean())
                num_ = g_[col].transform(lambda s_: s_.fillna(0).rolling(3, min_periods=1).sum())
                den_ = g_["minutes"].transform(lambda s_: s_.fillna(0).rolling(3, min_periods=1).sum())
                return (num_ / den_.replace(0, np.nan) * 90).round(3)
            return wk[col]

        # 1) points: actual vs the model's forecast made before each gameweek, plus the projection ahead
        st.markdown("**FPL points — actual vs forecast**")
        pts_rows = [wk[["Player", "GW"]].assign(Series="Actual", value=transform("total_points"))]
        if len(wf_all):
            a_tr = best_alpha(wf_all)
            wf_t = wf_all[(wf_all["GW"] >= 1) & wf_all["id"].isin(sel_tr)]
            if len(wf_t):
                fc_ = wf_t.assign(value=np.clip(wf_t["xPts"] + a_tr * wf_t["adj"], 0, None),
                                  Player=wf_t["id"].map(lambda i_: lab_tr[int(i_)].split(" (")[0]), Series="Forecast")
                pts_rows.append(fc_[["Player", "GW", "Series", "value"]])
        for g_, s_ in aer_by_gw.items():
            if g_ >= next_gw:
                pts_rows.append(pd.DataFrame({"Player": [lab_tr[i_].split(" (")[0] for i_ in sel_tr], "GW": g_, "Series": "Projection",
                                              "value": [float(s_.get(i_, np.nan)) for i_ in sel_tr]}))
        pts_long = pd.concat(pts_rows, ignore_index=True)
        pts_long["Line"] = pts_long["Player"] + " · " + pts_long["Series"]
        _chart(pts_long.dropna(subset=["value"]), "GW", "value", "Line", 300)
        st.caption("Actual = points scored (transformed by the 'Show' option). Forecast = what the model predicted before each finished gameweek, "
                   "using only earlier weeks. Projection = the current forecast for upcoming gameweeks. GW 0 and below = last season.")

        # 2) small multiples for the chosen metrics
        grid_ = st.columns(2)
        for k_, m_ in enumerate(met_tr):
            with grid_[k_ % 2]:
                st.markdown(f"**{m_}**" + ("" if view_tr == "Per gameweek" else f" · {view_tr.lower()}"))
                d_ = wk[["Player", "GW"]].assign(value=transform(TREND_METRICS[m_]))
                _chart(d_.dropna(subset=["value"]), "GW", "value", "Player", 220)

        # 3) season profile
        st.markdown("**Season profile (this season)**")
        hs_ = h_all[h_all["id"].isin(sel_tr)]
        agg_ = hs_.groupby("id").agg(Apps=("minutes", lambda s_: int((s_ > 0).sum())), Starts=("starts", "sum"), Minutes=("minutes", "sum"),
                                     Points=("total_points", "sum"), Goals=("goals_scored", "sum"), Assists=("assists", "sum"),
                                     xG=("expected_goals", "sum"), xA=("expected_assists", "sum"), Bonus=("bonus", "sum"),
                                     BPS=("bps", "sum"), DEFCON=("defensive_contribution", "sum"), Saves=("saves", "sum"))
        n90_ = (agg_["Minutes"] / 90).replace(0, np.nan)
        prof = pd.DataFrame({"Player": agg_.index.map(lambda i_: lab_tr[int(i_)]), "Apps": agg_["Apps"], "Starts": agg_["Starts"],
                             "Minutes": agg_["Minutes"], "Points": agg_["Points"], "Pts/app": (agg_["Points"] / agg_["Apps"].replace(0, np.nan)).round(2),
                             "Goals": agg_["Goals"], "xG": agg_["xG"].round(2), "Goals − xG": (agg_["Goals"] - agg_["xG"]).round(2),
                             "Assists": agg_["Assists"], "xA": agg_["xA"].round(2), "Assists − xA": (agg_["Assists"] - agg_["xA"]).round(2),
                             "xGI/90": ((agg_["xG"] + agg_["xA"]) / n90_).round(2), "Bonus": agg_["Bonus"], "BPS/90": (agg_["BPS"] / n90_).round(1),
                             "DEFCON/90": (agg_["DEFCON"] / n90_).round(1), "Saves/90": (agg_["Saves"] / n90_).round(2)})
        if len(wf_all):
            wf_s = wf_all[(wf_all["GW"] >= 1) & wf_all["id"].isin(sel_tr)]
            if len(wf_s):
                e_ = np.clip(wf_s["xPts"] + best_alpha(wf_all) * wf_s["adj"], 0, None) - wf_s["actual"]
                prof["Forecast bias"] = wf_s.assign(e=e_).groupby("id")["e"].mean().reindex(prof.index).round(2)
                prof["Forecast miss"] = wf_s.assign(e=e_.abs()).groupby("id")["e"].mean().reindex(prof.index).round(2)
        if aer_by_gw:
            g0_ = next(iter(aer_by_gw))
            prof[f"AER GW{g0_}"] = aer_by_gw[g0_].reindex(prof.index).round(2)
        st.dataframe(prof.reset_index(drop=True), use_container_width=True, hide_index=True)
        st.caption("Goals − xG and Assists − xA above 0 = finishing or luck running hot (it tends to cool). Forecast bias above 0 = the model "
                   "over-rated him; Forecast miss = average absolute error per gameweek.")

        # 4) percentile within position, per 90 (players with 270+ minutes)
        st.markdown("**Per-90 percentile within position** (vs players with 270+ minutes this season)")
        tot_ = h_all.groupby("id")[list(set(PCT_METRICS.values())) + ["minutes"]].sum()
        tot_ = tot_[tot_["minutes"] >= 270].join(pl_idx["element_type"])
        pct_rows = []
        for pid_ in sel_tr:
            if pid_ not in tot_.index:
                continue
            peers = tot_[tot_["element_type"] == tot_.loc[pid_, "element_type"]]
            for lab_, col_ in PCT_METRICS.items():
                r90 = peers[col_] / peers["minutes"] * 90
                pct_rows.append({"Metric": lab_, "Player": lab_tr[pid_].split(" (")[0], "Percentile": float((r90 <= r90.loc[pid_]).mean() * 100)})
        if pct_rows:
            pct_df = pd.DataFrame(pct_rows)
            try:
                st.bar_chart(pct_df, x="Metric", y="Percentile", color="Player", horizontal=True, stack=False, height=360)
            except TypeError:
                st.dataframe(pct_df.pivot_table(index="Metric", columns="Player", values="Percentile").round(0), use_container_width=True)
            st.caption("100 = best in his position this season; 50 = average. Players under 270 minutes are left out.")
        else:
            st.caption("Selected players haven't reached 270 minutes yet.")

# =========================================================
# NEWS — latest articles, photos and team news for every club, tagged by club and player
# =========================================================
BBC_SLUG = {"ARS": "arsenal", "AVL": "aston-villa", "BOU": "bournemouth", "BRE": "brentford", "BHA": "brighton-and-hove-albion",
            "BUR": "burnley", "CHE": "chelsea", "CRY": "crystal-palace", "EVE": "everton", "FUL": "fulham", "LEE": "leeds-united",
            "LIV": "liverpool", "MCI": "manchester-city", "MUN": "manchester-united", "NEW": "newcastle-united", "NFO": "nottingham-forest",
            "SUN": "sunderland", "TOT": "tottenham-hotspur", "WHU": "west-ham-united", "WOL": "wolverhampton-wanderers",
            "LEI": "leicester-city", "IPS": "ipswich-town", "SOU": "southampton", "SHU": "sheffield-united", "LUT": "luton-town",
            "COV": "coventry-city", "MID": "middlesbrough", "NOR": "norwich-city", "WBA": "west-bromwich-albion", "WAT": "watford",
            "HUL": "hull-city", "STK": "stoke-city", "BIR": "birmingham-city", "WRE": "wrexham"}
CLUB_NICK = {"MCI": ["Manchester City", "Man City"], "MUN": ["Manchester United", "Man Utd", "Man United"], "TOT": ["Tottenham", "Spurs"],
             "WOL": ["Wolves", "Wolverhampton"], "NFO": ["Nottingham Forest", "Nott'm Forest"], "NEW": ["Newcastle"], "WHU": ["West Ham"],
             "BHA": ["Brighton"], "AVL": ["Aston Villa"], "CRY": ["Crystal Palace"], "LEE": ["Leeds"], "SHU": ["Sheffield United", "Blades"],
             "WBA": ["West Brom"], "LEI": ["Leicester"], "IPS": ["Ipswich"], "SOU": ["Southampton"], "BOU": ["Bournemouth"]}
NEWS_IMPACT = {r"ruled out|out for|sidelined|surgery|ACL|fracture|broken": 3, r"injur|hamstring|knee|ankle|groin|calf|muscle|knock": 2,
               r"doubt|doubtful|fitness|50-50|late test|assess": 2, r"suspend|suspension|\bban\b|red card": 3,
               r"sack|sacked|dismissed|leaves club|appointed|new manager|head coach|interim": 3, r"illness|ill\b|virus|bereavement": 2,
               r"return|returns|back in training|fit again|available|recovered|boost": 2, r"team news|press conference|line-?up|starting XI": 2,
               r"rotation|rested|rest\b|benched|dropped": 2, r"crisis|turmoil|unrest|row|charged|investigation|points deduction": 2}
_MRSS = "{http://search.yahoo.com/mrss/}"


def _clean_html(s, n=240):
    s = _html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def _rss(url, source, club=""):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (fpl-draft-aer news reader)"}, timeout=15)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
    except Exception:
        return []
    out = []
    for it in root.iter("item"):
        title = _clean_html(it.findtext("title"), 300)
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        img = None
        for tag in (f"{_MRSS}thumbnail", f"{_MRSS}content", "enclosure"):
            for el in it.findall(tag):
                u_ = el.get("url")
                if u_ and not str(el.get("type", "image")).startswith(("audio", "video")):
                    img = u_
        try:
            when = pd.Timestamp(parsedate_to_datetime(it.findtext("pubDate"))).tz_convert("UTC")
        except Exception:
            when = pd.NaT
        src_ = (it.findtext("source") or source).strip()
        if source == "Google News" and " - " in title:
            title, src_ = title.rsplit(" - ", 1)[0], title.rsplit(" - ", 1)[1]
        out.append({"Title": title, "Link": link, "Source": src_, "When": when, "Summary": _clean_html(it.findtext("description")),
                    "Image": img, "feed_club": club})
    return out


@st.cache_data(ttl=900, show_spinner="Fetching the latest football news…")
def fetch_news(clubs: tuple, club_names: tuple, days: int):
    """BBC Sport club feeds (with photos), BBC + Guardian Premier League feeds, and a Google News search per club. Cached for 15 minutes."""
    jobs = [("https://feeds.bbci.co.uk/sport/football/premier-league/rss.xml", "BBC Sport", ""),
            ("https://www.theguardian.com/football/premierleague/rss", "The Guardian", "")]
    for c_, n_ in zip(clubs, club_names):
        if c_ in BBC_SLUG:
            jobs.append((f"https://feeds.bbci.co.uk/sport/football/teams/{BBC_SLUG[c_]}/rss.xml", "BBC Sport", c_))
        jobs.append((f"https://news.google.com/rss/search?q={quote_plus(chr(34) + n_ + chr(34) + ' football when:' + str(days) + 'd')}"
                     "&hl=en-GB&gl=GB&ceid=GB:en", "Google News", c_))
    with ThreadPoolExecutor(max_workers=10) as ex:
        rows = [r_ for res in ex.map(lambda j: _rss(*j), jobs) for r_ in res]
    if not rows:
        return pd.DataFrame(columns=["Title", "Link", "Source", "When", "Summary", "Image", "feed_club"])
    d = pd.DataFrame(rows)
    d["key"] = (d["Title"].str.replace(r"\s+[-–|]\s+[^-–|]{2,40}$", "", regex=True)          # same story syndicated with " - Source" suffix
                .str.lower().str.replace(r"[^a-z0-9 ]", "", regex=True).str.slice(0, 80))
    d = d.sort_values("Image", na_position="last").drop_duplicates("key").drop(columns="key")
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    return d[d["When"].isna() | (d["When"] >= cutoff)].sort_values("When", ascending=False, na_position="last").reset_index(drop=True)


def tag_news(d, teams_df, players_df):
    """Clubs and players each story mentions, plus a 0-10 'selection impact' score from injury / suspension / manager / team-news wording."""
    if d.empty:
        return d.assign(Clubs="", Players="", Impact=0)
    alias = {}
    for _, t_ in teams_df.iterrows():
        sc = str(t_["short_name"])
        alias[sc] = sorted({str(t_["name"])} | set(CLUB_NICK.get(sc, [])), key=len, reverse=True)
    pats = {sc: re.compile(r"\b(" + "|".join(re.escape(a_) for a_ in al) + r")\b", re.I) for sc, al in alias.items()}
    pl = players_df[(pd.to_numeric(players_df["minutes"], errors="coerce").fillna(0) >= 90)
                    & (players_df["web_name"].astype(str).str.len() >= 4)][["id", "web_name", "team_code"]]
    pl_pats = [(r_["web_name"], r_["team_code"], re.compile(r"\b" + re.escape(str(r_["web_name"])) + r"\b")) for _, r_ in pl.iterrows()]
    imp = [(re.compile(k_, re.I), w_) for k_, w_ in NEWS_IMPACT.items()]
    clubs_l, players_l, impact_l = [], [], []
    for _, r_ in d.iterrows():
        txt = f"{r_['Title']} {r_['Summary']}"
        cl = {sc for sc, p_ in pats.items() if p_.search(txt)}
        if r_.get("feed_club"):
            cl.add(r_["feed_club"])
        ps = [nm for nm, tc, p_ in pl_pats if tc in cl and p_.search(txt)]
        clubs_l.append(", ".join(sorted(cl)))
        players_l.append(", ".join(dict.fromkeys(ps)))
        impact_l.append(min(10, sum(w_ for p_, w_ in imp if p_.search(txt))))
    return d.assign(Clubs=clubs_l, Players=players_l, Impact=impact_l)


def _ago(ts):
    if pd.isna(ts):
        return ""
    m_ = (pd.Timestamp.now(tz="UTC") - ts).total_seconds() / 60
    return f"{m_:.0f} min ago" if m_ < 90 else (f"{m_ / 60:.0f} h ago" if m_ < 60 * 36 else f"{m_ / 1440:.0f} days ago")


news_df = pd.DataFrame()
if page == "News":
    st.subheader(f"News that can move GW{selected_gw}")
    fxs_n = fx[fx["event"] == selected_gw]
    playing = sorted({int(x_) for x_ in pd.concat([fxs_n["team_h"], fxs_n["team_a"]])}) if len(fxs_n) else sorted(teams["id"].astype(int))
    sn_map = dict(zip(teams["id"].astype(int), teams["short_name"]))
    nm_map = dict(zip(teams["id"].astype(int), teams["name"]))
    st.session_state.setdefault("news_clubs", playing)
    n1, n2, n3 = st.columns([4, 1, 1])
    clubs_sel = n1.multiselect("Clubs", sorted(teams["id"].astype(int), key=lambda i_: nm_map[i_]), key="news_clubs",
                               format_func=lambda i_: nm_map.get(i_, str(i_)), help=HELP["news_clubs"])
    days_n = n2.slider("Days back", 1, 7, 3, key="news_days", help=HELP["news_days"])
    max_n = n3.slider("Stories to show", 6, 90, 30, 6, key="news_max", help=HELP["news_max"])
    n4, n5 = st.columns([1, 3])
    impact_only = n4.checkbox("Only selection-relevant news", value=False, key="news_impact", help=HELP["news_impact"])
    search_n = n5.text_input("Search", "", key="news_search", help=HELP["news_search"])
    if st.button(":material/refresh: Refresh news", help="Fetches the feeds again now (otherwise they refresh every 15 minutes)."):
        fetch_news.clear()

    # FPL's own availability news (the most reliable source for selection)
    inj = players[players["news"].astype(str).str.len() > 0].copy() if "news" in players.columns else pd.DataFrame()
    if len(inj):
        inj = inj[inj["team"].astype(int).isin(clubs_sel or playing)]
        inj["Added"] = pd.to_datetime(inj.get("news_added"), errors="coerce", utc=True)
        inj = inj.sort_values("Added", ascending=False)
        with st.expander(f":material/healing: FPL availability news ({len(inj)} players)", expanded=True):
            st.dataframe(pd.DataFrame({"Player": inj["web_name"], "Team": inj["team_code"], "Pos": inj["position"],
                                       "Chance %": pd.to_numeric(inj["chance_of_playing_next_round"], errors="coerce"),
                                       "News": inj["news"], "Updated": inj["Added"].map(_ago)}),
                         use_container_width=True, hide_index=True, height=min(420, 38 + 35 * len(inj)))

    if clubs_sel:
        try:
            news_df = tag_news(fetch_news(tuple(sn_map[c_] for c_ in clubs_sel), tuple(nm_map[c_] for c_ in clubs_sel), int(days_n)),
                               teams, players)
        except Exception as e_:
            st.warning(f"Couldn't fetch news right now ({e_}).")
            news_df = pd.DataFrame()
    show_n = news_df.copy()
    if len(show_n):
        sel_codes = {sn_map[c_] for c_ in clubs_sel}
        show_n = show_n[show_n["Clubs"].apply(lambda s_: bool(set(filter(None, s_.split(", "))) & sel_codes))]
        if impact_only:
            show_n = show_n[show_n["Impact"] >= 2]
        if search_n.strip():
            q_n = search_n.strip().lower()
            show_n = show_n[(show_n["Title"] + " " + show_n["Summary"] + " " + show_n["Players"]).str.lower().str.contains(re.escape(q_n))]
    if not len(show_n):
        st.info("No stories match. Widen the clubs, days or search, or press Refresh news.")
    else:
        big = show_n[show_n["Impact"] >= 4].head(6)
        if len(big):
            st.markdown("#### 🔥 Biggest selection news")
            for _, r_ in big.iterrows():
                st.markdown(f"**[{r_['Title']}]({r_['Link']})** · {r_['Source']} · {_ago(r_['When'])} · "
                            f"{r_['Clubs']}{' · ' + r_['Players'] if r_['Players'] else ''}")
        st.markdown(f"#### Latest stories ({min(len(show_n), max_n)} of {len(show_n)})")
        grid_n = st.columns(3)
        for k_, (_, r_) in enumerate(show_n.head(max_n).iterrows()):
            with grid_n[k_ % 3]:
                if isinstance(r_["Image"], str) and r_["Image"]:
                    st.image(r_["Image"], use_container_width=True)
                badge = "🔴 " if r_["Impact"] >= 4 else ("🟠 " if r_["Impact"] >= 2 else "")
                st.markdown(f"{badge}**[{r_['Title']}]({r_['Link']})**  \n{r_['Source']} · {_ago(r_['When'])}"
                            + (f"  \n🏷 {r_['Clubs']}" if r_["Clubs"] else "") + (f" · 👤 {r_['Players']}" if r_["Players"] else ""))
                if r_["Summary"]:
                    st.caption(r_["Summary"])
        st.caption("Headlines, short summaries and photos come from the publishers' public RSS feeds (BBC Sport, The Guardian, Google News); "
                   "click a headline to read the full article on its site. 🔴 = big selection news, 🟠 = may affect selection. "
                   "The 🤖 Ask AI tab reads these stories too.")


# =========================================================
# LIVE CAMS — public webcams nearest each club's stadium
# =========================================================
# (stadium, latitude, longitude) by FPL short name; clubs not listed are located with OpenStreetMap's free geocoder
STADIUMS = {
    "ARS": ("Emirates Stadium", 51.5549, -0.1084), "AVL": ("Villa Park", 52.5092, -1.8847), "BOU": ("Vitality Stadium", 50.7352, -1.8384),
    "BRE": ("Gtech Community Stadium", 51.4907, -0.2889), "BHA": ("Amex Stadium", 50.8616, -0.0837), "BUR": ("Turf Moor", 53.7890, -2.2302),
    "CHE": ("Stamford Bridge", 51.4817, -0.1910), "CRY": ("Selhurst Park", 51.3983, -0.0855), "EVE": ("Hill Dickinson Stadium", 53.4246, -3.0012),
    "FUL": ("Craven Cottage", 51.4749, -0.2217), "LEE": ("Elland Road", 53.7778, -1.5722), "LIV": ("Anfield", 53.4308, -2.9608),
    "MCI": ("Etihad Stadium", 53.4831, -2.2004), "MUN": ("Old Trafford", 53.4631, -2.2913), "NEW": ("St James' Park", 54.9756, -1.6217),
    "NFO": ("City Ground", 52.9400, -1.1328), "SUN": ("Stadium of Light", 54.9146, -1.3884), "TOT": ("Tottenham Hotspur Stadium", 51.6043, -0.0664),
    "WHU": ("London Stadium", 51.5387, -0.0166), "WOL": ("Molineux", 52.5902, -2.1304), "LEI": ("King Power Stadium", 52.6204, -1.1422),
    "IPS": ("Portman Road", 52.0545, 1.1447), "SOU": ("St Mary's Stadium", 50.9058, -1.3911), "SHU": ("Bramall Lane", 53.3703, -1.4709),
    "LUT": ("Kenilworth Road", 51.8843, -0.4317), "COV": ("Coventry Building Society Arena", 52.4481, -1.4956),
    "MID": ("Riverside Stadium", 54.5782, -1.2170), "NOR": ("Carrow Road", 52.6221, 1.3091), "WBA": ("The Hawthorns", 52.5090, -1.9639),
    "WAT": ("Vicarage Road", 51.6498, -0.4015), "HUL": ("MKM Stadium", 53.7461, -0.3677), "STK": ("bet365 Stadium", 52.9884, -2.1755),
    "BIR": ("St Andrew's", 52.4757, -1.8682), "WRE": ("Racecourse Ground", 53.0518, -3.0040), "QPR": ("Loftus Road", 51.5093, -0.2322),
    "MIL": ("The Den", 51.4859, -0.0508), "CAR": ("Cardiff City Stadium", 51.4728, -3.2030), "SWA": ("Swansea.com Stadium", 51.6428, -3.9351),
}


CITY = {"ARS": "London", "BRE": "London", "CHE": "London", "CRY": "London", "FUL": "London", "TOT": "London", "WHU": "London",
        "QPR": "London", "MIL": "London", "WAT": "Watford", "AVL": "Birmingham", "BIR": "Birmingham", "WBA": "West Bromwich",
        "WOL": "Wolverhampton", "BOU": "Bournemouth", "BHA": "Brighton", "BUR": "Burnley", "EVE": "Liverpool", "LIV": "Liverpool",
        "LEE": "Leeds", "MCI": "Manchester", "MUN": "Manchester", "NEW": "Newcastle", "SUN": "Sunderland", "NFO": "Nottingham",
        "LEI": "Leicester", "IPS": "Ipswich", "SOU": "Southampton", "SHU": "Sheffield", "LUT": "Luton", "COV": "Coventry",
        "MID": "Middlesbrough", "NOR": "Norwich", "HUL": "Hull", "STK": "Stoke-on-Trent", "WRE": "Wrexham", "CAR": "Cardiff", "SWA": "Swansea"}


@st.cache_data(ttl=900, show_spinner=False)
def yt_live_search(key: str, lat=None, lon=None, radius_km=10.0, q: str = "", max_results: int = 15):
    """YouTube streams that are live right now (and embeddable), optionally within radius_km of a point. Search costs 100 quota units,
    so results are cached for 15 minutes. Geotagged streams get their coordinates from videos.list (1 unit)."""
    params = {"part": "snippet", "type": "video", "eventType": "live", "videoEmbeddable": "true", "maxResults": max_results, "key": key.strip()}
    if lat is not None:
        params.update(location=f"{lat:.5f},{lon:.5f}", locationRadius=f"{int(min(max(radius_km, 1), 1000))}km")
    if q:
        params["q"] = q
    r = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=20)
    if r.status_code != 200:
        try:
            err = r.json().get("error", {})
            msg = err.get("message", r.text[:200])
            reason = ((err.get("errors") or [{}])[0]).get("reason", "")
        except Exception:
            msg, reason = r.text[:200], ""
        if reason == "keyInvalid" or "API key not valid" in msg:
            k_ = key.strip()
            shape = "" if (k_.startswith("AIza") and len(k_) == 39) else (
                f" The value pasted doesn't look like a Google API key (those start with 'AIza' and are 39 characters; this one is {len(k_)}).")
            raise RuntimeError("YouTube doesn't recognise this API key." + shape + " In console.cloud.google.com → APIs & Services → "
                               "Credentials, click 'Show key' on an API key and copy it whole (not a client ID or secret). A brand-new key can "
                               "take a few minutes to start working.")
        if reason in ("accessNotConfigured", "SERVICE_DISABLED") or "has not been used" in msg or "is disabled" in msg:
            raise RuntimeError("This key's project doesn't have YouTube Data API v3 switched on. In console.cloud.google.com → APIs & Services → "
                               "Library, search 'YouTube Data API v3', open it and click Enable (same project as the key), wait a minute, retry.")
        if reason in ("quotaExceeded", "dailyLimitExceeded"):
            raise RuntimeError("YouTube's free daily quota is used up; it resets at midnight Pacific time.")
        raise RuntimeError(f"YouTube API error {r.status_code}: {msg}")
    items = r.json().get("items", []) or []
    ids = [it["id"]["videoId"] for it in items if (it.get("id") or {}).get("videoId")]
    rows = {i_: {"id": i_, "Camera": "", "Channel": "", "lat": np.nan, "lon": np.nan} for i_ in ids}
    for it in items:
        v_ = (it.get("id") or {}).get("videoId")
        if v_ in rows:
            sn = it.get("snippet") or {}
            rows[v_].update(Camera=_html.unescape(sn.get("title", "Live stream")), Channel=sn.get("channelTitle", ""))
    if ids:
        try:
            r2 = requests.get("https://www.googleapis.com/youtube/v3/videos",
                              params={"part": "recordingDetails", "id": ",".join(ids), "key": key.strip()}, timeout=20)
            for it in (r2.json().get("items", []) or []) if r2.status_code == 200 else []:
                loc = ((it.get("recordingDetails") or {}).get("location")) or {}
                if it.get("id") in rows and loc.get("latitude") is not None:
                    rows[it["id"]].update(lat=float(loc["latitude"]), lon=float(loc["longitude"]))
        except Exception:
            pass
    return pd.DataFrame(list(rows.values()))


def find_live_streams(key, short, lat, lon, radius_km, n):
    """Nearest live streams: webcam-type streams geotagged within the radius, widening to 25 km and 75 km if fewer than n are found,
    then city-wide webcam streams. Every result was live when found."""
    found, notes = [], []
    cam_q = "webcam|live cam|cctv|camera|street"
    for rad in sorted({float(radius_km), 25.0, 75.0}):
        if rad < radius_km:
            continue
        try:
            df_ = yt_live_search(key, lat, lon, rad, cam_q)
        except Exception as e_:
            notes.append(str(e_))
            break
        if len(df_):
            found.append(df_.assign(Where="geotagged"))
        if sum(len(f_) for f_ in found) >= n:
            break
    if sum(len(f_) for f_ in found) < n and not notes:
        city = CITY.get(short, "")
        if city:
            try:
                df_ = yt_live_search(key, None, None, 0, f"{city} live webcam")
                if len(df_):
                    found.append(df_.assign(Where=f"{city} city-wide"))
            except Exception as e_:
                notes.append(str(e_))
    if not found:
        return pd.DataFrame(), notes
    out = pd.concat(found, ignore_index=True).drop_duplicates("id")
    out["km"] = _haversine_km(lat, lon, out["lat"].astype(float), out["lon"].astype(float))
    return out.sort_values("km", na_position="last").head(n), notes


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, float)) for x in (lat1, lon1, lat2, lon2))
    a_ = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a_))


@st.cache_data(ttl=30 * 86400, show_spinner=False)
def geocode_stadium(team_name: str):
    """Fallback for clubs not in STADIUMS: OpenStreetMap Nominatim (free; one request, cached for a month)."""
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search", params={"q": f"{team_name} football stadium England", "format": "json", "limit": 1},
                         headers={"User-Agent": "fpl-draft-aer/1.0 (personal use)"}, timeout=15)
        js = r.json()
        if js:
            return js[0].get("display_name", team_name).split(",")[0], float(js[0]["lat"]), float(js[0]["lon"])
    except Exception:
        pass
    return None


@st.cache_data(ttl=300, show_spinner=False)
def load_tfl_cams():
    """Transport for London JamCams: public traffic cameras (free, no key). Each has a still image and a short video clip, refreshed every few minutes."""
    r = requests.get("https://api.tfl.gov.uk/Place/Type/JamCam", timeout=30)
    r.raise_for_status()
    rows = []
    for c in r.json():
        props = {p_.get("key"): p_.get("value") for p_ in c.get("additionalProperties", []) or []}
        if str(props.get("available", "true")).lower() == "false":
            continue
        rows.append({"id": c.get("id"), "Camera": c.get("commonName", "TfL camera"), "lat": c.get("lat"), "lon": c.get("lon"),
                     "image": props.get("imageUrl"), "video": props.get("videoUrl"), "Source": "TfL JamCams"})
    return pd.DataFrame(rows).dropna(subset=["lat", "lon"])


@st.cache_data(ttl=480, show_spinner=False)
def windy_nearby(key: str, lat: float, lon: float, radius_km: float, limit: int = 10):
    """Windy Webcams API v3: public webcams within radius_km. Free-tier image links expire after 10 minutes, so results are cached for 8."""
    r = requests.get("https://api.windy.com/webcams/api/v3/webcams",
                     params={"nearby": f"{lat:.5f},{lon:.5f},{max(1, int(np.ceil(radius_km)))}", "limit": limit,
                             "include": "images,location,player,urls"},
                     headers={"x-windy-api-key": key.strip()}, timeout=30)
    if r.status_code == 401:
        raise RuntimeError("Windy says the key isn't valid (create one at api.windy.com/keys).")
    r.raise_for_status()
    rows = []
    for w in r.json().get("webcams", []) or []:
        loc, img, pl = w.get("location") or {}, (w.get("images") or {}).get("current") or {}, w.get("player") or {}
        rows.append({"id": w.get("webcamId"), "Camera": w.get("title", "Webcam"), "lat": loc.get("latitude"), "lon": loc.get("longitude"),
                     "image": img.get("preview") or img.get("thumbnail"), "video": None,
                     "player": pl.get("live") or pl.get("day") or (pl.get("lifetime") if isinstance(pl.get("lifetime"), str) else None),
                     "Source": "Windy webcams", "status": w.get("status")})
    return pd.DataFrame(rows).dropna(subset=["lat", "lon"]) if rows else pd.DataFrame()


if page == "Live cams":
    st.subheader("Live cams near the grounds")
    st.caption("Public webcams nearest each club's stadium: London traffic cameras from Transport for London (free, no key) and public webcams "
               "worldwide from Windy (free key). These are publicly published street, traffic and city cameras near the ground, not club CCTV. "
               "Most update every few minutes rather than streaming continuously; each card shows how far the camera is from the stadium.")
    team_rows = teams.sort_values("name")
    opts_c = [int(t_) for t_ in team_rows["id"]]
    names_c = dict(zip(team_rows["id"].astype(int), team_rows["name"]))
    shorts_c = dict(zip(team_rows["id"].astype(int), team_rows["short_name"]))
    if "cam_team" not in st.session_state and opts_c:
        pref_ = [t_ for t_ in opts_c if names_c[t_] == selected_team]
        st.session_state["cam_team"] = pref_[0] if pref_ else opts_c[0]
    k1c, k2c, k3c, k4c = st.columns([3, 2, 2, 2])
    cam_team = k1c.selectbox("Club", opts_c, format_func=lambda t_: names_c.get(t_, str(t_)), key="cam_team", help=HELP["cam_team"])
    cam_radius = k2c.slider("Search radius (km)", 0.5, 10.0, 3.0, 0.5, key="cam_radius", help=HELP["cam_radius"])
    cam_n = k3c.slider("Cameras to show", 1, 8, 4, key="cam_n", help=HELP["cam_n"])
    cam_auto = k4c.checkbox("Auto-refresh every minute", value=False, key="cam_auto", help=HELP["cam_auto"])
    kk1, kk2 = st.columns(2)
    yt_key = kk1.text_input("YouTube live key (free — needed for live video)", type="password", key="yt_key", help=HELP["yt_key"]).strip() \
        or _secret("YOUTUBE_API_KEY")
    windy_key = kk2.text_input("Windy webcams key (free, optional — adds snapshot cameras outside London)", type="password", key="windy_key",
                               help=HELP["windy_key"]).strip() or _secret("WINDY_API_KEY")
    if (yt_key and not st.session_state.get("yt_key")) or (windy_key and not st.session_state.get("windy_key")):
        st.caption("Using the app's built-in camera keys.")

    sc_ = shorts_c.get(cam_team, "")
    stad = STADIUMS.get(sc_) or geocode_stadium(names_c.get(cam_team, ""))
    if not stad:
        st.warning("Couldn't locate this club's stadium.")
    else:
        s_name, s_lat, s_lon = stad
        frames_c, notes_c = [], []
        try:
            tfl_ = load_tfl_cams()
            if len(tfl_):
                frames_c.append(tfl_)
        except Exception as e_:
            notes_c.append(f"TfL cameras unavailable right now ({e_}).")
        if str(windy_key).strip():
            try:
                wd_ = windy_nearby(str(windy_key), s_lat, s_lon, cam_radius)
                if len(wd_):
                    frames_c.append(wd_)
            except Exception as e_:
                notes_c.append(f"Windy webcams unavailable ({e_}).")
        cams = pd.concat(frames_c, ignore_index=True) if frames_c else pd.DataFrame(columns=["Camera", "lat", "lon", "Source"])
        if len(cams):
            cams["km"] = _haversine_km(s_lat, s_lon, cams["lat"].astype(float), cams["lon"].astype(float))
            cams = cams[cams["km"] <= cam_radius].sort_values("km").head(cam_n)
        for n_ in notes_c:
            st.caption("⚠️ " + n_)

        map_df = pd.DataFrame({"lat": [s_lat] + list(cams["lat"].astype(float)), "lon": [s_lon] + list(cams["lon"].astype(float)),
                               "color": ["#c23b2a"] + ["#1f5fbf"] * len(cams), "size": [90] + [45] * len(cams)})
        st.markdown(f"**{s_name}** ({names_c.get(cam_team)}) · red = stadium, blue = cameras")
        try:
            st.map(map_df, latitude="lat", longitude="lon", color="color", size="size", zoom=14)
        except TypeError:
            st.map(map_df)

        # ---- live video first ----
        st.markdown("### 🔴 Live now")
        if not str(yt_key).strip():
            st.info("Live video needs a free YouTube Data API key (no card): console.cloud.google.com → create a project → APIs & Services → "
                    "enable **YouTube Data API v3** → Credentials → **Create API key**, then paste it above. Until then, only the "
                    "every-few-minutes cameras below are shown.")
        else:
            live_, lnotes = find_live_streams(str(yt_key), sc_, s_lat, s_lon, cam_radius, cam_n)
            for n_ in lnotes:
                st.caption("⚠️ " + n_)
            if live_.empty:
                st.warning("No live streams found near this ground right now. The cameras below update every few minutes.")
            else:
                if st.button(":material/refresh: Search again", help="Clears the 15-minute cache and searches YouTube again (uses free quota)."):
                    yt_live_search.clear()
                    _rerun()
                grid_l = st.columns(2)
                for k_, (_, c_) in enumerate(live_.iterrows()):
                    with grid_l[k_ % 2]:
                        if pd.notna(c_["km"]):
                            yd_ = c_["km"] * 1093.6
                            dist_ = f"{yd_:,.0f} yards" if yd_ < 1760 else f"{c_['km'] * 0.6214:.1f} miles"
                            where_ = f"{dist_} from {s_name}"
                        else:
                            where_ = f"{c_['Where']} (exact location not published)"
                        st.markdown(f"**🔴 {c_['Camera']}**  \n{where_} · {c_['Channel']}")
                        import streamlit.components.v1 as components
                        components.iframe(f"https://www.youtube.com/embed/{c_['id']}?autoplay=1&mute=1&playsinline=1", height=260)
                st.caption("Streams that were live on YouTube when searched (refreshed every 15 minutes), nearest first. They play continuously; "
                           "if one ends, press 'Search for live streams again'. Distance is only known for streams that publish a location.")
        st.markdown("### 🕒 Near the ground (updated every few minutes)")
        if cams.empty:
            london = s_lat > 51.28 and s_lat < 51.70 and s_lon > -0.51 and s_lon < 0.33
            st.info(f"No public cameras found within {cam_radius:g} km of {s_name}. "
                    + ("Try a bigger radius." if london else
                       "Outside London, add a free Windy key above (api.windy.com/keys) and/or widen the radius."))
        else:
            def show_cams():
                stamp = int(time.time() // 60)
                grid_c = st.columns(2)
                for k_, (_, c_) in enumerate(cams.iterrows()):
                    with grid_c[k_ % 2]:
                        yards = c_["km"] * 1093.6
                        dist = f"{yards:,.0f} yards" if yards < 1760 else f"{c_['km'] * 0.6214:.1f} miles"
                        st.markdown(f"**{c_['Camera']}**  \n{dist} from {s_name} · {c_['Source']}")
                        if isinstance(c_.get("video"), str) and c_["video"]:
                            st.video(c_["video"])
                        elif isinstance(c_.get("player"), str) and c_["player"]:
                            import streamlit.components.v1 as components
                            components.iframe(c_["player"], height=280)
                        if isinstance(c_.get("image"), str) and c_["image"]:
                            sep_ = "&" if "?" in c_["image"] else "?"
                            img_url = c_["image"] if c_["Source"].startswith("Windy") else f"{c_['image']}{sep_}t={stamp}"
                            st.image(img_url, use_container_width=True)
                st.caption(f"Loaded {time.strftime('%H:%M')}. TfL images and clips are replaced every few minutes; Windy cameras are periodic "
                           "snapshots or timelapses. " + ("Webcams provided by windy.com." if any(cams["Source"].str.startswith("Windy")) else ""))

            if cam_auto and hasattr(st, "fragment"):
                st.fragment(run_every=60)(show_cams)()
            else:
                if st.button(":material/refresh: Refresh cameras"):
                    load_tfl_cams.clear()
                show_cams()


# =========================================================
# MATCHUPS & ODDS
# =========================================================

# =========================================================
# SHOT MAPS — where a player shoots from vs where his opponent concedes shots (Understat, free)
# =========================================================
US_BASE = "https://understat.com"
US_RESULT = {"Goal": "Goal", "SavedShot": "Saved", "MissedShots": "Missed", "BlockedShot": "Blocked", "ShotOnPost": "Post", "OwnGoal": "Own goal"}


def understat_season():
    t = pd.Timestamp.today()
    return str(t.year if t.month >= 7 else t.year - 1)


def _us_get(path, api=False):
    h_ = {"User-Agent": "Mozilla/5.0 (fpl-hog-statistician; personal use)"}
    if api:
        h_.update({"X-Requested-With": "XMLHttpRequest", "Referer": US_BASE + "/"})
    r = requests.get(US_BASE + path, headers=h_, timeout=20)
    r.raise_for_status()
    return r


def _us_var(html_text, var):
    """Data Understat embeds in its pages as  var NAME = JSON.parse('...escaped...')."""
    m = re.search(var + r"\s*=\s*JSON\.parse\('(.*?)'\)", html_text, re.S)
    if not m:
        return None
    return json.loads(m.group(1).encode("utf-8").decode("unicode_escape"))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def us_league_matches(season):
    """Every Premier League match this season with Understat's match id (fixture list endpoint, falling back to the league page)."""
    dates = None
    try:
        dates = _us_get(f"/getLeagueData/EPL/{season}", api=True).json().get("dates")
    except Exception:
        dates = None
    if not dates:
        dates = _us_var(_us_get(f"/league/EPL/{season}").text, "datesData")
    rows = [{"id": int(d_["id"]), "played": bool(d_.get("isResult")), "home": d_["h"]["title"], "away": d_["a"]["title"],
             "when": pd.to_datetime(d_.get("datetime"), errors="coerce")} for d_ in (dates or [])]
    return pd.DataFrame(rows)


@st.cache_data(persist="disk", max_entries=800, show_spinner=False)
def us_match_shots(match_id):
    """All shots in one finished match (they never change, so they are kept on disk)."""
    data = None
    try:
        data = _us_var(_us_get(f"/match/{int(match_id)}").text, "shotsData")
    except Exception:
        data = None
    if not data:
        try:
            js = _us_get(f"/getMatchData/{int(match_id)}", api=True).json()
            data = js.get("shots") or js.get("shotsData")
        except Exception:
            data = None
    if not data:
        return pd.DataFrame()
    rows = []
    for side_ in ("h", "a"):
        for s_ in data.get(side_, []) or []:
            rows.append({"match": int(match_id), "side": side_, "team": s_.get("h_team") if side_ == "h" else s_.get("a_team"),
                         "opp": s_.get("a_team") if side_ == "h" else s_.get("h_team"), "player": s_.get("player"),
                         "X": float(s_.get("X", 0)), "Y": float(s_.get("Y", 0)), "xG": float(s_.get("xG", 0)),
                         "result": US_RESULT.get(s_.get("result"), s_.get("result")), "situation": s_.get("situation"),
                         "minute": int(float(s_.get("minute", 0) or 0))})
    return pd.DataFrame(rows)


def us_team_title(team_id, matches):
    """Understat's name for an FPL team (matched with the same fuzzy team matcher used for bookmaker odds)."""
    for t_ in sorted(set(matches["home"]) | set(matches["away"])):
        if match_team(t_, teams) == int(team_id):
            return t_
    return None


def us_team_shots(title, matches, last_n):
    """Shots by and against a team in its last `last_n` finished matches."""
    m_ = matches[matches["played"] & ((matches["home"] == title) | (matches["away"] == title))].sort_values("when").tail(last_n)
    if m_.empty:
        return pd.DataFrame(), 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        parts = [p_ for p_ in ex.map(us_match_shots, m_["id"].tolist()) if len(p_)]
    if not parts:
        return pd.DataFrame(), 0
    d_ = pd.concat(parts, ignore_index=True)
    return d_[(d_["team"] == title) | (d_["opp"] == title)], len(m_)


US_COL = {"Goal": "#00C46A", "Saved": "#2F80ED", "Post": "#9B51E0", "Blocked": "#F2994A", "Missed": "#BDBDBD", "Own goal": "#111111"}


def shot_zone(d_):
    """6 x 6 grid over the attacking half: across the pitch (6) by distance from goal in 8.75 m bands (6)."""
    return (np.floor(d_["Y"] * 6).clip(0, 5).astype(int).astype(str) + "-" + np.floor((1 - d_["X"]) * 12).clip(0, 5).astype(int).astype(str))


def zone_weakness(con, base):
    """Per zone: the share of a team's conceded xG that comes from it, divided by the share typical teams concede there (`base` = every
    shot in the matches loaded). Above 1 = they concede more there than usual. Shares, not totals, so it isn't just 'the six-yard box'."""
    if con is None or not len(con) or base is None or not len(base):
        return pd.Series(dtype=float)
    c_ = con.assign(z=shot_zone(con)).groupby("z")["xG"].sum()
    b_ = base.assign(z=shot_zone(base)).groupby("z")["xG"].sum()
    cs, bs = c_ / max(c_.sum(), 1e-9), b_ / max(b_.sum(), 1e-9)
    k_ = 0.02                                           # small-sample guard: zones with little baseline xG can't look extreme
    return ((cs + k_ * bs.mean()) / (bs + k_ * bs.mean())).reindex(bs.index).fillna(0.0)


def shot_map_svg(att, con, att_label, con_label, n_con, base=None):
    """Attacking half of a pitch drawn directly as SVG (goal at the top): red zones = where `con_label` concede xG per match (6 x 6 zones),
    dots = `att_label` shots (area ~ xG, colour = outcome). Understat gives every shot from the shooting team's view, so both line up."""
    W, H = 68.0, 52.5
    parts = [f"<rect x='0' y='0' width='{W}' height='{H}' fill='#e8f5ec'/>"]
    weak_ = zone_weakness(con, base) if base is not None else pd.Series(dtype=float)
    if len(weak_):
        for z_, r_ in weak_.items():
            if r_ <= 1.0:
                continue
            i_, j_ = (int(x_) for x_ in z_.split("-"))
            parts.append(f"<rect x='{i_ * W / 6:.2f}' y='{j_ * 8.75:.2f}' width='{W / 6:.2f}' height='8.75' fill='#E90052' "
                         f"fill-opacity='{min(0.12 + 0.5 * (r_ - 1), 0.62):.2f}'><title>{_html.escape(con_label)} concede {r_:.1f}× the usual share "
                         f"of their xG here</title></rect>")
    elif con is not None and len(con) and n_con:
        c_ = con.assign(px=con["Y"] * W, py=(1 - con["X"]) * 105.0)
        c_ = c_[c_["py"] <= H]
        bx, by = np.floor(c_["px"] / (W / 6)).clip(0, 5), np.floor(c_["py"] / (H / 6)).clip(0, 5)
        z_ = c_.assign(bx=bx, by=by).groupby(["bx", "by"])["xG"].sum() / n_con
        zmax = float(z_.max()) if len(z_) else 1.0
        for (i_, j_), v_ in z_.items():
            parts.append(f"<rect x='{i_ * W / 6:.2f}' y='{j_ * H / 6:.2f}' width='{W / 6:.2f}' height='{H / 6:.2f}' fill='#E90052' "
                         f"fill-opacity='{0.08 + 0.62 * v_ / max(zmax, 1e-9):.2f}'><title>{v_:.2f} xG conceded per match</title></rect>")
    ln = "fill='none' stroke='#2d6a3e' stroke-width='0.35'"
    parts += [f"<rect x='0' y='0' width='{W}' height='{H}' {ln}/>", f"<rect x='{(W - 40.32) / 2}' y='0' width='40.32' height='16.5' {ln}/>",
              f"<rect x='{(W - 18.32) / 2}' y='0' width='18.32' height='5.5' {ln}/>",
              f"<line x1='{(W - 7.32) / 2}' y1='0' x2='{(W + 7.32) / 2}' y2='0' stroke='#2d6a3e' stroke-width='1.2'/>",
              f"<circle cx='{W / 2}' cy='11' r='0.35' fill='#2d6a3e'/>",
              f"<path d='M {W / 2 - 7.3} 16.5 A 9.15 9.15 0 0 0 {W / 2 + 7.3} 16.5' {ln}/>",
              f"<path d='M {W / 2 - 9.15} {H} A 9.15 9.15 0 0 1 {W / 2 + 9.15} {H}' {ln}/>"]
    if att is not None and len(att):
        a_ = att.assign(px=att["Y"] * W, py=(1 - att["X"]) * 105.0)
        a_ = a_[a_["py"] <= H].assign(_g=lambda d: (d["result"] == "Goal").astype(int)).sort_values(["_g", "xG"], ascending=[True, False])
        for _, r_ in a_.iterrows():
            rad = 0.45 + 1.9 * np.sqrt(max(float(r_["xG"]), 0.0))
            parts.append(f"<circle cx='{r_['px']:.2f}' cy='{r_['py']:.2f}' r='{rad:.2f}' fill='{US_COL.get(r_['result'], '#999')}' fill-opacity='0.72' "
                         f"stroke='#37003C' stroke-width='0.25'><title>{_html.escape(str(r_['player']))} · {r_['minute']}' · {r_['result']} · "
                         f"{float(r_['xG']):.2f} xG</title></circle>")
    legend = " ".join(f"<span style='display:inline-flex;align-items:center;gap:4px;margin-right:10px'><span style='width:10px;height:10px;"
                      f"border-radius:50%;background:{c_}'></span>{k_}</span>" for k_, c_ in US_COL.items() if k_ != "Own goal")
    heat = (f"<span style='display:inline-flex;align-items:center;gap:4px'><span style='width:12px;height:10px;background:#E90052;opacity:.55'>"
            f"</span>{_html.escape(con_label)} concede {'more than usual' if base is not None else ''}</span>" if con is not None and len(con) else "")
    return (f"<div style='max-width:520px;margin:0 auto'><svg viewBox='-1 -2 {W + 2} {H + 3}' style='width:100%;border-radius:10px'>{''.join(parts)}</svg>"
            f"<div style='font-size:11px;margin-top:4px;text-align:center'>{legend}{heat}<br>Bigger dot = better chance (higher xG). "
            f"Hover for details.</div></div>")


def xg_race_data(shots, hc, ac):
    """Cumulative expected goals by minute for each side, as a step line (Streamlit's own chart), plus the list of goals."""
    rows, goals = [], []
    for side_, lab_ in (("h", hc), ("a", ac)):
        s_ = shots[shots["side"] == side_].sort_values("minute")
        cum = 0.0
        rows.append({"minute": 0.0, "team": lab_, "xG": 0.0})
        for _, r_ in s_.iterrows():
            m_ = float(r_["minute"])
            rows.append({"minute": m_, "team": lab_, "xG": cum})
            cum += float(r_["xG"])
            rows.append({"minute": m_ + 0.01, "team": lab_, "xG": cum})
            if r_["result"] == "Goal":
                goals.append(f"{lab_} {int(m_)}' {r_['player']}")
        rows.append({"minute": max(95.0, float(shots["minute"].max()) + 1 if len(shots) else 95.0), "team": lab_, "xG": cum})
    d_ = pd.DataFrame(rows).astype({"minute": float, "xG": float, "team": str})
    return d_.pivot_table(index="minute", columns="team", values="xG", aggfunc="last").sort_index().ffill().fillna(0.0), goals


def shot_map_chart(att, con, att_label, con_label, n_con):
    """Attacking half of a pitch (goal at the top): the opponent's shots-conceded zones as a heat layer (xG conceded per match per zone),
    the chosen player's shots on top (size = xG, colour = outcome). Understat gives every shot from the shooting team's view, so the two line up."""
    import altair as alt
    L, W = 105.0, 68.0
    lines = [(0, 52.5, W, 52.5), (0, 105, W, 105), (0, 52.5, 0, 105), (W, 52.5, W, 105),
             (13.84, 88.5, 54.16, 88.5), (13.84, 88.5, 13.84, 105), (54.16, 88.5, 54.16, 105),
             (24.84, 99.5, 43.16, 99.5), (24.84, 99.5, 24.84, 105), (43.16, 99.5, 43.16, 105)]
    th_ = np.linspace(np.radians(217), np.radians(323), 30)                     # the D at the top of the box
    arc = pd.DataFrame({"x": 34 + 9.15 * np.cos(th_), "y": 94 + 9.15 * np.sin(th_)})
    arc = arc[arc["y"] <= 88.5]
    th2 = np.linspace(np.pi, 2 * np.pi, 40)                                     # centre-circle half
    circ = pd.DataFrame({"x": 34 + 9.15 * np.cos(th2), "y": 52.5 - 9.15 * np.sin(th2)})
    seg = pd.DataFrame([{"x": a_, "y": b_, "x2": c_, "y2": d_} for a_, b_, c_, d_ in lines])
    xs = alt.Scale(domain=[0, W], nice=False)
    ys = alt.Scale(domain=[52.5, L], nice=False)
    layers = []
    if con is not None and len(con) and n_con:
        c_ = con.assign(px=con["Y"] * W, py=con["X"] * L)
        c_ = c_[c_["py"] >= 52.5]
        c_["bx"] = (np.floor(c_["px"] / (W / 6)) * (W / 6)).clip(0, W - W / 6)
        c_["by"] = (np.floor((c_["py"] - 52.5) / (52.5 / 6)) * (52.5 / 6) + 52.5).clip(52.5, L - 52.5 / 6)
        z_ = c_.groupby(["bx", "by"], as_index=False)["xG"].sum()
        z_["xG per match"] = z_["xG"] / n_con
        z_["bx2"], z_["by2"] = z_["bx"] + W / 6, z_["by"] + 52.5 / 6
        layers.append(alt.Chart(z_).mark_rect(opacity=0.75).encode(
            x=alt.X("bx:Q", scale=xs, axis=None), x2="bx2:Q", y=alt.Y("by:Q", scale=ys, axis=None), y2="by2:Q",
            color=alt.Color("xG per match:Q", scale=alt.Scale(scheme="reds"), title=f"xG {con_label} concede / match"),
            tooltip=[alt.Tooltip("xG per match:Q", format=".2f")]))
    layers.append(alt.Chart(seg).mark_rule(color="#2d6a3e", strokeWidth=1.5).encode(x=alt.X("x:Q", scale=xs, axis=None), y=alt.Y("y:Q", scale=ys, axis=None),
                                                                                    x2="x2:Q", y2="y2:Q"))
    for d_ in (arc, circ):
        layers.append(alt.Chart(d_).mark_line(color="#2d6a3e", strokeWidth=1.5).encode(x=alt.X("x:Q", scale=xs, axis=None), y=alt.Y("y:Q", scale=ys, axis=None), order="x:Q"))
    if att is not None and len(att):
        a_ = att.assign(px=att["Y"] * W, py=att["X"] * L)
        a_ = a_[a_["py"] >= 52.5]
        layers.append(alt.Chart(a_).mark_circle(opacity=0.9, stroke="#37003C", strokeWidth=1).encode(
            x=alt.X("px:Q", scale=xs, axis=None), y=alt.Y("py:Q", scale=ys, axis=None),
            size=alt.Size("xG:Q", scale=alt.Scale(range=[30, 600], domain=[0, 0.8]), title="Shot xG"),
            color=alt.Color("result:N", scale=alt.Scale(domain=["Goal", "Saved", "Post", "Blocked", "Missed", "Own goal"],
                                                        range=["#00C46A", "#2F80ED", "#9B51E0", "#F2994A", "#BDBDBD", "#000000"]),
                            title=f"{att_label} shot"),
            tooltip=["player:N", "result:N", "situation:N", "minute:Q", alt.Tooltip("xG:Q", format=".2f")]))
    return alt.layer(*layers).properties(height=430, background="#e8f5ec").configure_view(stroke=None)



def _mm_cell(v):
    """Matchup cell: +30% = this opponent concedes 30% more than an average team to that position."""
    if pd.isna(v):
        return "<td>–</td>"
    d = (v - 1) * 100
    bg = "#0b6e3a" if d >= 20 else "#7cc24a" if d >= 7 else "#e9e9ef" if d > -7 else "#ee7d2a" if d > -20 else "#c23b2a"
    fg = "#fff" if bg in ("#0b6e3a", "#c23b2a") else "#1f1a2e"
    txt_ = "0%" if round(d) == 0 else f"{round(d):+.0f}%"
    return f"<td style='background:{bg};color:{fg};font-weight:700'>{txt_}</td>"


MP_CSS = """<style>
.mp{border-radius:14px;background:#fff;color:#37003C;box-shadow:0 3px 12px rgba(0,0,0,.15);padding:10px 12px;margin-bottom:12px;font-family:-apple-system,system-ui,sans-serif}
.mp-teams{display:flex;align-items:center;justify-content:space-between;gap:6px}
.mp-team{width:30%;text-align:center;font-weight:800;font-size:15px}.mp-team svg{width:40px;height:40px;display:block;margin:0 auto}
.mp-score{text-align:center;font-weight:900;font-size:26px;line-height:1}.mp-score small{display:block;font-size:10px;font-weight:600;opacity:.7}
.mp-xg{text-align:center;font-size:11.5px;margin:4px 0 6px;opacity:.85}
.mp-bar{display:flex;height:22px;border-radius:6px;overflow:hidden;font-size:11px;font-weight:800}
.mp-bar span{display:flex;align-items:center;justify-content:center;white-space:nowrap;overflow:hidden}
.mp-stats{display:flex;flex-wrap:wrap;gap:4px 10px;justify-content:center;font-size:11px;margin-top:6px}
.mp-stats b{font-weight:800}
.mm{width:100%;border-collapse:separate;border-spacing:3px;font-family:-apple-system,system-ui,sans-serif;font-size:13px}
.mm th{font-size:11px;font-weight:700;opacity:.8;text-align:center;padding:2px}.mm td{text-align:center;padding:6px 4px;border-radius:6px}
.mm td.pos{font-weight:800;text-align:left;background:transparent}
</style>"""



# =========================================================
# SIGNAL FINDER — which statistics (alone, in pairs, in small combinations) best predict the points actually scored
# =========================================================
SF_LABELS = {"aer": "AER (pts)", "dps": "DPS process points (pts)", "xPts": "Structural forecast (pts)", "xPts0": "Forecast before fitted effects (pts)",
             "form": "Form (avg pts, last 4)", "ppg": "Points per game (season)", "pts_3": "Points per game (last 3)", "pts_6": "Points per game (last 6, decayed)",
             "mins_3": "Minutes per game (last 3)", "mins_6": "Minutes per game (last 6)", "start_3": "Starts (last 3)", "start_6": "Starts (last 6)",
             "p60": "Chance of 60+ minutes", "psub": "Chance of a sub appearance", "xMins": "Expected minutes", "p60_used": "Chance of 60+ (used)",
             "xG": "Expected goals this GW", "xA": "Expected assists this GW", "g90": "Goals per 90 (xG-blended)", "a90": "Assists per 90 (xA-blended)",
             "cs_team": "Clean-sheet chance", "xGC": "Goals against expected", "Fix": "Fixture attacking ease", "FDR": "FPL fixture difficulty",
             "home": "Home", "n_fix": "Fixtures this GW", "stab_self": "Team stability", "stab_opp": "Opponent stability", "des_self": "Team stakes",
             "des_opp": "Opponent stakes", "mm_idx": "Matchup (what the opponent leaks)", "role_f": "Finisher vs creator role", "perf70": "Points in 70+ min games",
             "leader": "Leader score", "E90": "Season 90s played", "conf": "Sample size", "pts90": "Points per 90 (season)", "dc90": "DEFCON actions per 90",
             "p_goal": "Expected goal points", "p_ast": "Expected assist points", "p_cs": "Expected clean-sheet points", "p_dc": "Expected DEFCON points",
             "p_app": "Expected appearance points", "p_oth": "Expected other points (bonus, saves, cards)", "opp_att": "Opponent attack strength",
             "opp_def": "Opponent defensive weakness", "team_def": "Own defence weakness", "lam_for": "Team expected goals", "elite_rate": "Past haul rate"}


def sf_label(c):
    if c in SF_LABELS:
        return SF_LABELS[c]
    stem, _, k = c.rpartition("_")
    if k.isdigit() and stem:
        return f"{stem.replace('_', ' ').capitalize()} per 90 (last {k})"
    return c.replace("_", " ").capitalize()


def _r2(y, yh):
    y, yh = np.asarray(y, float), np.asarray(yh, float)
    v = ((y - y.mean()) ** 2).sum()
    return float(1 - ((y - yh) ** 2).sum() / v) if v > 0 else np.nan


def _lin_fit(X, y, ridge=1e-3):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    w = np.linalg.solve(Z.T @ Z + ridge * np.diag([0] + [1] * X.shape[1]), Z.T @ y)
    return lambda Xn: np.column_stack([np.ones(len(Xn)), (Xn - mu) / sd]) @ w


def _bin_fit(x, y, nb=10, k=20.0):
    """Shape fit: average outcome in each tenth of the stat, shrunk toward the overall mean (catches curves a straight line misses)."""
    edges = np.unique(np.quantile(x, np.linspace(0, 1, nb + 1)[1:-1]))
    b = np.searchsorted(edges, x)
    g = y.mean()
    s, n = np.bincount(b, y, len(edges) + 1), np.bincount(b, None, len(edges) + 1)
    means = (s + k * g) / (n + k)
    return lambda xn: means[np.searchsorted(edges, xn)]


def _bin2_fit(x1, x2, y, nb=4, k=15.0):
    e1 = np.unique(np.quantile(x1, np.linspace(0, 1, nb + 1)[1:-1]))
    e2 = np.unique(np.quantile(x2, np.linspace(0, 1, nb + 1)[1:-1]))
    n1, n2 = len(e1) + 1, len(e2) + 1
    cell = np.searchsorted(e1, x1) * n2 + np.searchsorted(e2, x2)
    g = y.mean()
    s, n = np.bincount(cell, y, n1 * n2), np.bincount(cell, None, n1 * n2)
    means = (s + k * g) / (n + k)
    return lambda a, b: means[np.searchsorted(e1, a) * n2 + np.searchsorted(e2, b)]


def _walk(df, cols, kind, gws):
    """Out-of-sample predictions: each gameweek in `gws` is predicted by a fit on all earlier gameweeks only."""
    preds, ys = [], []
    for g in gws:
        tr, te = df[df["GW"] < g], df[df["GW"] == g]
        if len(tr) < 200 or not len(te):
            continue
        Xtr, Xte, y = tr[cols].to_numpy(float), te[cols].to_numpy(float), tr["actual"].to_numpy(float)
        if kind == "linear":
            f_ = _lin_fit(Xtr, y)
            p_ = f_(Xte)
        elif kind == "shape":
            p_ = _bin_fit(Xtr[:, 0], y)(Xte[:, 0])
        elif kind == "interaction":
            add = lambda X: np.column_stack([X, X[:, 0] * X[:, 1]])
            p_ = _lin_fit(add(Xtr), y)(add(Xte))
        else:                                           # "grid": 4 x 4 cells
            p_ = _bin2_fit(Xtr[:, 0], Xtr[:, 1], y)(Xte[:, 0], Xte[:, 1])
        preds.append(p_)
        ys.append(te["actual"].to_numpy(float))
    if not preds:
        return np.nan, np.array([]), np.array([])
    yy, pp = np.concatenate(ys), np.concatenate(preds)
    return _r2(yy, pp), yy, pp


@st.cache_data(show_spinner="Searching every statistic, pair and combination (one-off, then cached)…", max_entries=6)
def signal_finder(rows, holdout=3, top_single=20, max_combo=5):
    """Discovery: every pre-gameweek statistic is scored by out-of-sample R² (each gameweek predicted from earlier ones only), as a straight
    line and as a shape (tenths). The best 20 are paired (straight line with interaction, and a 4 x 4 grid). Forward selection then builds the
    best small combination. Confirmation: the last `holdout` gameweeks are kept out of all of that, and the winners are re-scored on them."""
    gws = sorted(rows["GW"].unique())
    if len(gws) < holdout + 2:
        return None
    hold = gws[-holdout:]
    disc_gws = [g for g in gws[:-holdout] if g > gws[0]]          # each needs at least one earlier gameweek to learn from
    disc = rows[~rows["GW"].isin(hold)]
    feats = [c for c in rows.columns if c not in ("actual", "GW", "id", "played") and rows[c].std() > 1e-9]
    single = []
    for c in feats:
        r_lin, _, _ = _walk(disc, [c], "linear", disc_gws)
        r_sh, _, _ = _walk(disc, [c], "shape", disc_gws)
        single.append({"stat": c, "Straight-line R²": r_lin, "Shape R²": r_sh, "Best R²": np.nanmax([r_lin, r_sh]),
                       "Correlation": float(np.corrcoef(disc[c], disc["actual"])[0, 1])})
    single = pd.DataFrame(single).sort_values("Best R²", ascending=False).reset_index(drop=True)
    top = single["stat"].head(top_single).tolist()
    best1 = dict(zip(single["stat"], single["Best R²"]))
    pairs = []
    for i, a in enumerate(top):
        for b in top[i + 1:]:
            r_int, _, _ = _walk(disc, [a, b], "interaction", disc_gws)
            r_grid, _, _ = _walk(disc, [a, b], "grid", disc_gws)
            best_ = np.nanmax([r_int, r_grid])
            pairs.append({"stat 1": a, "stat 2": b, "Line + interaction R²": r_int, "4×4 grid R²": r_grid, "Best R²": best_,
                          "Gain over better single": best_ - max(best1.get(a, 0), best1.get(b, 0)),
                          "method": "interaction" if (r_int >= r_grid or np.isnan(r_grid)) else "grid"})
    pairs = pd.DataFrame(pairs).sort_values("Best R²", ascending=False).reset_index(drop=True)
    # forward selection
    cand = single["stat"].head(30).tolist()
    chosen, path, cur = [], [], -np.inf
    for _ in range(max_combo):
        best_c, best_r = None, cur
        for c in cand:
            if c in chosen:
                continue
            r_, _, _ = _walk(disc, chosen + [c], "linear", disc_gws)
            if r_ > best_r + 0.002:
                best_c, best_r = c, r_
        if best_c is None:
            break
        chosen.append(best_c)
        cur = best_r
        path.append({"step": len(chosen), "added": best_c, "Combination R²": best_r})
    path = pd.DataFrame(path)
    # confirmation on the held-out gameweeks (fits use every earlier gameweek, including discovery ones)

    def conf(cols, kind):
        r_, _, _ = _walk(rows, cols, kind, hold)
        return r_
    single["Confirmed R² (held-out GWs)"] = np.nan
    for i_ in range(min(len(single), 15)):
        c = single.at[i_, "stat"]
        single.at[i_, "Confirmed R² (held-out GWs)"] = conf([c], "shape" if single.at[i_, "Shape R²"] > single.at[i_, "Straight-line R²"] else "linear")
    pairs["Confirmed R² (held-out GWs)"] = np.nan
    for i_ in range(min(len(pairs), 15)):
        pairs.at[i_, "Confirmed R² (held-out GWs)"] = conf([pairs.at[i_, "stat 1"], pairs.at[i_, "stat 2"]], pairs.at[i_, "method"])
    if len(path):
        path["Confirmed R² (held-out GWs)"] = [conf(chosen[:k + 1], "linear") for k in range(len(chosen))]
    combo_w = None
    if chosen:
        X_, y_ = rows[chosen].to_numpy(float), rows["actual"].to_numpy(float)
        mu_, sd_ = X_.mean(0), X_.std(0) + 1e-9
        Z_ = np.column_stack([np.ones(len(X_)), (X_ - mu_) / sd_])
        w_ = np.linalg.lstsq(Z_, y_, rcond=None)[0]
        combo_w = pd.DataFrame({"stat": chosen, "Weight per standard deviation (pts)": w_[1:], "Weight per unit": w_[1:] / sd_})
    return {"single": single, "pairs": pairs, "path": path, "combo": combo_w, "n": len(rows), "n_hold": int(rows["GW"].isin(hold).sum()),
            "hold": hold, "disc_gws": disc_gws}


def grid_table(rows, a, b, nb=4):
    """'If stat 1 is in this range and stat 2 in that range, players averaged X points' (all rows; counts shown)."""
    qa = pd.qcut(rows[a].rank(method="first"), nb, labels=[f"Q{i + 1}" for i in range(nb)])
    qb = pd.qcut(rows[b].rank(method="first"), nb, labels=[f"Q{i + 1}" for i in range(nb)])
    g = rows.assign(qa=qa, qb=qb).groupby(["qa", "qb"], observed=True)["actual"].agg(["mean", "size"]).reset_index()
    rng_a = rows.groupby(qa, observed=True)[a].agg(["min", "max"])
    rng_b = rows.groupby(qb, observed=True)[b].agg(["min", "max"])
    return g, rng_a, rng_b



# =========================================================
# MATCH ANALYZER — one fixture: what the model expected, what the chances said, what happened, and what it meant for FPL
# =========================================================
def xg_race_chart(shots, home_t, away_t, hc, ac):
    import altair as alt
    rows = []
    for side_, tt_, lab_ in (("h", home_t, hc), ("a", away_t, ac)):
        s_ = shots[shots["side"] == side_].sort_values("minute")
        cum = 0.0
        rows.append({"minute": 0, "xG": 0.0, "team": lab_, "goal": False})
        for _, r_ in s_.iterrows():
            cum += float(r_["xG"])
            rows.append({"minute": int(r_["minute"]), "xG": cum, "team": lab_, "goal": r_["result"] == "Goal", "player": r_["player"]})
        rows.append({"minute": 95, "xG": cum, "team": lab_, "goal": False})
    d_ = pd.DataFrame(rows)
    col = alt.Color("team:N", scale=alt.Scale(domain=[hc, ac], range=["#00C46A", "#963CFF"]), title=None)
    line = alt.Chart(d_).mark_line(interpolate="step-after", strokeWidth=3).encode(
        x=alt.X("minute:Q", title="Minute", scale=alt.Scale(domain=[0, 95])), y=alt.Y("xG:Q", title="Cumulative expected goals"), color=col)
    goals = alt.Chart(d_[d_["goal"]]).mark_point(size=160, filled=True, shape="circle", stroke="#111", strokeWidth=1).encode(
        x="minute:Q", y="xG:Q", color=col, tooltip=["player:N", "minute:Q"])
    return alt.layer(line, goals).properties(height=260)


def deserved_result(shots, n=20000, seed=0):
    """Replays the match's chances n times (each shot scores with probability = its xG): how often each side wins on those chances."""
    rng = np.random.default_rng(seed)
    sh = shots[shots["result"] != "Own goal"]

    def _goals(xg_):
        xg_ = np.asarray(xg_, float)
        return (rng.random((n, len(xg_))) < xg_).sum(1) if len(xg_) else np.zeros(n)

    gh, ga = _goals(sh.loc[sh["side"] == "h", "xG"]), _goals(sh.loc[sh["side"] == "a", "xG"])
    return float((gh > ga).mean()), float((gh == ga).mean()), float((gh < ga).mean())


def sf_build_rows(pos="All positions", only_played=False):
    """Every past player-gameweek with the statistics known before it (plus AER and DPS where they exist) and the points actually scored."""
    if train_df is None or train_df.empty:
        return pd.DataFrame()
    r_ = train_df.copy()
    if len(wf_all):
        r_ = r_.merge(wf_all.assign(aer=np.clip(wf_all["xPts"] + best_alpha(wf_all) * wf_all["adj"], 0, None))[["id", "GW", "aer"]],
                      on=["id", "GW"], how="left")
    r_["aer"] = r_["aer"].fillna(r_["xPts"]) if "aer" in r_.columns else r_["xPts"]
    dv_s = globals().get("dps_val")
    if isinstance(dv_s, pd.DataFrame) and len(dv_s):
        r_ = r_.merge(dv_s[["id", "GW", "dps"]], on=["id", "GW"], how="left")
    r_["dps"] = r_["dps"].fillna(r_["xPts"]) if "dps" in r_.columns else r_["xPts"]
    mn_ = ctx["h"][["id", "round", "minutes"]].rename(columns={"round": "GW"})
    r_ = r_.merge(mn_, on=["id", "GW"], how="left")
    if only_played:
        r_ = r_[r_["minutes"].fillna(0) > 0]
    r_ = r_.drop(columns=["minutes"])
    if pos != "All positions":
        r_ = r_[r_["element_type"].isin({"Goalkeepers & defenders": [1, 2], "Midfielders": [3], "Forwards": [4]}[pos])]
    keep_ = ["id", "GW", "actual"] + [c_ for c_ in r_.select_dtypes("number").columns
                                      if c_ not in ("id", "GW", "actual", "act60", "miss_self", "miss_opp", "q10", "q90", "adj")]
    r_ = r_[list(dict.fromkeys(keep_))].replace([np.inf, -np.inf], np.nan)
    return r_.fillna(r_.median(numeric_only=True))


# =========================================================
# POINTS CALCULATOR — pick metrics like calculator keys; it fits the equation and shows the prediction with its out-of-sample R²
# =========================================================
CALC_KEYS = [("aer", "AER"), ("dps", "DPS"), ("xPts", "Structural"), ("form", "Form"), ("pts_6", "Pts L6"), ("ppg", "PPG"),
             ("mins_6", "Mins L6"), ("start_6", "Starts L6"), ("p60", "60+ %"), ("xMins", "xMins"), ("xG", "xG"), ("xA", "xA"),
             ("p_goal", "Goal pts"), ("p_ast", "Assist pts"), ("p_cs", "CS pts"), ("cs_team", "CS %"), ("p_dc", "DEFCON pts"),
             ("threat_6", "Threat"), ("creativity_6", "Creativity"), ("bps_6", "BPS"), ("bonus_6", "Bonus"), ("Fix", "Fixture"),
             ("home", "Home"), ("mm_idx", "Matchup"), ("stab_self", "Stability"), ("perf70", "Pts 70+"), ("elite_rate", "Haul rate"),
             ("expected_goal_involvements_6", "xGI")]
CALC_LIVE = {"aer": "AER1", "dps": "DPS"}           # this gameweek's value for keys whose live column has a different name


def _ridge(X, y, lam):
    """Ridge regression on standardised metrics: weights are shrunk toward 0 by `lam`, which keeps them sensible when metrics overlap or
    data is thin. Returns a predictor plus per-unit weights and constant."""
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    w = np.linalg.solve(Z.T @ Z + lam * np.diag([0] + [1] * X.shape[1]), Z.T @ y)
    per = w[1:] / sd
    const = w[0] - float((per * mu).sum())
    return (lambda Xn: const + Xn @ per), per, const


@st.cache_data(show_spinner=False, max_entries=64)
def calc_fit(rows, keys, before_gw=None):
    """Weights and constant for the chosen keys, fitted on rows before `before_gw`. Exact duplicates are dropped; the amount of shrinkage is
    chosen by out-of-sample accuracy (each gameweek predicted from earlier ones only); the R² reported is that out-of-sample figure."""
    keys = list(keys)
    fit_rows = rows[rows["GW"] < before_gw] if before_gw is not None else rows
    if len(fit_rows) < 40 or not keys:
        return None
    used, dropped = [], []
    for k_ in keys:                                     # identical or near-identical to a key already in the equation
        dup = next((u_ for u_ in used if abs(np.corrcoef(fit_rows[k_], fit_rows[u_])[0, 1]) > 0.995), None) if fit_rows[k_].std() > 1e-9 else "constant"
        (dropped.append((k_, dup)) if dup else used.append(k_))
    if not used:
        return {"used": [], "dropped": dropped}
    gws = sorted(fit_rows["GW"].unique())
    best = (np.nan, None)
    scores = {}
    for lam in (0.01, 1.0, 10.0, 100.0, 1000.0):
        if len(gws) < 2:
            break
        yy, pp = [], []
        for g in gws[1:]:
            tr, te = fit_rows[fit_rows["GW"] < g], fit_rows[fit_rows["GW"] == g]
            if len(tr) < 30 or not len(te):
                continue
            f_, _, _ = _ridge(tr[used].to_numpy(float), tr["actual"].to_numpy(float), lam)
            pp.append(f_(te[used].to_numpy(float)))
            yy.append(te["actual"].to_numpy(float))
        if pp:
            scores[lam] = _r2(np.concatenate(yy), np.concatenate(pp))
    if scores:
        lam_best = max(scores, key=scores.get)
        best = (scores[lam_best], lam_best)
    lam_use = best[1] if best[1] is not None else 10.0 * len(used)
    X, y = fit_rows[used].to_numpy(float), fit_rows["actual"].to_numpy(float)
    f_, per, const = _ridge(X, y, lam_use)
    return {"const": float(const), "w": dict(zip(used, per.astype(float))), "r2": best[0], "r2_in": _r2(y, f_(X)), "n": len(fit_rows),
            "gws": (int(min(gws)), int(max(gws))), "used": used, "dropped": dropped, "lam": lam_use}


if page == "Points calculator":
    st.subheader("Points calculator")
    rows_c = sf_build_rows()
    if rows_c.empty or full_df is None:
        st.info("Needs a few finished gameweeks of model data.")
    else:
        keys_ok = [(k_, l_) for k_, l_ in CALC_KEYS if k_ in rows_c.columns]
        ss_ = st.session_state
        ss_.setdefault("calc_sel", ["aer"])
        ss_["calc_sel"] = [k_ for k_ in ss_["calc_sel"] if k_ in dict(keys_ok)]

        def _toggle(k_):
            ss_["calc_sel"] = [x_ for x_ in ss_["calc_sel"] if x_ != k_] if k_ in ss_["calc_sel"] else ss_["calc_sel"] + [k_]

        def _clear():
            ss_["calc_sel"] = []

        def _best():
            res_ = None
            try:
                res_ = signal_finder(rows_c, max(1, min(3, int(rows_c["GW"].nunique()) - 2)))
            except Exception:
                pass
            if res_ is not None and len(res_["path"]):
                ss_["calc_sel"] = [k_ for k_ in res_["path"]["added"] if k_ in dict(keys_ok)] or ["aer"]

        st.markdown("""<style>
.st-key-calc { background: linear-gradient(180deg,#2b0030,#37003C); border-radius: 22px; padding: 16px 16px 10px; box-shadow: 0 10px 28px rgba(0,0,0,.35); }
.st-key-calc label p { color: #e9d9f2 !important; }
.calc-screen { background: #c9f7d9; color: #0b2d18; border-radius: 12px; padding: 12px 14px; margin: 6px 0 12px;
  font-family: "SF Mono", Menlo, Consolas, monospace; box-shadow: inset 0 2px 6px rgba(0,0,0,.25); }
.calc-screen .eq { font-size: 12.5px; line-height: 1.55; word-break: break-word; }
.calc-screen .res { font-size: 30px; font-weight: 800; text-align: right; margin-top: 4px; }
.calc-screen .res small { font-size: 15px; font-weight: 600; opacity: .8; }
.calc-screen .meta { font-size: 11px; opacity: .75; text-align: right; }
.st-key-calc .stButton > button { width: 100%; min-height: 2.9rem; border-radius: 12px !important; font-weight: 700 !important; }
.st-key-calc .stButton > button[kind="secondary"], .st-key-calc [data-testid="stBaseButton-secondary"] { background: #4d1556 !important;
  color: #f3e8f7 !important; border: 1px solid #7a3a85 !important; }
.st-key-calc .stButton > button[kind="secondary"] p, .st-key-calc [data-testid="stBaseButton-secondary"] p { color: #f3e8f7 !important; }
.st-key-calc .stButton > button[kind="primary"] { background: linear-gradient(90deg,#04F5FF,#00FF87) !important; color: #37003C !important; }
</style>""", unsafe_allow_html=True)
        with _keyed_container("calc"):
            # the "number bar": player and gameweek
            opts_p = full_df[full_df["E90"] > 0].sort_values("AER1", ascending=False).index.tolist() or full_df.index.tolist()
            if ss_.get("calc_player") not in opts_p:
                ss_["calc_player"] = opts_p[0]
            gw_opts = sorted({int(g_) for g_ in rows_c["GW"].unique() if g_ >= 1} | {int(selected_gw)})
            if ss_.get("calc_gw") not in gw_opts:
                ss_["calc_gw"] = int(selected_gw)
            s1_, s2_, s3_ = st.columns([3, 1.2, 1.4])
            pid_c = s1_.selectbox("Player", opts_p, key="calc_player",
                                  format_func=lambda i_: f"{full_df.at[i_, 'web_name']} ({full_df.at[i_, 'team_code']}, {full_df.at[i_, 'position']})")
            gw_c = s2_.selectbox("Gameweek", gw_opts, key="calc_gw")
            ss_.setdefault("calc_pos", True)
            by_pos = s3_.toggle("Fit on his position", key="calc_pos", help="Fit the weights on players in the same position group only.")
            et_c = int(full_df.at[pid_c, "element_type"])
            grp_ = {1: [1, 2], 2: [1, 2], 3: [3], 4: [4]}[et_c]
            rows_fit = rows_c[rows_c["element_type"].isin(grp_)] if by_pos else rows_c
            past = gw_c < int(selected_gw) and ((rows_c["GW"] == gw_c) & (rows_c["id"] == pid_c)).any()
            sel_c = ss_["calc_sel"]
            fit_c = calc_fit(rows_fit, tuple(sel_c), int(gw_c)) if sel_c else None
            # the player's values for this gameweek
            vals = {}
            if past:
                row_p = rows_c[(rows_c["GW"] == gw_c) & (rows_c["id"] == pid_c)].iloc[0]
                vals = {k_: float(row_p[k_]) for k_ in sel_c}
                actual_c = float(row_p["actual"])
            else:
                for k_ in sel_c:
                    col_ = CALC_LIVE.get(k_, k_)
                    v_ = full_df.at[pid_c, col_] if col_ in full_df.columns else np.nan
                    vals[k_] = float(v_) if pd.notna(v_) else float(rows_fit[k_].median())
                actual_c = None
            lab_ = dict(keys_ok)
            notes_ = []
            if fit_c is None or not fit_c.get("used"):
                eq_html = ("Tap metrics below to build an equation." if not sel_c else
                           "Not enough earlier gameweeks to fit this equation." if fit_c is None else "Those metrics carry no usable information yet.")
                res_html = "–"
                meta_ = ""
            else:
                used_ = fit_c["used"]
                terms = [f"{fit_c['w'][k_]:+.3f}×{lab_[k_]}<b>({vals[k_]:.2f})</b>" for k_ in used_]
                pred_c = fit_c["const"] + sum(fit_c["w"][k_] * vals[k_] for k_ in used_)
                r2_txt = f"R² {fit_c['r2']:.3f}" if pd.notna(fit_c["r2"]) else "R² needs 2+ gameweeks"
                eq_html = f"pts = {fit_c['const']:+.3f} " + " ".join(terms)
                res_html = f"{pred_c:.2f} pts <small>({r2_txt})</small>"
                meta_ = (f"{len(used_)} metric{'s' if len(used_) != 1 else ''} · fitted on {fit_c['n']:,} player-gameweeks, GW{fit_c['gws'][0]}–{fit_c['gws'][1]}"
                         + (f" · actual this GW: <b>{actual_c:.0f} pts</b>" if actual_c is not None else ""))
                if fit_c["dropped"]:
                    notes_.append("Left out (identical to another key so far): " + ", ".join(
                        f"{lab_.get(k_, k_)}" + (f" = {lab_.get(d_, d_)}" if d_ != "constant" else " (no variation)") for k_, d_ in fit_c["dropped"]))
                if fit_c["n"] / max(len(used_), 1) < 40:
                    notes_.append(f"Only about {fit_c['n'] // max(len(used_), 1)} rows per metric: with this little data, fewer metrics usually predict "
                                  "better. Weights are shrunk to keep them sensible.")
                if pd.notna(fit_c["r2"]) and fit_c["r2"] < fit_c["r2_in"] - 0.08:
                    notes_.append(f"It fits the past much better (R² {fit_c['r2_in']:.2f}) than it predicts unseen weeks ({fit_c['r2']:.2f}): "
                                  "a sign of too many metrics. Try removing some.")
            st.markdown(f"<div class='calc-screen'><div class='meta'>{_html.escape(str(full_df.at[pid_c, 'web_name']))} · GW{gw_c}"
                        f"{' (past)' if past else ''}</div><div class='eq'>{eq_html}</div><div class='res'>{res_html}</div>"
                        f"<div class='meta'>{meta_}</div>"
                        + "".join(f"<div class='meta' style='text-align:left;color:#7a2a00;opacity:.95'>⚠ {_html.escape(n_)}</div>" for n_ in notes_)
                        + "</div>", unsafe_allow_html=True)
            # the keys
            per_row = 4
            for i0 in range(0, len(keys_ok), per_row):
                cols_k = st.columns(per_row)
                for j_, (k_, l_) in enumerate(keys_ok[i0:i0 + per_row]):
                    cols_k[j_].button(("✓ " + l_) if k_ in sel_c else l_, key=f"calc_k_{k_}", on_click=_toggle, args=(k_,),
                                      type="primary" if k_ in sel_c else "secondary", use_container_width=True, help=sf_label(k_))
            b1_, b2_ = st.columns(2)
            b1_.button("C", key="calc_c", on_click=_clear, use_container_width=True, help="Clear the equation.")
            b2_.button("Best", key="calc_best", on_click=_best, use_container_width=True,
                       help="Load the combination the Signal finder found best (runs the search if needed).")
        st.caption("Green with a ✓ = in the equation. Start with two or three metrics and add more only if the R² rises. "
                   "Tap metrics to add or remove them. The weights and constant are refitted for whatever you choose, using only gameweeks before "
                   "the one shown (for a past gameweek you also see what he actually scored). The R² in brackets is out of sample: each gameweek "
                   "predicted from earlier ones only, so adding metrics only raises it if they genuinely help. Hover a key for its full name.")

if page == "Signal finder":
    st.subheader("Signal finder: what actually predicts points?")
    st.caption("Searches every statistic the model computes before a gameweek (alone, in pairs and in small combinations) for the strongest "
               "link to the points players actually scored. Every score is out of sample: each gameweek is predicted from earlier gameweeks only, "
               "and the most recent gameweeks are held back entirely and used to confirm the winners.")
    if train_df is None or train_df.empty:
        st.info("Needs a few finished gameweeks of model data.")
    else:
        f1_, f2_, f3_ = st.columns([2, 2, 1])
        pos_sf = f1_.selectbox("Players", ["All positions", "Goalkeepers & defenders", "Midfielders", "Forwards"], key="sf_pos")
        cond_sf = f2_.selectbox("Rows", ["Every player-gameweek", "Only when he played (1+ minute)"], key="sf_cond",
                                help="'Only when he played' uses hindsight (whether he played), so it measures how well stats predict points "
                                     "given that he plays. The first option includes the uncertainty about minutes.")
        _n_gw_sf = int(train_df["GW"].nunique())
        _hold_opts = [h_ for h_ in (1, 2, 3, 4, 5) if h_ <= max(_n_gw_sf - 2, 1)]
        if st.session_state.get("sf_hold") not in _hold_opts:
            st.session_state["sf_hold"] = _hold_opts[min(len(_hold_opts) - 1, 2)]
        hold_sf = f3_.selectbox("Held-out GWs", _hold_opts, key="sf_hold",
                                help="The most recent gameweeks kept out of the search and used only to confirm the winners.")
        if _n_gw_sf < 6:
            st.info(f"Only {_n_gw_sf} gameweeks of model data so far (they start at GW3), so results will be noisy. Turning on "
                    "**Learn from past seasons** (sidebar → Tune on history) adds a whole season of gameweeks to the search.")
        r_ = sf_build_rows(pos_sf, cond_sf.startswith("Only"))
        if st.button("Run the search", type="primary") or st.session_state.get("sf_ran"):
            st.session_state["sf_ran"] = True
            res = signal_finder(r_, int(hold_sf))
            if res is None:
                st.info("Not enough gameweeks yet: the search needs at least two gameweeks to learn from plus the held-out ones. "
                        "Hold out fewer, or turn on Learn from past seasons.")
            else:
                s_, p_, pa_ = res["single"], res["pairs"], res["path"]
                best_pair = p_.iloc[0] if len(p_) else None
                best_combo = pa_.iloc[-1] if len(pa_) else None
                aer_r = s_.loc[s_["stat"] == "aer", "Best R²"]
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Best single stat", sf_label(s_.at[0, "stat"]), f"R² {s_.at[0, 'Best R²']:.3f}", delta_color="off")
                if best_pair is not None:
                    c2.metric("Best pair", f"{sf_label(best_pair['stat 1'])} × {sf_label(best_pair['stat 2'])}",
                              f"R² {best_pair['Best R²']:.3f}", delta_color="off")
                if best_combo is not None:
                    c3.metric(f"Best {int(best_combo['step'])}-stat combination", f"R² {best_combo['Combination R²']:.3f}",
                              f"confirmed {best_combo['Confirmed R² (held-out GWs)']:.3f}", delta_color="off")
                if len(aer_r):
                    c4.metric("AER on its own", f"R² {float(aer_r.iloc[0]):.3f}", "for comparison", delta_color="off")
                st.caption(f"{res['n']:,} player-gameweeks · discovery on GWs {min(res['disc_gws'])}–{max(res['disc_gws'])}, confirmation on GWs "
                           f"{', '.join(str(int(g_)) for g_ in res['hold'])} ({res['n_hold']:,} rows the search never saw). R² = share of the "
                           "week-to-week differences in points it explains (0 = none, 1 = all). For single gameweeks of FPL, 0.15–0.25 is strong.")
                st.markdown("#### Strongest single statistics")
                t1 = s_.head(15).assign(Statistic=lambda d: d["stat"].map(sf_label))[["Statistic", "Straight-line R²", "Shape R²",
                                                                                     "Confirmed R² (held-out GWs)", "Correlation"]]
                st.dataframe(t1.round(3), use_container_width=True, hide_index=True)
                st.markdown("#### Strongest pairs")
                t2 = p_.head(15).assign(**{"Stat 1": lambda d: d["stat 1"].map(sf_label), "Stat 2": lambda d: d["stat 2"].map(sf_label)})[
                    ["Stat 1", "Stat 2", "Line + interaction R²", "4×4 grid R²", "Gain over better single", "Confirmed R² (held-out GWs)"]]
                st.dataframe(t2.round(3), use_container_width=True, hide_index=True)
                if best_pair is not None:
                    a_, b_ = best_pair["stat 1"], best_pair["stat 2"]
                    g_, ra_, rb_ = grid_table(r_, a_, b_)
                    piv = g_.pivot(index="qa", columns="qb", values="mean")
                    cnt = g_.pivot(index="qa", columns="qb", values="size")
                    vmin, vmax = float(np.nanmin(piv.values)), float(np.nanmax(piv.values))
                    head_ = "".join(f"<th>{sf_label(b_)}<br>{rb_.loc[q_, 'min']:.2f}–{rb_.loc[q_, 'max']:.2f}</th>" for q_ in piv.columns)
                    body_ = ""
                    for qa_ in piv.index:
                        body_ += f"<tr><th style='text-align:left'>{sf_label(a_)}<br>{ra_.loc[qa_, 'min']:.2f}–{ra_.loc[qa_, 'max']:.2f}</th>"
                        for qb_ in piv.columns:
                            v_ = piv.loc[qa_, qb_]
                            if pd.isna(v_):                     # no player-gameweeks in that combination of ranges
                                body_ += "<td style='text-align:center;opacity:.5'>–<br><small>0 rows</small></td>"
                                continue
                            x_ = (v_ - vmin) / max(vmax - vmin, 1e-9)
                            rgb_ = _interp(_VALUE_STOPS, x_)
                            fg_ = "#111" if (0.299 * rgb_[0] + 0.587 * rgb_[1] + 0.114 * rgb_[2]) / 255 > 0.6 else "#fff"
                            body_ += (f"<td style='background:{_rgb(rgb_)};color:{fg_};text-align:center;padding:8px;border-radius:6px'>"
                                      f"<b>{v_:.2f}</b><br><small>{int(cnt.loc[qa_, qb_])} rows</small></td>")
                        body_ += "</tr>"
                    st.markdown(f"#### If {sf_label(a_)} is… and {sf_label(b_)} is… → average points scored")
                    st.markdown(f"<table style='border-collapse:separate;border-spacing:3px;width:100%;font-size:13px'><tr><th></th>{head_}</tr>{body_}</table>",
                                unsafe_allow_html=True)
                    st.caption("Each cell = the average points players actually scored when both statistics were in those ranges (quarters of all "
                               "player-gameweeks). Green = high, red = low.")
                if best_combo is not None:
                    st.markdown("#### Best small combination (built one statistic at a time)")
                    st.dataframe(pa_.assign(**{"Added": pa_["added"].map(sf_label)})[["step", "Added", "Combination R²", "Confirmed R² (held-out GWs)"]].round(3),
                                 use_container_width=True, hide_index=True)
                    cw_ = res["combo"]
                    st.markdown("**The formula:** expected points ≈ " + " + ".join(
                        f"{r__['Weight per unit']:+.3f} × {sf_label(r__['stat'])}" for _, r__ in cw_.iterrows()) + " + constant")
                    st.caption("A statistic is added only if it raises the out-of-sample R² by more than 0.002. Weights are fitted on all rows; "
                               "'per unit' = points per one unit of that statistic.")
                st.caption("How to read it: discovery R² picks the winners; 'confirmed' re-scores them on gameweeks the search never saw. If a "
                           "pair or combination looks great in discovery but drops on confirmation, it found noise. The confirmed figures are the honest ones.")

if page == "Match analyzer":
    st.markdown(MP_CSS, unsafe_allow_html=True)
    st.subheader("Match analyzer")
    gws_a = sorted(int(x_) for x_ in fx["event"].unique())
    last_fin = max(started) if started else selected_gw
    st.session_state.setdefault("ma_gw", int(last_fin))
    a1_, a2_ = st.columns([1, 3])
    gw_a = a1_.selectbox("Gameweek", gws_a, key="ma_gw")
    fxa = fx[fx["event"] == gw_a]
    if fxa.empty:
        st.info("No fixtures in that gameweek.")
    else:
        def _fx_lab(fid_):
            r_ = fxa[fxa["id"] == fid_].iloc[0]
            sc_ = (f" {int(r_['team_h_score'])}–{int(r_['team_a_score'])} " if bool(r_["finished"]) and pd.notna(r_["team_h_score"]) else " v ")
            return f"{code[int(r_['team_h'])]}{sc_}{code[int(r_['team_a'])]}"
        if st.session_state.get("ma_fix") not in fxa["id"].tolist():
            st.session_state["ma_fix"] = int(fxa["id"].iloc[0])
        fid_a = a2_.selectbox("Fixture", fxa["id"].tolist(), format_func=_fx_lab, key="ma_fix")
        fa = fxa[fxa["id"] == fid_a].iloc[0]
        ht, at = int(fa["team_h"]), int(fa["team_a"])
        hc, ac = code[ht], code[at]
        done = bool(fa["finished"]) and pd.notna(fa["team_h_score"])
        # ---- 1. what the model expected (built only from matches before this gameweek) ----
        pre = fixture_predictions(ctx["long"], fxa[fxa["id"] == fid_a][["id", "team_h", "team_a"]], gw_a, p, fin=ctx["fin"])
        if len(pre):
            pr_ = pre.iloc[0]
            M_ = score_matrix(pr_["xg_h"], pr_["xg_a"])
            ph_, pd_, pa_ = pr_["p_home"] * 100, pr_["p_draw"] * 100, pr_["p_away"] * 100
            head_ = (f"{int(fa['team_h_score'])}–{int(fa['team_a_score'])}" if done else pr_["score"])
            sub_ = "final score" if done else f"predicted score · {pr_['p_score'] * 100:.0f}%"
            verdict = ""
            if done:
                hs_, as_ = int(fa["team_h_score"]), int(fa["team_a_score"])
                p_res = pr_["p_home"] if hs_ > as_ else (pr_["p_draw"] if hs_ == as_ else pr_["p_away"])
                p_ex = float(M_[hs_, as_]) if hs_ < M_.shape[0] and as_ < M_.shape[1] else 0.0
                tone = ("an expected result" if p_res >= 0.45 else "a plausible result" if p_res >= 0.25 else "an upset")
                verdict = (f"<div class='mp-xg'>Before kick-off the model gave this result <b>{p_res * 100:.0f}%</b> ({tone}) and this exact score "
                           f"<b>{p_ex * 100:.1f}%</b>. It predicted {pr_['score']}.</div>")
            st.markdown(f"<div class='mp' style='max-width:640px'><div class='mp-teams'><div class='mp-team'>{jersey_svg(hc)}{hc}</div>"
                        f"<div class='mp-score'>{head_}<small>{sub_}</small></div><div class='mp-team'>{jersey_svg(ac)}{ac}</div></div>"
                        f"<div class='mp-xg'>Pre-match expected goals <b>{pr_['xg_h']:.2f} – {pr_['xg_a']:.2f}</b></div>"
                        f"<div class='mp-bar'><span style='width:{ph_:.0f}%;background:#00C46A;color:#fff'>{hc} {ph_:.0f}%</span>"
                        f"<span style='width:{pd_:.0f}%;background:#c9c6d6;color:#37003C'>Draw {pd_:.0f}%</span>"
                        f"<span style='width:{pa_:.0f}%;background:#963CFF;color:#fff'>{ac} {pa_:.0f}%</span></div>{verdict}</div>",
                        unsafe_allow_html=True)
        if not done:
            st.info("This match hasn't been played yet, so this is a preview. Team news, matchups and shot maps are under More → Matchups.")
        else:
            # ---- 2. what the chances said (Understat shots) ----
            st.markdown("#### The chances")
            try:
                us_ma = us_league_matches(understat_season())
                th_u, ta_u = us_team_title(ht, us_ma), us_team_title(at, us_ma)
                row_u = us_ma[(us_ma["home"] == th_u) & (us_ma["away"] == ta_u)] if th_u and ta_u else pd.DataFrame()
                shots_u = us_match_shots(int(row_u["id"].iloc[0])) if len(row_u) else pd.DataFrame()
            except Exception as e_u:
                shots_u = pd.DataFrame()
                st.caption(f"Couldn't reach Understat ({type(e_u).__name__}); shot data unavailable right now.")
            if len(shots_u):
                xh_, xa_ = shots_u.loc[shots_u["side"] == "h", "xG"].sum(), shots_u.loc[shots_u["side"] == "a", "xG"].sum()
                dh_, dd_, da_ = deserved_result(shots_u)
                k1, k2, k3 = st.columns(3)
                k1.metric("Expected goals (match)", f"{xh_:.2f} – {xa_:.2f}", f"{(shots_u['side'] == 'h').sum()} – {(shots_u['side'] == 'a').sum()} shots",
                          delta_color="off")
                k2.metric(f"On these chances, {hc} win", f"{dh_ * 100:.0f}%", f"draw {dd_ * 100:.0f}% · {ac} {da_ * 100:.0f}%", delta_color="off",
                          help="Replays every shot 20,000 times, each scoring with probability equal to its xG.")
                hs_, as_ = int(fa["team_h_score"]), int(fa["team_a_score"])
                fair_ = "the result matched the chances" if (hs_ > as_) == (xh_ > xa_ + 0.3) and (hs_ < as_) == (xa_ > xh_ + 0.3) else \
                        "the finishing decided it more than the chances"
                k3.metric("Verdict", "Fair" if fair_.startswith("the result") else "Finishing", fair_, delta_color="off")
                race_, goals_ = xg_race_data(shots_u, hc, ac)
                st.markdown("**Expected-goals race** (cumulative xG by minute)")
                try:
                    st.line_chart(race_, height=260, color=["#00C46A" if c_ == hc else "#963CFF" for c_ in race_.columns])
                except Exception:
                    st.line_chart(race_, height=260)
                if goals_:
                    st.caption("Goals (from the shot data): " + " · ".join(goals_))
                m1_, m2_ = st.columns(2)
                for col_, side_, tc_, oc_ in ((m1_, "h", hc, ac), (m2_, "a", ac, hc)):
                    col_.markdown(f"**{tc_} shots**")
                    col_.markdown(shot_map_svg(shots_u[shots_u["side"] == side_], None, tc_, oc_, 0), unsafe_allow_html=True)
                st.caption("Shot data: understat.com. Dots: bigger = better chance, colour = outcome.")
            # ---- 3. the FPL side: points vs forecast, bonus race ----
            st.markdown("#### FPL points vs forecast")
            hg_ = ctx["h"][(ctx["h"]["round"] == gw_a) & (ctx["h"]["team"].isin([ht, at])) & (ctx["h"]["minutes"] > 0)].copy()
            if hg_.empty:
                st.info("No player data for this match yet.")
            else:
                if (hg_["n_played"] > 1).any():
                    st.caption("One of these teams played twice this gameweek; FPL's per-gameweek totals include both matches.")
                pl_ = players.set_index("id")
                fc_ = pd.Series(dtype=float)
                if len(train_df) and "xPts" in train_df.columns:            # structural forecast (exists from GW3)
                    t_ = train_df[(train_df["GW"] == gw_a) & ((train_df["season"] == "this season") if "season" in train_df.columns else True)]
                    fc_ = pd.Series(t_["xPts"].to_numpy(float), index=t_["id"].to_numpy())
                if len(wf_all):                                               # the learned forecast replaces it where it exists
                    w_ = wf_all[wf_all["GW"] == gw_a]
                    fc_ = pd.concat([fc_, pd.Series(np.clip(w_["xPts"] + best_alpha(wf_all) * w_["adj"], 0, None).to_numpy(float),
                                                    index=w_["id"].to_numpy())])
                    fc_ = fc_[~fc_.index.duplicated(keep="last")]
                tb_ = pd.DataFrame({"Player": hg_["id"].map(pl_["web_name"]), "Team": hg_["team"].map(code),
                                    "Pos": hg_["element_type"].map({1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}), "Mins": hg_["minutes"].astype(int),
                                    "Goals": hg_["goals_scored"].astype(int), "Assists": hg_["assists"].astype(int),
                                    "Bonus": hg_["bonus"].astype(int), "BPS": hg_["bps"].astype(int),
                                    "DEFCON": hg_["defensive_contribution"].astype(int), "Saves": hg_["saves"].astype(int),
                                    "Points": hg_["total_points"].astype(int),
                                    "Forecast (pts)": pd.to_numeric(hg_["id"].map(fc_), errors="coerce").astype(float).round(2)})
                tb_["vs forecast"] = (tb_["Points"] - tb_["Forecast (pts)"]).astype(float).round(2)
                if tb_["Forecast (pts)"].isna().all():
                    st.caption("No pre-match forecasts exist for this gameweek (the model's forecasts start at GW3).")
                tb_ = tb_.sort_values("Points", ascending=False).reset_index(drop=True)
                if tb_["Forecast (pts)"].notna().any():
                    up_ = tb_.dropna(subset=["vs forecast"]).nlargest(3, "vs forecast")
                    dn_ = tb_.dropna(subset=["vs forecast"]).nsmallest(3, "vs forecast")
                    st.markdown("**Beat their forecast:** " + ", ".join(f"{r_['Player']} {r_['Points']} pts (forecast {r_['Forecast (pts)']:.1f})" for _, r_ in up_.iterrows())
                                + "  \\n**Missed it:** " + ", ".join(f"{r_['Player']} {r_['Points']} pts (forecast {r_['Forecast (pts)']:.1f})" for _, r_ in dn_.iterrows()))
                sty_a = tb_.style.apply(lambda s_: pct_colors(s_), subset=["Points"], axis=0).apply(
                    lambda s_: [("background-color:#c8f0d8" if v_ > 1 else "background-color:#f6d0d0" if v_ < -1 else "") if pd.notna(v_) else "" for v_ in s_],
                    subset=["vs forecast"], axis=0).format(na_rep="–")
                st.dataframe(sty_a, use_container_width=True, hide_index=True, height=min(560, 38 + 35 * len(tb_)))
                br_ = tb_.nlargest(6, "BPS")[["Player", "Team", "BPS", "Bonus"]]
                st.markdown("**Bonus race:** " + " · ".join(f"{r_['Player']} {r_['BPS']} BPS" + (f" (+{r_['Bonus']})" if r_["Bonus"] else "") for _, r_ in br_.iterrows()))
                # ---- 4. team news: regulars who didn't play ----
                prev_ = ctx["h"][(ctx["h"]["round"] < gw_a) & (ctx["h"]["round"] >= gw_a - 3) & ctx["h"]["team"].isin([ht, at])]
                reg_ = prev_.groupby("id")["min_pm"].agg(["mean", "size"])
                reg_ = reg_[(reg_["mean"] >= 60) & (reg_["size"] >= 2)].index
                now_ = ctx["h"][ctx["h"]["round"] == gw_a].set_index("id")["minutes"]
                out_ = [i_ for i_ in reg_ if float(now_.get(i_, 0)) == 0]
                st.markdown("**Regulars who didn't play:** " + (", ".join(f"{pl_.at[i_, 'web_name']} ({code[int(pl_.at[i_, 'team'])]})" for i_ in out_ if i_ in pl_.index)
                                                              if out_ else "none"))
                # ---- 5. your league ----
                lg_a = load_league(int(league_id))
                if my_entry_sel is not None:
                    mine_a = [i_ for i_ in squad_for(lg_a, my_entry_sel, gw_a, next_gw) if i_ in set(hg_["id"])]
                    opp_le_ = None
                    mt_a = lg_a["matches"]
                    if not mt_a.empty and {"event", "league_entry_1", "league_entry_2"} <= set(mt_a.columns):
                        r_m = mt_a[(mt_a["event"] == gw_a) & ((mt_a["league_entry_1"] == my_entry_sel) | (mt_a["league_entry_2"] == my_entry_sel))]
                        if len(r_m):
                            opp_le_ = int(r_m["league_entry_2"].iloc[0] if int(r_m["league_entry_1"].iloc[0]) == my_entry_sel else r_m["league_entry_1"].iloc[0])
                    theirs_a = [i_ for i_ in squad_for(lg_a, opp_le_, gw_a, next_gw) if i_ in set(hg_["id"])] if opp_le_ is not None else []
                    pts_ = hg_.set_index("id")["total_points"]
                    fmt_ = lambda ids_: ", ".join(f"{pl_.at[i_, 'web_name']} {int(pts_.get(i_, 0))} pts" for i_ in ids_) or "none"
                    st.markdown(f"**Your players in this match:** {fmt_(mine_a)}  \\n**Your opponent's:** {fmt_(theirs_a)}")

if page == "Matchups":
    fx_sm = fx[fx["event"] == selected_gw]
    st.markdown(MP_CSS, unsafe_allow_html=True)
    st.subheader(f"Match predictions · GW{selected_gw}")
    if fx_sm.empty:
        st.info("No fixtures this gameweek.")
    else:
        # team news: FPL's flags give the default missing list (out = 1, doubtful = 1 - chance of playing); each fixture's list can be
        # edited in the deep-dive below. How much it matters is measured on this season's matches.
        sh_now = player_shares(ctx["h"], players, selected_gw)
        abs_coefs, abs_tab, abs_n = absence_effect_fit(ctx["long"], ctx["fin"], ctx["h"], players, p)
        _flag_w = {}
        for _, r_ in players.iterrows():
            stt_, ch_ = str(r_.get("status", "a")), pd.to_numeric(r_.get("chance_of_playing_next_round"), errors="coerce")
            if stt_ in ("i", "s", "u") or (pd.notna(ch_) and ch_ <= 25):
                _flag_w[int(r_["id"])] = 1.0
            elif stt_ == "d":
                _flag_w[int(r_["id"])] = 1 - (float(ch_) / 100 if pd.notna(ch_) else 0.5)
        miss_w = {}
        for _, f_ in fx_sm.iterrows():
            for t_ in (int(f_["team_h"]), int(f_["team_a"])):
                key_ = f"mu_miss_{int(f_['id'])}_{t_}"
                tp_ = players.loc[players["team"] == t_, "id"].astype(int)
                if key_ in st.session_state:                      # your edit for this fixture
                    for pid_ in st.session_state[key_]:
                        miss_w[int(pid_)] = 1.0
                else:
                    for pid_ in tp_:
                        if pid_ in _flag_w:
                            miss_w[pid_] = _flag_w[pid_]
        miss_now = team_absence(miss_w, sh_now, players)
        NEWS_MODES = {"Measured from this season": abs_coefs, "Standard (attack 50%, defence 15%)": (0.5, 0.15), "Off": (0.0, 0.0)}
        st.session_state.setdefault("mu_news_mode", "Measured from this season")
        news_mode = st.radio("Team news strength", list(NEWS_MODES), key="mu_news_mode", horizontal=True,
                             help="Measured = the strength that best predicted this season's finished matches (it can be 0 early in the season, when "
                                  "there are few absences to learn from). Standard = a typical effect: a player with 30% of his team's attack missing "
                                  "cuts its expected goals by 15%. Off = ignore who's missing.")
        news_coefs = NEWS_MODES[news_mode]
        preds = fixture_predictions(ctx["long"], fx_sm[["id", "team_h", "team_a"]], selected_gw, p,
                                    (mkt or {}).get("team") if mkt_live else None, mkt_w if mkt_live else 0.0, ctx["fin"],
                                    miss_now, news_coefs)
        cols_p = st.columns(3)
        for k_, (_, r_) in enumerate(preds.iterrows()):
            hc, ac = code[int(r_["home"])], code[int(r_["away"])]
            ph_, pd_, pa_ = r_["p_home"] * 100, r_["p_draw"] * 100, r_["p_away"] * 100
            cols_p[k_ % 3].markdown(
                f"<div class='mp'><div class='mp-teams'><div class='mp-team'>{jersey_svg(hc)}{hc}</div>"
                f"<div class='mp-score'>{r_['score']}<small>predicted score · {r_['p_score'] * 100:.0f}%</small></div>"
                f"<div class='mp-team'>{jersey_svg(ac)}{ac}</div></div>"
                f"<div class='mp-xg'>Expected goals <b>{r_['xg_h']:.2f} – {r_['xg_a']:.2f}</b> · likeliest exact scores {r_['next_scores']}</div>"
                f"<div class='mp-bar'><span style='width:{ph_:.0f}%;background:#00C46A;color:#fff'>{hc} {ph_:.0f}%</span>"
                f"<span style='width:{pd_:.0f}%;background:#c9c6d6;color:#37003C'>Draw {pd_:.0f}%</span>"
                f"<span style='width:{pa_:.0f}%;background:#963CFF;color:#fff'>{ac} {pa_:.0f}%</span></div>"
                f"<div class='mp-stats'><span>{hc} clean sheet <b>{r_['cs_h'] * 100:.0f}%</b></span><span>{ac} clean sheet <b>{r_['cs_a'] * 100:.0f}%</b></span>"
                f"<span>Both score <b>{r_['btts'] * 100:.0f}%</b></span><span>Over 2.5 <b>{r_['over25'] * 100:.0f}%</b></span></div>"
                + (f"<div class='mp-xg' style='margin-top:4px'>Team news: {hc} {r_['dxg_h']:+.2f} xG · {ac} {r_['dxg_a']:+.2f} xG</div>"
                   if abs(r_["dxg_h"]) + abs(r_["dxg_a"]) >= 0.01 else "") + "</div>",
                unsafe_allow_html=True)
        bt_ = prediction_backtest(ctx["long"], ctx["fin"], p)
        if len(abs_tab):
            _b0 = abs_tab[(abs_tab["Attack loss"] == 0) & (abs_tab["Defence gain"] == 0)].iloc[0]
            _bb = abs_tab[(abs_tab["Attack loss"] == abs_coefs[0]) & (abs_tab["Defence gain"] == abs_coefs[1])].iloc[0]
            st.caption(f"**Team news effect**, measured on {abs_n} finished matches (regulars who played 0 minutes counted as missing): "
                       + (f"a missing player cuts his team's expected goals by {abs_coefs[0] * 100:.0f}% of his share of their attack, and missing "
                          f"defenders/keeper add {abs_coefs[1] * 100:.0f}% of their share to the opponent. Right result {_b0['Right result %']:.0f}% → "
                          f"{_bb['Right result %']:.0f}%, probability error {_b0['Brier']:.3f} → {_bb['Brier']:.3f} with team news."
                          if any(abs_coefs) else "missing players didn't measurably change results yet, so team news isn't applied. It is re-checked as "
                          "results come in."))
        src_txt = preds["source"].iloc[0] if len(preds) else "team ratings"
        if len(bt_) >= 10:
            st.caption(f"Based on {src_txt}: every team's attack and defence rated together from all of this season's matches, adjusting for who "
                       f"they played, with goals blended {preds['xw'].iloc[0] * 100:.0f}% with expected goals and older matches fading (half-life "
                       f"{preds['hl'].iloc[0]:.0f} gameweeks), both chosen by past accuracy. Predicted score = the likeliest score for the likeliest result; a single exact score is rarely more "
                       f"than 12–15% likely, so it is usually a low one. Tested on {len(bt_)} finished matches, each predicted only "
                       f"from earlier games: the likeliest result happened **{bt_['hit'].mean() * 100:.0f}%** of the time (guessing from this season's "
                       f"home/draw/away rates: {bt_['base_hit'].mean() * 100:.0f}%); exact score {bt_['exact'].mean() * 100:.0f}%; probability error "
                       f"(Brier) {bt_['brier'].mean():.3f} vs {bt_['base_brier'].mean():.3f} for the guess (lower is better).")
        else:
            st.caption(f"Based on the {src_txt}. The accuracy check needs a few more finished gameweeks.")

        # ---- best attacking matchups across the gameweek ----
        mm_all = matchup_tables(ctx, selected_gw, p)
        best_ = []
        for _, f_ in fx_sm.iterrows():
            for atk_, dfn_, ven_ in ((int(f_["team_h"]), int(f_["team_a"]), "H"), (int(f_["team_a"]), int(f_["team_h"]), "A")):
                for pos_, nm_ in ((2, "DEF"), (3, "MID"), (4, "FWD")):
                    best_.append((mm_all["idx"]["pts"][dfn_, pos_], atk_, dfn_, ven_, pos_, nm_))
        best_ = sorted(best_, reverse=True)[:8]
        st.markdown("**Best attacking matchups this gameweek**")
        lines_ = []
        for v_, atk_, dfn_, ven_, pos_, nm_ in best_:
            cand_ = full_df[(full_df["team"] == atk_) & (full_df["element_type"] == pos_) & (full_df["n_fix"] > 0)] if full_df is not None else pd.DataFrame()
            top_ = ", ".join(f"{r_['web_name']} ({r_['AER1']:.1f})" for _, r_ in cand_.nlargest(2, "AER1").iterrows()) if len(cand_) else ""
            lines_.append(f"- **{code[atk_]} {nm_}** ({ven_}) vs **{code[dfn_]}** — {code[dfn_]} give up **{(v_ - 1) * 100:+.0f}%** FPL points to "
                          f"{nm_.lower()}s{(' · ' + top_) if top_ else ''}")
        st.markdown("\n".join(lines_))
        st.caption("+30% = that opponent concedes 30% more FPL points to players in that position than an average team (recent games weighted most). "
                   "Numbers in brackets = expected points this gameweek.")

        # ---- fixture deep-dive ----
        st.subheader("Fixture deep-dive")
        fix_lab = {int(r_["id"]): f"{code[int(r_['team_h'])]} v {code[int(r_['team_a'])]}" for _, r_ in fx_sm.iterrows()}
        fid_sel = st.selectbox("Fixture", list(fix_lab), format_func=fix_lab.get, key="mu_fix")
        fr_sel = fx_sm[fx_sm["id"] == fid_sel].iloc[0]
        st.markdown("**Who's missing?** Starts with FPL's injury and suspension flags; add or remove players and the prediction above updates.")
        cm_l, cm_r = st.columns(2)
        for col_, t_ in zip((cm_l, cm_r), (int(fr_sel["team_h"]), int(fr_sel["team_a"]))):
            key_ = f"mu_miss_{int(fid_sel)}_{t_}"
            tp_ = players[players["team"] == t_].copy()
            tp_["sh_att"] = tp_["id"].map(sh_now["att"]).fillna(0)
            tp_["sh_def"] = tp_["id"].map(sh_now["def"]).fillna(0)
            tp_ = tp_.sort_values(["sh_att", "sh_def", "minutes"], ascending=False)
            if key_ not in st.session_state:
                st.session_state[key_] = [int(i_) for i_ in tp_["id"] if _flag_w.get(int(i_), 0) >= 0.5]
            lab_m = {int(r_["id"]): (f"{r_['web_name']} ({r_['position']}) · {r_['sh_att'] * 100:.0f}% of attack"
                                     + (f", {r_['sh_def'] * 100:.0f}% of defence" if r_["sh_def"] > 0 else "")) for _, r_ in tp_.iterrows()}
            col_.multiselect(f"Missing for {code[t_]}", list(lab_m), key=key_, format_func=lambda i_, l_=lab_m: l_.get(i_, str(i_)))
        if len(abs_tab):
            with st.expander("How much team news matters (tested on past matches)"):
                st.dataframe(abs_tab.round(3).sort_values("Log loss"), use_container_width=True, hide_index=True)
                st.caption("Each row re-predicts this season's finished matches from earlier games only, with missing players' effect at that strength. "
                           "Lowest log loss is used. Attack loss 0.75 = a player with 30% of his team's attack missing cuts its expected goals by 22%.")
        c_l, c_r = st.columns(2)
        for col_, (atk_, dfn_, ven_) in zip((c_l, c_r), ((int(fr_sel["team_h"]), int(fr_sel["team_a"]), "home"),
                                                          (int(fr_sel["team_a"]), int(fr_sel["team_h"]), "away"))):
            rows_h = "".join(f"<tr><td class='pos'>{nm_}</td>" + "".join(_mm_cell(mm_all["idx"][s_][dfn_, pos_]) for s_ in ("g", "a", "thr", "pts")) + "</tr>"
                             for pos_, nm_ in ((2, "Defenders"), (3, "Midfielders"), (4, "Forwards")))
            bestpos_ = max(((mm_all["idx"]["pts"][dfn_, pos_], nm_) for pos_, nm_ in ((2, "defenders"), (3, "midfielders"), (4, "forwards"))))
            col_.markdown(f"**{code[atk_]} attacking ({ven_}) — what {code[dfn_]} concede**", unsafe_allow_html=True)
            col_.markdown(f"<table class='mm'><tr><th></th><th>Goals</th><th>Assists</th><th>Chances</th><th>FPL pts</th></tr>{rows_h}</table>",
                          unsafe_allow_html=True)
            col_.caption(f"Best target: {code[atk_]} {bestpos_[1]} ({(bestpos_[0] - 1) * 100:+.0f}% FPL points vs an average opponent).")
        st.caption("Each cell compares this opponent with an average team: green = they give up more to that position (good for the attackers), "
                   "red = they're tight. Chances = shot threat conceded. FPL data has no left/right-flank detail, so this works by position.")

    st.subheader("Shot map")
    if fx_sm.empty:
        st.info("No fixtures this gameweek.")
    else:
        try:
            us_m = us_league_matches(understat_season())
        except Exception as e_us:
            us_m = pd.DataFrame()
            st.warning(f"Couldn't reach Understat right now ({type(e_us).__name__}). Shot maps need understat.com; try again later.")
        if len(us_m):
            s2_, s3_, s4_ = st.columns([2, 1.4, 1.6])
            fid_sm = st.session_state.get("mu_fix", list(fix_lab)[0])
            fid_sm = fid_sm if fid_sm in fix_lab else list(fix_lab)[0]
            st.caption(f"Fixture: **{fix_lab[fid_sm]}** (change it under Fixture deep-dive).")
            fr_ = fx_sm[fx_sm["id"] == fid_sm].iloc[0]
            side_ids = {f"{code[int(fr_['team_h'])]} attacking": (int(fr_["team_h"]), int(fr_["team_a"])),
                        f"{code[int(fr_['team_a'])]} attacking": (int(fr_["team_a"]), int(fr_["team_h"]))}
            side_sm = s2_.radio("Side", list(side_ids), key="sm_side", horizontal=True)
            last_n = s3_.selectbox("Matches back", [5, 10, 38], index=1, key="sm_n", format_func=lambda n_: "Season" if n_ == 38 else f"Last {n_}")
            sit_sm = s4_.selectbox("Shots", ["Open play", "All (no penalties)", "All"], key="sm_sit")
            atk_id, def_id = side_ids[side_sm]
            atk_t, def_t = us_team_title(atk_id, us_m), us_team_title(def_id, us_m)
            if not atk_t or not def_t:
                st.info("Couldn't match these clubs to Understat's names.")
            else:
                with st.spinner("Loading shots from Understat…"):
                    a_sh, n_a = us_team_shots(atk_t, us_m, last_n)
                    d_sh, n_d = us_team_shots(def_t, us_m, last_n)

                def _sit(d_):
                    if d_ is None or d_.empty:
                        return d_
                    if sit_sm == "Open play":
                        return d_[d_["situation"] == "OpenPlay"]
                    if sit_sm.startswith("All (no"):
                        return d_[d_["situation"] != "Penalty"]
                    return d_

                att_all = _sit(a_sh[a_sh["team"] == atk_t]) if len(a_sh) else pd.DataFrame()
                con_all = _sit(d_sh[d_sh["opp"] == def_t]) if len(d_sh) else pd.DataFrame()
                if att_all is None or att_all.empty:
                    st.info(f"No {atk_t} shots found in those matches.")
                else:
                    by_p = att_all.groupby("player")["xG"].sum().sort_values(ascending=False)
                    who = st.selectbox("Player (or the whole team)", ["Whole team"] + list(by_p.index), key="sm_player",
                                       format_func=lambda p_: p_ if p_ == "Whole team" else f"{p_} · {by_p[p_]:.2f} xG")
                    att_ = att_all if who == "Whole team" else att_all[att_all["player"] == who]
                    base_sh = pd.concat([a_sh, d_sh], ignore_index=True).drop_duplicates(["match", "minute", "player", "X", "Y"])
                    base_sh = _sit(base_sh)
                    st.markdown(shot_map_svg(att_, con_all, who if who != "Whole team" else atk_t, def_t, n_d, base_sh), unsafe_allow_html=True)
                    # does he shoot from where they concede?
                    whose_ = "His" if who != "Whole team" else f"{atk_t}'s"
                    if len(con_all) and n_d:
                        wk_ = zone_weakness(con_all, base_sh)
                        weak_z = set(wk_[wk_ > 1.15].index)
                        az_ = att_.assign(z=shot_zone(att_))
                        bz_ = base_sh.assign(z=shot_zone(base_sh))
                        share_ = float(az_.loc[az_["z"].isin(weak_z), "xG"].sum() / max(az_["xG"].sum(), 1e-9))
                        typ_ = float(bz_.loc[bz_["z"].isin(weak_z), "xG"].sum() / max(bz_["xG"].sum(), 1e-9))
                        k1_, k2_, k3_ = st.columns(3)
                        k1_.metric("Shots / xG", f"{len(att_)} / {att_['xG'].sum():.2f}", f"{att_['xG'].sum() / max(n_a, 1):.2f} xG per match", delta_color="off")
                        k2_.metric(f"{def_t} concede", f"{con_all['xG'].sum() / n_d:.2f} xG / match", f"{len(con_all) / n_d:.1f} shots per match", delta_color="off")
                        k3_.metric(f"{whose_} xG from {def_t}'s weak spots", f"{share_ * 100:.0f}%",
                                   f"{(share_ - typ_) * 100:+.0f} pts vs typical {typ_ * 100:.0f}%", delta_color="normal" if share_ >= typ_ else "inverse",
                                   help=f"Weak spots = zones where {def_t} concede a bigger share of their xG than teams usually do there (red on the map). "
                                        "Compare with 'typical': the share of all shots' xG from those same zones. Above typical = these chances come exactly "
                                        f"where {def_t} are unusually vulnerable.")
                    _few = ", all played so far" if n_a < last_n and last_n != 38 else ""
                    _few_d = ", all played so far" if n_d < last_n and last_n != 38 else ""
                    st.caption(f"Red = where {def_t} concede more of their xG than usual (last {n_d} matches{_few_d}; darker = more unusual).")
                    st.caption(f"Dots = {who if who != 'Whole team' else atk_t} shots, last {n_a} matches{_few}. Bigger = higher xG. Data: understat.com.")
    st.subheader("Bookmaker priors")
    st.caption("Implied goals, clean sheets and scorer odds from The Odds API. There is no clean-sheet market, so clean-sheet odds come from the goals implied by 1X2, over/under, "
               "team totals and both-teams-to-score. Free plans have a monthly credit cap; fetches are cached for 3 hours and logged to fpl_odds_history.csv so you can back-test later.")
    if st.session_state.get("odds_err"):
        st.warning(st.session_state["odds_err"])
    if pack_ and pack_["gw"] == selected_gw and pack_.get("hdr"):
        st.caption(f"Credits remaining: {pack_['hdr'].get('x-requests-remaining', '?')} · last call cost: {pack_['hdr'].get('x-requests-last', '?')}")
    if not mkt_live:
        st.info("Enter your key in the sidebar, tick 'Use bookmaker priors' and press 'Fetch odds for this gameweek'.")
    else:
        st.dataframe(mkt_info.round(2), use_container_width=True, hide_index=True)
        if mkt_meta.get("unmatched"):
            st.caption(f"{mkt_meta['unmatched']} player quotes couldn't be matched to FPL players and were ignored.")
        st.caption("The ML correction was trained on model-only forecasts (no historical odds are available on free plans), so bookmaker priors can't be back-tested yet. "
                   "Where the market expects far fewer goals than a starter would score, the model also trims that player's chance of playing.")

# =========================================================
# ACCURACY
# =========================================================
if page == "Accuracy":
    st.subheader("How close is AER to what was really scored?")
    st.caption("Every finished GW is forecast using only earlier gameweeks (the learned layer is retrained each week on the past), "
               "then compared with actual points. GW 0 and below are past seasons (last season's GW38 = GW 0, its GW1 = GW −37).")
    if wf_all.empty:
        st.info("Needs about 4+ finished gameweeks before the learned layer can be evaluated.")
    else:
        a_all = best_alpha(wf_all)
        god_s = god_expanding(wf_all, a_all, god_conv)
        sc, ov = score_table(wf_all, a_all, god_s)
        m = sc.drop(columns=["GW", "Players"]).mean()
        c = st.columns(5)
        c[0].metric("R² — AER", f"{ov['r2_aer']:.3f}", f"{ov['r2_aer'] - ov['r2_form']:+.3f} vs form")
        c[1].metric("R² — Base", f"{ov['r2_base']:.3f}")
        c[2].metric("Avg miss (pts)", f"{m['Avg miss (AER)']:.2f}", f"{m['Avg miss (AER)'] - m['Avg miss (Form)']:+.2f} vs form", delta_color="inverse")
        c[3].metric("Top-20 pts", f"{m['Top-20 pts (AER)']:.2f}", f"{m['Top-20 pts (AER)'] - m['Top-20 pts (Form)']:+.2f} vs form")
        c[4].metric("Within ±2 pts", f"{m['Within ±2 (AER)'] * 100:.0f}%")
        g1, g2, g3 = st.columns(3)
        g1.metric("R² — Solve FPL", f"{ov['r2_god']:.3f}", f"{ov['r2_god'] - ov['r2_aer']:+.3f} vs AER")
        g2.metric("Avg miss — Solve FPL", f"{m['Avg miss (Solve)']:.2f}", f"{m['Avg miss (Solve)'] - m['Avg miss (AER)']:+.2f} vs AER", delta_color="inverse")
        g3.metric("Top-20 pts — Solve FPL", f"{m['Top-20 pts (Solve)']:.2f}", f"{m['Top-20 pts (Solve)'] - m['Top-20 pts (AER)']:+.2f} vs AER")
        st.caption(f"Learned-correction strength picked automatically: **{a_all:.2f}** (0 = ignore the learner, 1 = trust it fully). "
                   f"Bias: {m['Bias (AER)']:+.2f} pts per player.")
        st.line_chart(sc.set_index("GW")[["Rank corr (AER)", "Rank corr (Base)", "Rank corr (Form)", "Rank corr (Solve)"]])
        st.line_chart(sc.set_index("GW")[["Top-20 pts (AER)", "Top-20 pts (Base)", "Top-20 pts (Form)", "Top-20 pts (Solve)"]])
        st.dataframe(sc.round(3), use_container_width=True)
        st.info("Reality check: single-gameweek FPL points are dominated by luck (a goal or a clean sheet swings 4–6 points). "
                "Even the best public models explain only roughly 15–25% of player-week variance, so R² in that range is a strong result. "
                "AER's job is to be the best *average*; Floor/Ceiling and 5+/8+ % show the realistic spread around it.")

    with st.expander(":material/bolt: DPS (process points): does it predict?"):
        dv_ = globals().get("dps_val")
        if dv_ is None or not len(dv_):
            st.info("Needs a few finished gameweeks of model forecasts.")
        else:
            wv_ = wf_all.assign(aer=np.clip(wf_all["xPts"] + best_alpha(wf_all) * wf_all["adj"], 0, None))[["id", "GW", "aer"]] if len(wf_all) else None
            dvm_ = dv_.merge(wv_, on=["id", "GW"], how="left") if wv_ is not None else dv_.assign(aer=np.nan)
            tab_ = {"DPS (process points)": _fit_stats(dvm_["dps"], dvm_["actual"]), "Structural forecast": _fit_stats(dvm_["xPts"], dvm_["actual"]),
                    "Recent form (avg points)": _fit_stats(dvm_["form"], dvm_["actual"])}
            if dvm_["aer"].notna().sum() > 100:
                ok_ = dvm_["aer"].notna()
                tab_["AER (on the same weeks)"] = _fit_stats(dvm_.loc[ok_, "aer"], dvm_.loc[ok_, "actual"])
                tab_["DPS (on the same weeks)"] = _fit_stats(dvm_.loc[ok_, "dps"], dvm_.loc[ok_, "actual"])
            st.dataframe(pd.DataFrame(tab_).T.round(3), use_container_width=True)
            st.caption(f"{len(dvm_):,} player-gameweeks, each predicted only from earlier gameweeks. Higher R² and rank correlation, lower miss = better. "
                       "DPS uses no goals, assists or bonus actually scored, so it shows how much of the forecast the underlying process alone explains.")
            rel_ = globals().get("dps_rel") or {}
            if rel_:
                st.caption("How repeatable each stat is from one game to the next (share of variation that is real difference between players): "
                           + "; ".join(f"{nm_}: " + ", ".join(f"{pn_} {rel_.get((k_, e_), np.nan):.2f}" for e_, pn_ in ((2, "DEF"), (3, "MID"), (4, "FWD"))
                                                            if (k_, e_) in rel_)
                                       for k_, nm_ in (("xg", "xG"), ("xa", "xA"), ("dc", "DEFCON"), ("bonus", "bonus"))) + ". Low = pulled harder to the position norm.")
    with st.expander(":material/stacked_line_chart: Zipf xVR: power law and out-of-sample check"):
        _zi = globals().get("zipf_info") or {}
        _zl = _zi.get("law", pd.DataFrame())
        if not len(_zl):
            st.info("Needs finished gameweeks with minutes data.")
        else:
            z1, z2, z3 = st.columns(3)
            z1.metric("Zipf exponent s (median)", f"{_zl['Zipf exponent s'].median():.2f}",
                      help="Slope of log(points over expectation) against log(rank) among each gameweek's top 20%. 1 = classic Zipf.")
            z2.metric("Log-log fit R² (median)", f"{_zl['Log-log fit R²'].median():.2f}",
                      help="How straight the log-log line is. Close to 1 = the elite tail really does follow a power law.")
            z3.metric("Points from the top 20%", f"{_zl['Share of points scored by the top 20%'].mean() * 100:.0f}%",
                      help="Pareto check: the share of all points (from players who played) scored by the top 20%.")
            st.dataframe(_zl.round(3), use_container_width=True, hide_index=True)
            zb = zipf_backtest(_zi.get("rows")) if _zi.get("rows") is not None else pd.DataFrame()
            if len(zb):
                st.caption("Each gameweek predicted from earlier gameweeks only. AUC 0.5 = guessing, 1.0 = perfect at picking who makes the top 20%. "
                           "If the Zipf xVR top 20 don't outscore AER's top 20, treat it as a tie-breaker rather than a ranking.")
                st.line_chart(zb.set_index("GW")[[c_ for c_ in ["AUC (picking the top 20%)", "AUC with Theta Swole inputs", "AUC of Theta Swole alone",
                                                                 "AUC without BPS profile"] if c_ in zb.columns]])
                st.line_chart(zb.set_index("GW")[[c_ for c_ in ["Top-20 pts by Zipf xVR", "Top-20 pts by converged xVR", "Top-20 pts by Theta Swole",
                                                                 "Top-20 pts by AER"] if c_ in zb.columns]])
                st.dataframe(zb.drop(columns="GW").mean().rename("Average").round(3).to_frame(), use_container_width=True)
            else:
                st.caption("The out-of-sample check needs a few more finished gameweeks of model forecasts.")
            if isinstance(_zi.get("defs"), pd.DataFrame):
                st.markdown("**What 'points over expectation' is measured against**")
                st.dataframe(_zi["defs"].round(3), use_container_width=True)
                st.caption("Old: each player's points over the model's own forecast for him. That rewards surprise, so the elite model learned to favour "
                           "players the model expected little from. New (in use): points over what an ordinary player in the same position would score in "
                           "the same minutes, Theta Swole's 'compare with the position' idea. 'Hauls among xVR top 20' = how many of the 20 players xVR "
                           "rated highest really landed a top-20% raw-points week (same yardstick for both).")
            st.markdown("**Theta Swole vs Zipf xVR**")
            if "rho" in _zi or "auc_conv" in _zi:
                s1, s2, s3, s4 = st.columns(4)
                s1.metric("Rank agreement now (ρ)", f"{_zi.get('rho', float('nan')):.2f}",
                          help="Spearman rank correlation between the two ratings across players with a fixture this gameweek. 1 = same order, 0 = unrelated.")
                s2.metric("Same players in both top 20", f"{_zi.get('top20_overlap', 0)}/20")
                s3.metric("Elite AUC: xVR → with Theta inputs", f"{_zi.get('auc_conv', float('nan')):.3f}",
                          f"{_zi.get('auc_conv', float('nan')) - _zi.get('auc_base', float('nan')):+.3f} vs {_zi.get('auc_base', float('nan')):.3f}")
                s4.metric("Elite AUC: Theta Swole alone", f"{_zi.get('auc_theta', float('nan')):.3f}")
                st.caption(f"Elite model inputs in use: **{_zi.get('variant', 'standard inputs')}**. Average hit rate (AUC) on gameweeks the model "
                           f"hadn't seen: standard {_zi.get('auc_base', float('nan')):.3f}, without BPS profile {_zi.get('auc_nobps', float('nan')):.3f}, "
                           f"with Theta Swole's inputs {_zi.get('auc_conv', float('nan')):.3f}. The app switches set only for a gain above 0.002 that holds "
                           "in at least half the gameweeks, and re-checks every time new results come in.")
                if _zi.get("rho_pos"):
                    st.caption("Rank agreement by position: " + ", ".join(f"{k_} ρ = {v_:.2f}" for k_, v_ in _zi["rho_pos"].items())
                               + (f". Past gameweeks (out of sample): ρ = {_zi['hist_rho']:.2f} on average." if "hist_rho" in _zi else "."))
                st.caption("They measure different things: Theta Swole ranks expected points above a replacement-level player at the same position "
                           "(the average week); Zipf xVR ranks the chance and size of a top-20% week (the tail). The elite model was refitted with Theta's "
                           "ingredients added (points above replacement, DPS signal, floor–ceiling spread, upside) and compared gameweek by gameweek. "
                           + ("It improved the hit rate in most gameweeks, so the app now uses the converged model."
                              if _zi.get("converge") else "It did not improve the hit rate reliably, so the app keeps the elite model without them."))
            if seasons_used:
                st.caption(f"Transfers in, tested on {seasons_used[0]}: coefficient {_zi.get('tr_coef', 0):+.3f} per standard deviation, "
                           f"z = {_zi.get('tr_z', 0):.1f} → {'used' if _zi.get('tr_used') else 'not used'} (needs z > 2 and a positive effect).")
    with st.expander(":material/straighten: Ceiling & floor accuracy"):
        if wf_all.empty or "q90" not in wf_all.columns:
            st.info("Needs about 4+ finished gameweeks and scikit-learn.")
        else:
            a_i = best_alpha(wf_all)
            rep_ = interval_report(wf_all, god_expanding(wf_all, a_i, god_conv))
            if rep_ is None:
                st.info("Ceiling/floor models need scikit-learn and enough history.")
            else:
                r1, r2, r3 = st.columns(3)
                r1.metric("Actual ≤ Ceiling", f"{rep_['ceiling_hit'] * 100:.0f}%", f"target 90% (raw model {rep_['raw_ceiling_hit'] * 100:.0f}%)", delta_color="off")
                r2.metric("Actual ≥ Floor", f"{rep_['floor_hit'] * 100:.0f}%", f"target 90% (raw model {rep_['raw_floor_hit'] * 100:.0f}%)", delta_color="off")
                r3.metric("Average width (pts)", f"{rep_['width']:.1f}")
                st.caption("Ceiling = 90th and Floor = 10th percentile of points, from gradient-boosted quantile regression on the same signals as the forecast, "
                           "then shifted by offsets learned only from earlier gameweeks so real hit rates match the targets.")
    with st.expander(":material/timer: Minutes model"):
        if train_df.empty or "p60_ml" not in train_df.columns or train_df["p60_ml"].notna().sum() < 300:
            st.info("The learned minutes model needs about 5+ finished gameweeks and scikit-learn.")
        else:
            mm_ = train_df[train_df["p60_ml"].notna()]
            br = lambda pcol: float(((mm_[pcol] - mm_["act60"]) ** 2).mean())
            b1, b2, b3 = st.columns(3)
            b1.metric("Brier — learned model", f"{br('p60_ml'):.3f}")
            b2.metric("Brier — smoothed rates", f"{br('p60_eb'):.3f}", f"{br('p60_ml') - br('p60_eb'):+.3f} learned vs smoothed", delta_color="inverse")
            b3.metric("Brier — last-3 starts", f"{float(((mm_['start_3'] - mm_['act60']) ** 2).mean()):.3f}")
            st.caption(f"Probability that a player plays 60+ minutes, scored on {len(mm_):,} player-weeks; lower is better. The learned model is refit every 3 gameweeks "
                       "using only earlier games.")
    with st.expander(":material/groups: Accuracy by position"):
        if wf_all.empty:
            st.info("Needs about 4+ finished gameweeks.")
        else:
            w_ = wf_all.assign(aer=god_expanding(wf_all, best_alpha(wf_all), god_conv))
            rows_ = []
            for gname, sel_ in (("DEF/GK", w_["element_type"].isin([1, 2])), ("MID", w_["element_type"] == 3), ("FWD", w_["element_type"] == 4)):
                m_ = w_[sel_]
                if len(m_) < 50:
                    continue
                var_ = ((m_["actual"] - m_["actual"].mean()) ** 2).sum()
                rows_.append({"Group": gname, "Player-weeks": len(m_),
                              "R² (AER)": 1 - ((m_["actual"] - m_["aer"]) ** 2).sum() / var_,
                              "R² (Base)": 1 - ((m_["actual"] - m_["xPts"]) ** 2).sum() / var_,
                              "Rank corr (AER)": rank_corr(m_["aer"], m_["actual"]),
                              "Avg miss (AER)": (m_["aer"] - m_["actual"]).abs().mean()})
            st.dataframe(pd.DataFrame(rows_).round(3), use_container_width=True, hide_index=True)
            st.caption("Untick 'Separate models for DEF/GK, MID, FWD' under Advanced to compare against one pooled model.")
    with st.expander(":material/insights: Patterns in past gameweeks"):
        if wf_all.empty:
            st.info("Needs about 4+ finished gameweeks.")
        else:
            a_p = best_alpha(wf_all)
            pat = residual_patterns(wf_all.assign(pred=god_expanding(wf_all, a_p, god_conv) if god_on else np.clip(wf_all["xPts"] + a_p * wf_all["adj"], 0, None)))
            st.caption("Average of (actual − forecast) by situation, using out-of-sample forecasts. |t| above 2 means a real, systematic miss "
                       "(▲ = players in that group scored more than forecast, ▼ = less).")
            st.dataframe(pat.assign(**{"Actual − forecast (pts)": pat["Actual − forecast (pts)"].round(2), "t-stat": pat["t-stat"].round(1)})
                         .sort_values("t-stat", key=lambda z: z.abs(), ascending=False), use_container_width=True, hide_index=True)
            if st.button("Rank the most pertinent signals", help=HELP["rank_signals"]):
                imp = signal_importance(train_df, feat_cols)
                if imp is None:
                    st.info("Needs 4+ gameweeks of training data and scikit-learn.")
                else:
                    st.caption("How much prediction error grows when a signal is scrambled (larger = more useful; ≤ 0 = adds nothing).")
                    st.dataframe(imp.head(20).rename("Error increase").round(4).to_frame(), use_container_width=True)
    with st.expander(":material/history: Tuning report", expanded=bool(st.session_state.get("last_tune"))):
        rep_t = st.session_state.get("last_tune") or _saved_tune
        if not rep_t:
            st.info("No tune saved yet. Press **🎯 Tune on all history** in the sidebar.")
        else:
            b_, a_ = float(rep_t.get("before", np.nan)), float(rep_t.get("after", np.nan))
            t1, t2, t3 = st.columns(3)
            t1.metric("Squared error, tuned gameweeks", f"{a_:.3f}", f"{a_ - b_:+.3f} vs before ({(1 - a_ / b_) * 100:.1f}% lower)" if b_ else None,
                      delta_color="inverse")
            if "holdout_after" in rep_t:
                hb_, ha_ = float(rep_t["holdout_before"]), float(rep_t["holdout_after"])
                t2.metric("Squared error, held-out gameweeks", f"{ha_:.3f}", f"{ha_ - hb_:+.3f} vs before", delta_color="inverse",
                          help="Past-season gameweeks the search never looked at. If this also falls, the new settings generalise.")
            t3.metric("Gameweeks scored", f"{rep_t.get('gws_scored', '?')}", f"{rep_t.get('mode', '')}, {rep_t.get('minutes', '?')} min", delta_color="off")
            parts_ = pd.DataFrame({"Before": pd.Series(rep_t.get("before_parts", {})), "After": pd.Series(rep_t.get("after_parts", {}))})
            if len(parts_):
                st.dataframe(parts_.round(3), use_container_width=True)
            st.dataframe(pd.Series(rep_t.get("params", {}), name="Tuned value").to_frame(), use_container_width=True)
            st.caption(f"Saved {rep_t.get('when', '?')} to {TUNED_FILE}. Every gameweek is forecast only from the weeks before it, so these are "
                       "out-of-sample errors. The learned correction then trains on the same history (this season plus past seasons).")


# =========================================================
# DOCS
# =========================================================
DOCS_MD = """
**1. Structural forecast (Base)** — sum over the GW's fixtures of:
minutes (2·P(60+) + 1·P(sub)) + goals·(4/5/6) + assists·3 + clean sheet·(4/4/1/0) + **DEFCON** 2·P(threshold: DEF 10, MID/FWD 12)
− ½·E[⌊goals against/2⌋] (GKP/DEF) + saves/3 + bonus − cards/OG/pen misses.
Rates come from the player's last N games, split into home and away, pulled toward their season rate when the sample is thin;
team attack/defence and clean-sheet odds are built the same way, then adjusted for how soft this opponent is.

**2. Learned correction** — for every finished gameweek the program rebuilds what it *would have known* beforehand, records the miss versus
actual points, and trains an ensemble (gradient boosting + extra-trees, one per position group) on those misses. It is retrained walk-forward
(never sees the week it predicts), and the correction strength is chosen automatically by minimising out-of-sample error.

**3. Floor / Ceiling / 5+ % / 8+ %** — conformalised quantile regression, so the spread is empirical and its hit rate is checked in the Accuracy tab.

**Master xP equation (the AER core).**
xP = E[Mins] + Pts_G·λ̃_xG + 3·λ̃_xA + Pts_CS·P(60+)·e^(−λ̃_conceded) + E[bonus] − E[Cards], where (because Σ k·Poisson(k;λ) = λ) the goal and assist terms are the
adjusted rates themselves: λ̃_xG = λ⁰_xG·Ω_attack·α_finish·D_attack, λ̃_xA = λ⁰_xA·Ω_attack·β_assist·D_attack,
λ̃_conceded = λ⁰_opp·(Ω_opp,attack / Ω_team,defence)·Ω_defence-penalty·D_open, with Ω = γ_venue × M_momentum × M_morale.
Where the equation as written would have hurt accuracy, this is what was changed:
• **Momentum** uses recent xG difference relative to the team's *own season level*, capped at ±10%. The GD − xGD version measured finishing luck, which reverses,
so it rewarded the wrong teams.
• **Desperation** is match leverage: from projected final points and games left, how much a win instead of a loss changes the chance of the title, top 5 or survival.
It is large only for teams near a line late in the season, ~0 early on and for teams already safe or doomed, and negative for dead rubbers. It raises a team's own
attacking returns (D_attack), opens the game so both sides keep fewer clean sheets (D_open), and slightly raises card risk. Its strength is **fitted from past gameweeks**
(shrunk toward a prior), not hard-coded, and the earlier bug where a desperate team *conceded less* is gone.
• **Morale / squad availability:** each player's share of his team's recent xGI is weighted by his chance of missing the game. Teammates absorb 25% of that missing output
in proportion to their own share; 50% is lost, which lowers the team's threat and raises the opponent's clean-sheet odds. This replaces the team-wide squad-fitness term
(slider kept, default 0) because it knows *which* players are missing. Flags exist only for upcoming gameweeks, so it is a prior and never enters the learner.
Manager news still comes from your stability-grid edits, and the betting-line shift from logged odds.
• **γ_venue**, **α_finish** (career + season goals÷xG, career shots estimated as xG ÷ 0.10), **β_assist** (teammates' finishing), **tactical errors**
(cards and own goals as the proxy; FPL has no touches, turnovers, dispossessions or errors-leading-to-goal) work as before.
• Where bookmaker prices are used, the context, stakes and availability adjustments step back in proportion to the market weight, since prices already contain them.

**Colours.** Hue = how good, relative to the players shown (red → amber → green). On forecast columns the colour's strength shows
confidence: vivid = a confident forecast, faded = a shaky one. **Confidence %** (grey → deep blue) = 35% sample size + 35% certainty of minutes
(a near-certain starter or non-starter both count as certain) + 30% tightness of his floor–ceiling range relative to his AER. On the My Team pitch the
hue is on an absolute scale (about 1 point red, 4 amber, 7+ dark green) so cards compare across teams.

**👕 My Team.** Pick your team: the page takes your squad (the picks you made for past gameweeks, current ownership for upcoming ones),
finds the highest-scoring legal XI, orders the bench for automatic substitutions and adds the expected bench cover. It does the same for your
head-to-head opponent and gives an approximate win chance. If your saved lineup differs, it lists who to start and bench.

**DPS (process points).** Expected FPL points from stats that repeat, with no goals, assists or bonus actually scored:
appearance points + expected goals × goal points + expected assists × 3 + clean-sheet chance × clean-sheet points + DEFCON chance × 2 + saves
(keepers) − goals-conceded deductions (defenders and keepers) + bonus rate − cards. Each per-90 rate comes from recent games (recent count most)
and is pulled toward the position's norm in proportion to how unreliable that stat is game to game, as measured from the data (empirical Bayes),
so stats that barely repeat are pulled hard and stable ones are trusted. The opponent comes in through the season team ratings (how leaky
they are, how dangerous their attack is). AER − DPS shows whether a player's forecast is running ahead of (above 0) or behind his process.
DPS is tested on past gameweeks against AER, the structural forecast and plain form (Accuracy page). Penalty duty is not yet split out
(it needs penalty-by-penalty history). The old definition was: (DPS formula + FPL xP) ÷ 2, where DPS formula = ((AER + Form) × (xG + xA + creativity + threat + influence)) ÷ √Ceiling and FPL xP is
FPL's own expected points (published only for the current and next gameweek; elsewhere DPS is the formula alone). FPL's historical expected points
are not published, so the FPL xP half cannot be back-tested; the DPS back-test in the Accuracy tab covers the formula part.

**Theta Swole** (the default ranking) = projected points above a replacement-level player at the same position:
θ = (AER + β × DPS residual) − replacement level for his position + risk appetite × (Ceiling − Floor).
• *Why points above replacement:* a raw points ranking over-rates forwards and midfielders and under-rates positions where good players are scarce.
Replacement level is the best player just outside the league's starting pool (number of teams × typical starters: 1 GK, 4 DEF, 4 MID, 2 FWD), so θ
compares every position on the same footing: +2.0 means two points better than what you could otherwise expect to field in that slot.
• *DPS residual:* the part of DPS that AER doesn't already contain. Its weight β is fitted on past gameweeks (ridge-shrunk toward 0, never negative);
if DPS has no extra predictive power, β ≈ 0 and θ is just position-adjusted AER. The old DPS ÷ AER ratio was replaced because dividing by expected
points pushed players projected near zero to the top.
• *Risk appetite* (default 0) lets you favour boom-or-bust players when you're the underdog, or steady ones when you're protecting a lead.
• *Upgrade vs my XI* = a player's points minus your weakest starter at his position: the number that matters for a waiver claim.
θ uses the full 'Gameweeks to sum' horizon for AER.

**Zipf xVR (Zipf Expected-Value Rating).** FPL points are heavy-tailed: a small share of performances produce most of the points.
For every finished gameweek: (1) take everyone who played (N players); (2) rank them by points over expectation, where expectation = what an ordinary player in the same position
would score in the same minutes (Theta Swole's position-relative idea; measuring it against each player's own forecast was tried first and
rewarded surprise rather than quality); (3) split off the top 20% (Pareto's vital few); (4) give each of them credit = points ÷ rank^s × (1 + fraction of 90
minutes played), where s is the Zipf exponent fitted on that gameweek's top 20% (slope of log surplus on log rank), so the rank weighting
follows the law the data actually shows rather than an assumed one; the other 80% get no credit. A logistic model, fitted only on earlier
gameweeks, turns xG, xA, threat, creativity, BPS, minutes, starts, form, DEFCON rate, position, the model's forecast and the player's decayed
past elite rate into his chance of making the next top 20% (Elite %). Zipf xVR = Elite % × his typical elite credit (shrunk toward his
position's average) × a constant that converts credit back to points. FPL's live transfers in (refreshing until the deadline) are added only
when a past season shows they predict the top 20% beyond everything else (z > 2). The Accuracy page shows the fitted exponent, how straight the
log-log line is, the share of points scored by the top 20%, and a walk-forward test of whether xVR picks the top 20%.

**BPS profile and the bonus race.** BPS per 90 minutes from games of 30+ minutes (recent games count most) gives each player an
average, a floor (10th percentile) and a ceiling (90th percentile), each pulled toward his position's norm by three pseudo-games, then ranked
only against his own position. Every fixture's bonus race is simulated 4,000 times: each player starts, comes on or sits out with his
playing-time probabilities, scores a BPS drawn from his own floor–average–ceiling profile (scaled by how good the fixture is for him), and the
top three in the match get 3, 2 and 1 bonus points. That gives xBonus, P(3 bonus) and P(any bonus).

**Zipf xVR with BPS.** Zipf xVR = Elite % × Elite size. Elite % is the chance of making the next gameweek's top 20% (points over
expectation), from a logistic model that now includes the within-position BPS average, floor, ceiling and volatility. Elite size is how big
his top-20% week tends to be (Zipf credit = points ÷ rank^s × (1 + minutes fraction)), predicted by a ridge regression on the same signals and
blended with his own past elite weeks. Both models use only earlier gameweeks. The Accuracy page compares the elite model's AUC with and
without the BPS profile, so you can see whether BPS earns its place.

**Theta Swole and Zipf xVR together.** The two answer different questions: Theta Swole = expected points above a replacement-level player
at the same position (the average week); Zipf xVR = chance × size of a top-20% week (the tail). The app measures how alike they are (rank
correlation and top-20 overlap, now and in past gameweeks) and tests a converged elite model that adds Theta's ingredients (points above
replacement, the DPS signal, floor–ceiling spread and upside) to the Zipf inputs. Each gameweek it is fitted on earlier gameweeks only and
compared with the plain elite model, and with the elite model minus the BPS profile. The app uses whichever set of inputs raises the hit
rate (AUC) by more than 0.002 on average and in at least half the gameweeks; otherwise it keeps the standard set. It re-checks as new results
arrive, so BPS and Theta's ingredients are used only while they earn their place. Theta Swole itself is unchanged, so the two remain separate rankings.

**Haul points and Green score.** Haul points = chance of a top-20% week × the points he scores in a typical haul week (his own haul
weeks, pulled toward his position's typical haul by three pseudo-weeks). It is in real FPL points: the slice of his expected points that
comes from big weeks. The Zipf-weighted xVR index is kept on the Accuracy page as the research behind the haul chance. Green score (0–100)
rates strength across seven areas, each counted once so AER isn't counted several times over: expected points (AER), upside (ceiling and
haul chance), minutes (expected minutes), attack (xG, xA), defence (clean-sheet chance, DEFCON; not scored for forwards), bonus (expected
bonus) and value to your team (WPA, or Pts added). Each area adds how far he is into the green half of the players shown; the average is
faded by confidence. Green areas = how many areas he is in the top quarter for.

**Match predictions** (More → Matchups). For each fixture: expected goals for both sides from season-long Poisson team ratings, where
every team's attack and defence are rated together from all of this season's matches, adjusting for opponent strength (goals blended with
expected goals, older matches fading at a rate chosen by past accuracy, mild shrinkage toward average), blended with bookmaker-implied goals
when odds have been fetched. The predicted score is the likeliest scoreline within the likeliest result; the likeliest exact scores are
listed beside it. Goals are modelled as Poisson with the
Dixon & Coles correction for low scores, giving win/draw/loss chances, the likeliest scores, clean-sheet chances, both-teams-to-score and
over 2.5 goals. Every finished match is re-predicted from earlier games only, and the page reports how often the likeliest result happened
against a no-information guess. The matchup section lists the gameweek's best attacking matchups and, for one fixture, what each side
concedes to defenders, midfielders and forwards as "+30%"-style comparisons with an average team.

**Team news in match predictions.** Each fixture's predicted goals are adjusted for who's missing: each player's share of his team's recent
expected goal involvements (attack) and, for defenders and keepers, of the team's defensive minutes. By default the missing list is FPL's
injury and suspension flags (doubtful players count partly); in the Fixture deep-dive you can add or remove players per team. How strongly
absences move the score is measured on this season's finished matches, treating regulars who played 0 minutes as missing, and re-checked as
results come in; if absences don't measurably help, no adjustment is applied.

**Points calculator** (More → Points calculator). Pick a player and a gameweek in the display, then tap metrics like calculator keys. It
fits the weights and constant for exactly those metrics (on gameweeks before the one shown, optionally only his position group), writes out
the equation with his values filled in, and shows the predicted points with the out-of-sample R² in brackets. For a past gameweek it also
shows what he actually scored. C clears; Best loads the Signal finder's best combination.

**Signal finder** (More → Signal finder). Searches every statistic the model computes before a gameweek for the strongest link to the points
actually scored: each statistic alone (as a straight line and as a shape, in tenths), the best 20 in every pair (straight line with an
interaction term, and a 4 × 4 grid), and the best small combination built one statistic at a time (forward selection, added only if it raises
the out-of-sample R² by more than 0.002). Every gameweek is predicted from earlier gameweeks only, and the most recent gameweeks are held back
entirely to confirm the winners, which guards against finding patterns in noise after trying thousands of combinations. The best pair is
shown as a grid of average points by range of each statistic, and the best combination as a formula.

**Match analyzer** (More → Match analyzer). Pick a gameweek and fixture. For a finished match: the model's pre-match prediction (built only
from earlier matches) against the final score and how likely that result was; Understat's shots as an expected-goals race chart and shot maps;
a "deserved result" from replaying every chance 20,000 times; every player's FPL points broken down and set against his pre-match forecast,
the bonus race, regulars who didn't play, and which of your and your opponent's players featured. Unplayed matches show the prediction.

**Shot maps** (More → Matchups). Pick a fixture and which side is attacking: red squares show where the defending team has conceded shots
(xG per match, by zone), dots show where the attacking team, or one chosen player, has shot from (size = xG, colour = outcome). "His xG from
their weakest zones" = the share of his chances that came from the third of zones where that opponent concedes most. Data comes free from
understat.com (shots only; FPL has no location data) and is cached, so it's quick after the first look.

**Pick Team** works like the FPL app: tap a player, then tap a highlighted player to switch them (only legal formations are offered; two
substitutes can swap bench order). "Rate players by" changes the card numbers and re-picks the XI by that rating; projected points always
count expected points. Transfers below lists the best available replacements for any squad player.

**⚡ DPS selected team** (top of the Rankings tab) replaces the old shortlist: the legal XI (1 GK, 3–5 DEF, 2–5 MID, 1–3 FWD) with the highest total DPS
from the players the filters show, plus a bench that keeps a legal 2/5/5/3 squad. Its projected points count expected points (AER) plus bench cover.
On the My Team page you can choose your own lineup by AER or by DPS. The Accuracy tab compares the actual points of the XI chosen by DPS, by AER and by
Theta Swole's points estimate in past gameweeks, plus each estimate's squared error (DPS's formula part only, since FPL publishes no historical xP).

**📹 Live cams.** Pick a club to see a map of its stadium and the nearest publicly published webcams: Transport for London's JamCams
(free, no key, a new still and short clip every few minutes) for London grounds, and Windy's public webcam network (free key from api.windy.com)
for every ground. Each camera shows its distance from the stadium. These are public street, traffic and city cameras, not club CCTV; most are
periodic snapshots rather than continuous live video, and coverage outside London depends on what cameras exist nearby.

**📰 News.** The latest stories for the clubs you pick (default: every club playing in the target gameweek) from BBC Sport club feeds
(with photos), BBC and Guardian Premier League feeds and a Google News search per club, refreshed every 15 minutes. Each story is tagged with the
clubs and players it mentions and scored 0–10 for selection impact (injuries, suspensions, fitness, manager changes, team news, rotation).
🔴 marks big selection news, 🟠 news that may matter. FPL's own availability news is listed at the top. The AI reads all of this.

**🤖 Ask AI can change the app.** Ask things like "Man City news looks bad, what should their stability be this gameweek?" and it sets the
slider, sizing the change to how serious the news is, and lists every change as old → new. It then asks whether the opponent's slider
should move too and offers its recommendation as a one-click button. It can also change sidebar settings (smoothing, WPA horizon, Solve FPL,
ranking, desperation and more) and a player's chance-of-playing focus. Untick "Let the AI change settings" to get buttons to confirm instead.

**🤖 Ask AI.** A chat assistant that answers anything. It runs free on Google Gemini (free API key from aistudio.google.com) or fully
offline on your Mac with Ollama; Anthropic Claude is available as a paid option. It answers questions such as: how the program works, what a number means,
who to pick up, league standings, FPL rules, football news or general questions. Each question sends the app's documentation (cached, so repeats
are cheaper) and a live snapshot of your squad, the rankings, projections and league table; it can search the web for current news and can
optionally read the full source code.

**WPA — Win Probability Added** (default ranking). Pick your team in the sidebar. For every unowned player the app adds him to your squad,
drops your least valuable player at his position (lowest projected points over the WPA horizon), re-picks your best XI each gameweek (with bench
cover), and compares your chance of beating that week's real head-to-head opponent before and after. Win chance uses a normal approximation:
P(win) = Φ((your projected points − theirs) ÷ √(your spread² + their spread²)), with spreads from each starter's floor–ceiling range. WPA % adds
the changes over the next few gameweeks, later weeks weighted less. Because variance is in the formula, boom-or-bust players help more when you're
the underdog and steady ones when you're the favourite, automatically. **Pts added** is the same exercise for projected points and is the ranking
when your league has no head-to-head fixtures. **Drop** names who you would release.

**📈 Player trends.** Pick up to six players and any weekly stats: charts show each gameweek (or a 3-week rolling average, the running season total,
or per-90 rates), actual points against the model's forecast made before each gameweek plus the projection ahead, a season profile table
(goals vs xG, per-90 rates, forecast bias and miss) and per-90 percentiles within position. Last season can be added (matched by name).

**🎯 Tune on all history.** The app downloads past seasons' match-by-match data (public vaastav/Fantasy-Premier-League dataset), rebuilds
every past gameweek's forecast using only the weeks before it, and uses those player-weeks in three ways: (1) the tuner searches 16 settings
(windows, smoothing, half-lives, xG trust, team memory, minutes model, venue, momentum, discipline, finishing) for the lowest squared error between
forecast and actual points across this season and past seasons, then checks the winner on gameweeks it never saw; (2) the learned correction,
the fitted stability/HMPR/desperation effects, the DPS signal and Solve FPL all train on this season plus past seasons, with older weeks weighted
less; (3) the Accuracy tab scores all of it. The best settings are saved to fpl_tuned_params.json and applied at the start of every session.
Past-season players are tied to the club they finished the season at, so rows from before a mid-season transfer are left out.

**Team stability**, **HMPR**, **matchup matrix**, **learned minutes model**, **long-memory team strengths**, **bookmaker priors**, **Solve FPL** and **DPS**
work as described in their captions. Every fitted strength is validated walk-forward in the Accuracy tab.

**Statistical methods:** independent-Poisson goal model (Maher 1982) with exponential time decay (Dixon & Coles 1997); empirical-Bayes shrinkage
(Efron & Morris 1975; Morris 1983); exact expected values for stepped scoring; gradient-boosted residual learning (Friedman 2001) stacked on the structural
forecast (Wolpert 1992) with rolling-origin validation (Tashman 2000); conformalised quantile regression (Romano, Patterson & Candès 2019).

**Not available in the FPL API:** touches, turnovers, dispossessions, shots, errors leading to goals, pass receivers, referees and historical odds.
"""

if page == "Guide":
    with st.expander(":material/help: Control guide", expanded=False):
        for sec_, items_ in HELP_GUIDE:
            st.markdown(f"**{sec_}**")
            st.table(pd.DataFrame({"Control": [lbl_ for lbl_, _ in items_], "What it does": [HELP[k_] for _, k_ in items_]}).set_index("Control"))
        st.markdown("**Stability recipe ingredients** (standard deviations vs the league; raising any one can only raise stability)")
        st.table(pd.DataFrame({"Ingredient": [STAB_LABELS[k_] for k_ in STAB_KEYS],
                               "What it measures": [INGREDIENT_HELP[k_] for k_ in STAB_KEYS]}).set_index("Ingredient"))
    st.markdown(DOCS_MD)


# =========================================================
# AI ASSISTANT — answers questions about the program, your league, FPL and anything else
# =========================================================
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
AI_INSTRUCTIONS = """You are the AI assistant built into "FPL Draft AER", a Streamlit app that forecasts Fantasy Premier League Draft points.
Answer any question the user asks: about this program (how every number is calculated, what each control does, how to use it), about their
league, team, opponents and players, about FPL and FPL Draft rules and strategy, about football generally, or about anything else.

How to answer:
- Use the APP DOCUMENTATION and the LIVE APP STATE below as your primary source for anything about the program, the league or the forecasts.
  Quote the actual numbers from the live state (AER, WPA, win chance, projections) rather than generic advice.
- When recommending a move, say what the numbers are, why, and the main risk (minutes, injury, fixture, small sample).
- The forecasts are expected values; single-gameweek FPL points are mostly luck. Be honest about uncertainty without being vague.
- For current events (injuries, press conferences, team news, transfers, rule changes), use web search if it's available and say where the
  information came from. If you can't verify something, say so rather than guessing.
- The LIVE APP STATE includes the latest news headlines and FPL availability news. Use them to explain how news should change the
  forecasts, and say which stories you relied on.

CHANGING THE APP'S SETTINGS
You can change sliders and settings. When the user asks you to change, set or adjust something, or asks what a setting SHOULD be
(e.g. "what should Man City's away stability be this gameweek?"), decide the value and set it by ending your reply with a block exactly like:
<<ACTIONS>>
[{"action": "set_stability", "team": "MCI", "gw": 7, "value": -0.45},
 {"action": "set_setting", "name": "desperation_impact", "value": 1.5},
 {"action": "set_focus", "player": "Haaland", "team": "MCI", "value": 0.6},
 {"action": "set_stability", "team": "ARS", "gw": 7, "value": 0.15, "suggest": true}]
<<END>>
Rules:
- set_stability: team = a team code from the fixture list in the live state; gw = the gameweek; value from -1 to +1 (steps of 0.05).
  Scale: -1 chaos, 0 normal, +1 driven and settled. Calibrate the size of the change to the news: minor or uncertain news ±0.05–0.2 from the
  current value; significant (a key player out, manager under pressure, poor run) ±0.25–0.5; extreme (sacking, several starters out, scandal,
  sanctions) ±0.55 or more. Good news moves it up by the same logic.
- After changing one team's stability for a fixture, ALWAYS end your text by asking whether the opponent's slider for that fixture should change
  too, and include your recommended opponent value as a suggestion ("suggest": true) relative to its current value (e.g. a small lift if the
  opponent benefits from the other side's turmoil, or no change if it doesn't matter). Also do this if the user explicitly asks about the opponent.
- set_setting: name must be one of the settings listed in the live state; keep within its range.
- set_focus: a player's chance-of-playing multiplier for the target gameweek (0 = won't play, 0.5 = heavy rotation risk, 1 = normal, 1.2 = nailed).
- Use "suggest": true for changes you are proposing but were not asked to make; the app shows them as buttons the user can accept.
- Only include the block when making or suggesting changes. In your text, state each change as old → new and why.
- Write in plain, direct language. Keep answers as short as the question allows; use a short list or table only when it genuinely helps.
"""


def _df_text(d, n=None):
    try:
        d = d if n is None else d.head(n)
        return d.to_csv(index=False, float_format="%.2f")
    except Exception:
        return ""


def ai_static_context(include_source=False):
    guide = "\n".join(f"[{sec}] {lbl}: {HELP[k]}" for sec, items in HELP_GUIDE for lbl, k in items)
    ingred = "\n".join(f"- {STAB_LABELS[k]}: {INGREDIENT_HELP[k]}" for k in STAB_KEYS)
    txt = (AI_INSTRUCTIONS + "\n\n=== APP DOCUMENTATION ===\n" + DOCS_MD + "\n\n=== CONTROL GUIDE (every sidebar/page control) ===\n" + guide
           + "\n\nStability recipe ingredients:\n" + ingred)
    if include_source:
        try:
            with open(__file__, encoding="utf-8") as f_:
                txt += "\n\n=== FULL PROGRAM SOURCE CODE ===\n" + f_.read()
        except Exception:
            pass
    return txt


def ai_live_context():
    """Snapshot of what the app is showing right now, built from whatever has been computed in this run."""
    G = globals()
    parts = [f"Today: {time.strftime('%A %d %B %Y')}. FPL Draft league ID: {G.get('league_id')}. Next unfinished gameweek: GW{G.get('next_gw')}. "
             f"Target gameweek on screen: GW{G.get('selected_gw')} (summing {G.get('horizon')} gameweek(s)). Ranking by: {G.get('rank_by')}."]
    lg = G.get("_league") or {}
    ents = lg.get("entries", pd.DataFrame())
    my = G.get("my_entry_sel")
    if isinstance(ents, pd.DataFrame) and len(ents) and my is not None:
        nm = ents.loc[ents["id"].astype(int) == int(my), "entry_name"]
        parts.append(f"The user's team: {nm.iloc[0] if len(nm) else my} (league entry {my}).")
    elif my is None:
        parts.append("The user has not picked their team in the sidebar yet.")
    # league table from finished head-to-head matches
    mt = lg.get("matches", pd.DataFrame())
    if isinstance(mt, pd.DataFrame) and len(mt) and "finished" in mt.columns and isinstance(ents, pd.DataFrame) and len(ents):
        f_ = mt[mt["finished"].astype(bool)]
        rows = []
        for _, e in ents.iterrows():
            le = int(e["id"])
            a_ = f_[f_["league_entry_1"] == le]
            b_ = f_[f_["league_entry_2"] == le]
            pf = list(a_["league_entry_1_points"]) + list(b_["league_entry_2_points"])
            pa = list(a_["league_entry_2_points"]) + list(b_["league_entry_1_points"])
            w_ = sum(x > y for x, y in zip(pf, pa)); d_ = sum(x == y for x, y in zip(pf, pa)); l_ = len(pf) - w_ - d_
            rows.append({"Team": e["entry_name"], "P": len(pf), "W": w_, "D": d_, "L": l_, "Pts": 3 * w_ + d_, "Points for": sum(pf),
                         "Points against": sum(pa)})
        if rows:
            tab = pd.DataFrame(rows).sort_values(["Pts", "Points for"], ascending=False)
            parts.append("LEAGUE TABLE (head-to-head, 3 pts per win):\n" + _df_text(tab))
        up = mt[~mt["finished"].astype(bool)]
        if my is not None and len(up):
            mine_up = up[(up["league_entry_1"] == my) | (up["league_entry_2"] == my)].sort_values("event").head(5)
            nm_map = dict(zip(ents["id"].astype(int), ents["entry_name"]))
            fx_txt = ", ".join(f"GW{int(r['event'])} vs {nm_map.get(int(r['league_entry_2'] if int(r['league_entry_1']) == my else r['league_entry_1']), '?')}"
                               for _, r in mine_up.iterrows())
            parts.append("User's upcoming head-to-head fixtures: " + fx_txt)
    fd = G.get("full_df")
    my_ids = G.get("_my_ids") or []
    if isinstance(fd, pd.DataFrame) and my_ids:
        sq = fd.loc[[i for i in my_ids if i in fd.index]]
        cols = {"web_name": "Player", "position": "Pos", "team_code": "Team", "Opponent": "Opp", "AER1": "AER", "DPS": "DPS", "Theta": "Theta",
                "Floor": "Floor", "Ceiling": "Ceiling", "Confidence": "Conf", "appear": "P(plays)", "Status": "Status", "form": "Form"}
        sq = sq[[c for c in cols if c in sq.columns]].rename(columns=cols).sort_values("AER", ascending=False)
        parts.append("USER'S SQUAD (first target gameweek):\n" + _df_text(sq))
    mine, theirs = G.get("mine"), G.get("theirs")
    if isinstance(mine, dict):
        s = (f"My Team page: best lineup {mine.get('form')} projects {mine.get('total', 0):.1f} pts "
             f"(XI {mine.get('base', 0):.1f} + bench cover {mine.get('sub', 0):.1f}); starters: {', '.join(mine['xi']['web_name'].astype(str))}; "
             f"bench: {', '.join(mine['bench']['web_name'].astype(str))}.")
        if isinstance(theirs, dict):
            s += f" Opponent {theirs.get('name')} best lineup projects {theirs.get('total', 0):.1f} pts."
        parts.append(s)
    wi = G.get("wpa_info")
    if isinstance(wi, dict) and wi.get("pwin"):
        parts.append("User's win chance by gameweek (before any pickup): " + ", ".join(f"GW{g} {p * 100:.0f}%" for g, p in wi["pwin"].items()))
    v = G.get("view")
    if isinstance(v, pd.DataFrame) and len(v):
        keep = [c for c in ["Rank", "Player", "Pos", "Team", "Opp", "WPA %", "Pts added", "Drop", "Haul pts", "Elite %", "Haul size", "BPS avg", "BPS floor", "BPS ceil", "xBonus", "P(3 bonus) %", "Theta Swole", "Upgrade vs my XI", "DPS",
                            "FPL xP", "AER", "Confidence %", "Floor", "Ceiling", "Mins", "Form", "xG", "xA", "CS %", "Status", "Chance %", "News"]
                if c in v.columns]
        parts.append(f"RANKINGS TABLE as currently filtered (top 40 of {len(v)}):\n" + _df_text(v[keep], 40))
    dxi = G.get("dxi_")
    if isinstance(dxi, pd.DataFrame) and len(dxi):
        parts.append(f"DPS selected team ({G.get('dform_')}): " + ", ".join(dxi["web_name"].astype(str)))
    if isinstance(fd, pd.DataFrame) and len(fd):
        top = fd.sort_values("AER1", ascending=False)
        cols = [c for c in ["web_name", "position", "team_code", "Opponent", "AER1", "DPS", "Theta", "Status", "news"] if c in top.columns]
        parts.append("TOP 30 PLAYERS IN THE GAME BY AER (owned or not):\n" + _df_text(top[cols], 30))
    nd = G.get("news_df")
    if isinstance(nd, pd.DataFrame) and len(nd):
        top_n = nd.assign(_r=np.arange(len(nd))).sort_values(["Impact", "_r"], ascending=[False, True]).head(35)
        lines_n = [f"- [{_ago(r_['When'])}] {r_['Title']} ({r_['Source']}; clubs {r_['Clubs'] or '?'}"
                   f"{'; players ' + r_['Players'] if r_['Players'] else ''}; impact {r_['Impact']}/10): {r_['Summary'][:160]}"
                   for _, r_ in top_n.iterrows()]
        parts.append("LATEST NEWS (most selection-relevant first; headline and short summary only):\n" + "\n".join(lines_n))
    if "news" in players.columns:
        inj_ = players[players["news"].astype(str).str.len() > 0]
        if len(inj_):
            inj_ = inj_.assign(_t=pd.to_datetime(inj_.get("news_added"), errors="coerce", utc=True)).sort_values("_t", ascending=False).head(45)
            parts.append("FPL AVAILABILITY NEWS:\n" + "\n".join(f"- {r_['web_name']} ({r_['team_code']}, {r_['position']}): {r_['news']} "
                                                                  f"[chance {r_.get('chance_of_playing_next_round')}%]"
                                                                  for _, r_ in inj_.iterrows()))
    sp, am = G.get("stab_pairs") or {}, G.get("auto_map") or {}
    if sp:
        fxl = fx[fx["event"].isin(G.get("gws_shown") or [G.get("selected_gw")])]
        rows_s = [f"GW{int(r_['event'])}: {code[int(r_['team_h'])]} (home) current {sp.get((r_['id'], r_['team_h']), am.get(r_['team_h'], 0)):+.2f}, "
                  f"auto {am.get(r_['team_h'], 0):+.2f}  vs  {code[int(r_['team_a'])]} (away) current "
                  f"{sp.get((r_['id'], r_['team_a']), am.get(r_['team_a'], 0)):+.2f}, auto {am.get(r_['team_a'], 0):+.2f}"
                  for _, r_ in fxl.iterrows()]
        parts.append("FIXTURES AND TEAM STABILITY SLIDERS (−1 to +1; team codes for set_stability):\n" + "\n".join(rows_s))
    parts.append("SETTINGS YOU CAN CHANGE (name: current value, range):\n" + "\n".join(
        f"- {nm}: {st.session_state.get(spec[0])} ({spec[1]}{'' if spec[1] in ('bool', 'choice') else f' {spec[2]} to {spec[3]}'}"
        f"{', options ' + str(spec[4]) if spec[1] == 'choice' else ''}) — {spec[5]}" for nm, spec in AI_SETTABLE.items()))
    ov_, m_ = G.get("ov"), G.get("m")
    if isinstance(ov_, dict):
        parts.append(f"Out-of-sample accuracy: R² AER {ov_.get('r2_aer', float('nan')):.3f}, R² base {ov_.get('r2_base', float('nan')):.3f}, "
                     f"R² form-only {ov_.get('r2_form', float('nan')):.3f}.")
    tn = st.session_state.get("last_tune") or G.get("_saved_tune")
    if isinstance(tn, dict):
        parts.append(f"Last history tune ({tn.get('when')}): squared error {tn.get('before', 0):.3f} -> {tn.get('after', 0):.3f}; settings {tn.get('params')}.")
    pp = G.get("p")
    if isinstance(pp, dict):
        parts.append("Current model settings: " + ", ".join(f"{k}={v_}" for k, v_ in sorted(pp.items())))
    return "=== LIVE APP STATE ===\n" + "\n\n".join(parts)


def ask_ai(messages, key, model, web=True, include_source=False, max_rounds=4):
    """One answer from the Anthropic Messages API. Static documentation is prompt-cached; the live snapshot is sent fresh each time.
    Returns (text, sources)."""
    system = [{"type": "text", "text": ai_static_context(include_source), "cache_control": {"type": "ephemeral"}},
              {"type": "text", "text": ai_live_context()}]
    body = {"model": model, "max_tokens": 2500, "system": system, "messages": list(messages)}
    if web:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    texts, sources = [], {}
    for _ in range(max_rounds):
        r = requests.post(ANTHROPIC_URL, headers=headers, json=body, timeout=180)
        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message", r.text[:300])
            except Exception:
                msg = r.text[:300]
            raise RuntimeError(f"Anthropic API error {r.status_code}: {msg}")
        js = r.json()
        for blk in js.get("content", []):
            if blk.get("type") == "text":
                texts.append(blk.get("text", ""))
                for c in blk.get("citations") or []:
                    if c.get("url"):
                        sources[c["url"]] = c.get("title") or c["url"]
        if js.get("stop_reason") != "pause_turn":      # long web searches pause; send the partial turn back to let it continue
            break
        body["messages"] = list(messages) + [{"role": "assistant", "content": js.get("content", [])}]
    return "".join(texts).strip(), sources


def _api_error(r, name):
    try:
        js = r.json()
        msg = js.get("error", {}).get("message") if isinstance(js.get("error"), dict) else js.get("error")
    except Exception:
        msg = None
    return RuntimeError(f"{name} error {r.status_code}: {msg or r.text[:300]}")


GEMINI_FREE_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite"]


def _gemini_error(r):
    """Google's own error message plus its suggested wait, if any."""
    try:
        err = r.json().get("error", {})
    except Exception:
        return r.text[:300], ""
    wait = ""
    for d_ in err.get("details", []) or []:
        if "RetryInfo" in str(d_.get("@type", "")) and d_.get("retryDelay"):
            wait = str(d_["retryDelay"])
    return str(err.get("message", r.text[:300])), wait


def _gemini_post(model, key, body):
    return requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model.strip()}:generateContent",
                         headers={"x-goog-api-key": key.strip(), "content-type": "application/json"}, json=body, timeout=180)


def ask_gemini(messages, key, model, web=True, include_source=False):
    """Google Gemini (free tier), native generateContent API. Tries Google Search grounding first; if the key has no search quota
    (429) or search isn't supported (400/403), it retries without search. If a model has no free quota, it moves on to the other free
    models. Every refusal is reported with Google's own message."""
    body = {"systemInstruction": {"parts": [{"text": ai_static_context(include_source) + "\n\n" + ai_live_context()}]},
            "contents": [{"role": "model" if m_["role"] == "assistant" else "user", "parts": [{"text": m_["content"]}]} for m_ in messages],
            "generationConfig": {"maxOutputTokens": 4096}}
    models = [model.strip()] + [m_ for m_ in GEMINI_FREE_MODELS if m_ != model.strip()]
    tried = []
    for mdl in models:
        for search in ([True, False] if web else [False]):
            b_ = dict(body)
            if search:
                b_["tools"] = [{"google_search": {}}]
            r = _gemini_post(mdl, key, b_)
            if r.status_code == 200:
                js = r.json()
                cand = (js.get("candidates") or [{}])[0]
                text = "".join(p_.get("text", "") for p_ in cand.get("content", {}).get("parts", []) if not p_.get("thought"))
                if not text:
                    tried.append((mdl, search, 200, cand.get("finishReason") or "no text returned", ""))
                    continue
                sources = {}
                for ch in (cand.get("groundingMetadata") or {}).get("groundingChunks", []) or []:
                    w_ = ch.get("web") or {}
                    if w_.get("uri"):
                        sources[w_["uri"]] = w_.get("title") or w_["uri"]
                notes = []
                if mdl != model.strip():
                    notes.append(f"answered by {mdl} because {model.strip()} was unavailable on your key")
                if web and not search:
                    notes.append("no web search: your key has no free search quota right now")
                return text.strip() + (f"\n\n_({'; '.join(notes)}.)_" if notes else ""), sources
            msg, wait = _gemini_error(r)
            tried.append((mdl, search, r.status_code, msg, wait))
            if r.status_code in (401,) or "API key not valid" in msg or "API_KEY_INVALID" in msg:
                raise RuntimeError("Google says the API key isn't valid. Create a new one at aistudio.google.com → Get API key, and paste it again.")
            if r.status_code not in (400, 403, 404, 429, 500, 503):
                raise RuntimeError(f"Gemini error {r.status_code} on {mdl}: {msg}")
    last = tried[-1] if tried else ("?", False, "?", "no response", "")
    zero = any("limit: 0" in t_[3] for t_ in tried)
    hint = ("Google reports a free quota of 0 for these models on your key. That usually means the key's Google Cloud project isn't eligible for "
            "the free tier (for example, it was created in a project that has had billing changes). Create a fresh key in a new project at "
            "aistudio.google.com → Get API key → Create API key in new project.") if zero else \
           (f"Google asks you to wait {last[4]} before trying again." if last[4] else "Wait a minute and try again.")
    raise RuntimeError(f"Gemini refused every free model ({', '.join(sorted({t_[0] for t_ in tried}))}). {hint}\n\n"
                       f"Last message from Google ({last[0]}, HTTP {last[2]}): {last[3]}\n\n"
                       "Use **🔌 Test Gemini connection** below to see each model's response, or switch the provider to Ollama (fully free, local).")


def gemini_diagnose(key):
    """Tiny 'reply OK' request to each free model, with and without search, so the user can see exactly what their key allows."""
    rows = []
    for mdl in GEMINI_FREE_MODELS:
        for search in (False, True):
            b_ = {"contents": [{"role": "user", "parts": [{"text": "Reply with the single word OK."}]}], "generationConfig": {"maxOutputTokens": 20}}
            if search:
                b_["tools"] = [{"google_search": {}}]
            try:
                r = _gemini_post(mdl, key, b_)
                msg, wait = ("OK", "") if r.status_code == 200 else _gemini_error(r)
                rows.append({"Model": mdl, "Web search": "yes" if search else "no", "HTTP": r.status_code,
                             "Result": "✅ works" if r.status_code == 200 else "❌ " + msg[:160], "Retry after": wait})
            except Exception as e_:
                rows.append({"Model": mdl, "Web search": "yes" if search else "no", "HTTP": "-", "Result": f"❌ {e_}", "Retry after": ""})
    return pd.DataFrame(rows)


def free_web_snippets(query, n=5):
    """Free web search for Ollama via DuckDuckGo (needs the 'ddgs' package). Returns (text block, sources) or ('', {})."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
    except ImportError:
        return "", {}
    try:
        res = list(DDGS().text(query, max_results=n))
    except Exception:
        return "", {}
    lines, src_ = [], {}
    for r_ in res:
        u_ = r_.get("href") or r_.get("url")
        if u_:
            src_[u_] = r_.get("title") or u_
            lines.append(f"- {r_.get('title', '')} ({u_}): {r_.get('body', '')}")
    return ("\n\n=== WEB SEARCH RESULTS (DuckDuckGo, for the latest question) ===\n" + "\n".join(lines)) if lines else "", src_


def ask_ollama(messages, host, model, web=True):
    """A local model through Ollama (free, private). Uses a 16k-token context so the documentation and live snapshot fit."""
    extra, sources = free_web_snippets(messages[-1]["content"]) if web else ("", {})
    sys_ = ai_static_context(False) + "\n\n" + ai_live_context() + extra
    body = {"model": model.strip(), "stream": False, "options": {"num_ctx": 16384},
            "messages": [{"role": "system", "content": sys_}] + [{"role": m_["role"], "content": m_["content"]} for m_ in messages]}
    try:
        r = requests.post(f"{host.strip().rstrip('/')}/api/chat", json=body, timeout=900)
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Ollama isn't running. Install it from ollama.com, open it, then in Terminal run:  ollama pull "
                           f"{model.strip() or 'llama3.2'}")
    if r.status_code == 404:
        raise RuntimeError(f"Ollama doesn't have the model '{model.strip()}'. In Terminal run:  ollama pull {model.strip()}")
    if r.status_code != 200:
        raise _api_error(r, "Ollama")
    text = ((r.json().get("message") or {}).get("content") or "").strip()
    if web and not extra:
        text += "\n\n_(No live web search: install the free 'ddgs' package with  python3 -m pip install ddgs  to enable it.)_"
    return text, sources


# name: (session_state key, kind, min, max, step-or-options, description)
AI_SETTABLE = {
    "window": ("s_window", "int", 1, 8, 1, "recent games used (home/away split)"),
    "player_pull": ("s_player_pull", "float", 0.0, 6.0, 0.5, "recent-form smoothing"),
    "minutes_pull": ("s_minutes_pull", "float", 0.0, 3.0, 0.5, "minutes smoothing"),
    "team_pull": ("s_team_pull", "float", 0.0, 3.0, 0.25, "team smoothing"),
    "xg_weight": ("s_xg_weight", "float", 0.0, 1.0, 0.05, "trust xG/xA over actual goals/assists"),
    "form_hl": ("s_form_hl", "int", 1, 12, 1, "recent-form half-life in gameweeks"),
    "minutes_hl": ("s_minutes_hl", "int", 1, 8, 1, "minutes half-life"),
    "prior_strength": ("s_prior_strength", "float", 0.25, 3.0, 0.25, "season shrinkage"),
    "team_long": ("s_team_long", "float", 0.0, 1.0, 0.1, "long-memory team strength weight"),
    "mins_ml_w": ("s_mins_ml_w", "float", 0.0, 1.0, 0.1, "learned minutes model weight"),
    "ctx_pow": ("s_ctx_pow", "float", 0.0, 1.5, 0.25, "master-equation context strength"),
    "w_venue": ("s_w_venue", "float", 0.0, 1.0, 0.05, "venue weight"),
    "mom_cap": ("s_mom_cap", "float", 0.0, 0.2, 0.01, "momentum cap"),
    "psi": ("s_psi", "float", 0.0, 0.5, 0.05, "defensive-error sensitivity"),
    "sigma_disc": ("s_sigma_disc", "float", 0.0, 1.0, 0.05, "discipline sensitivity"),
    "k_reg": ("s_k_reg", "int", 50, 600, 25, "finishing regression K"),
    "desperation_impact": ("s_desperation_impact", "float", 0.0, 3.0, 0.25, "how much match stakes matter"),
    "leader_boost": ("s_leader_boost", "float", 0.0, 1.5, 0.1, "captain/veteran sensitivity to stability"),
    "hmpr_impact": ("s_hmpr_impact", "float", 0.0, 3.0, 0.25, "historical match rating impact"),
    "ep_w": ("s_ep_w", "float", 0.0, 0.5, 0.05, "blend of FPL's own expected points"),
    "risk_kappa": ("s_risk_kappa", "float", -0.3, 0.3, 0.05, "Theta Swole risk appetite"),
    "wpa_h": ("s_wpa_h", "int", 1, 5, 1, "WPA gameweeks ahead"),
    "wpa_decay": ("s_wpa_decay", "float", 0.5, 1.0, 0.05, "WPA later-weeks weight"),
    "horizon": ("s_horizon", "int", 1, 6, 1, "gameweeks to sum in the rankings"),
    "use_squad_avail": ("s_use_squad_avail", "bool", None, None, None, "redistribute injured players' output"),
    "use_learning": ("use_learning", "bool", None, None, None, "learned correction on/off"),
    "god_on": ("god_on", "bool", None, None, None, "Solve FPL on/off"),
    "god_conv": ("god_conv", "float", 0.0, 2.0, 0.1, "Solve FPL conviction"),
    "rank_by": ("rank_by", "choice", None, None, ["WPA", "Green score", "Haul points", "Theta Swole", "AER", "DPS"], "ranking order"),
}
ACTION_RE = re.compile(r"<<ACTIONS>>(.*?)(?:<<END>>|$)", re.S)


def parse_actions(text):
    """Pulls the <<ACTIONS>> JSON block (or a ```json list of actions) out of a reply. Returns (reply without the block, actions)."""
    m = ACTION_RE.search(text or "")
    raw = m.group(1) if m else None
    if raw is None:
        m2 = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text or "", re.S)
        if m2 and '"action"' in m2.group(1):
            raw, m = m2.group(1), m2
    if raw is None:
        return text, []
    try:
        acts = json.loads(raw.strip().strip("`").replace("json\n", "", 1))
        acts = acts if isinstance(acts, list) else [acts]
    except Exception:
        acts = []
    return (text[: m.start()] + text[m.end():]).strip(), [a_ for a_ in acts if isinstance(a_, dict)]


def _team_id(t_):
    t_ = str(t_ or "").strip()
    for i_, sc in code.items():
        if sc.upper() == t_.upper():
            return int(i_)
    for i_, nm in zip(teams["id"], teams["name"]):
        if _norm(nm) == _norm(t_) or _norm(t_) in [_norm(x_) for x_ in CLUB_NICK.get(code.get(int(i_), ""), [])]:
            return int(i_)
    return None


def resolve_action(a):
    """Validates one action. Returns (kind, payload, description) or (None, None, reason)."""
    kind = a.get("action")
    if kind == "set_stability":
        tid = _team_id(a.get("team"))
        gw_ = int(a.get("gw") or selected_gw)
        if tid is None:
            return None, None, f"unknown team {a.get('team')}"
        fxa = fx[(fx["event"] == gw_) & ((fx["team_h"] == tid) | (fx["team_a"] == tid))]
        if fxa.empty:
            return None, None, f"{code.get(tid)} has no fixture in GW{gw_}"
        r_ = fxa.iloc[0]
        opp = int(r_["team_a"] if int(r_["team_h"]) == tid else r_["team_h"])
        val = float(np.clip(round(float(a.get("value", 0)) / 0.05) * 0.05, -1, 1))
        cur = (G_ := globals()).get("stab_pairs", {}).get((r_["id"], tid), G_.get("auto_map", {}).get(tid, 0.0))
        return "stab", (gw_, tid, val, opp), f"{code.get(tid)} stability GW{gw_} (vs {code.get(opp)}) {float(cur):+.2f} → {val:+.2f}"
    if kind == "set_setting":
        nm = str(a.get("name", ""))
        if nm not in AI_SETTABLE:
            return None, None, f"unknown setting {nm}"
        key_, typ, lo, hi, step, _ = AI_SETTABLE[nm]
        v_ = a.get("value")
        try:
            if typ == "bool":
                v_ = v_ if isinstance(v_, bool) else str(v_).lower() in ("true", "1", "on", "yes")
            elif typ == "choice":
                v_ = next((o_ for o_ in step if o_.lower() == str(v_).lower()), None)
                if v_ is None:
                    return None, None, f"{nm} must be one of {step}"
            elif typ == "int":
                v_ = int(np.clip(round(float(v_)), lo, hi))
            else:
                v_ = float(np.clip(round(float(v_) / step) * step, lo, hi))
                v_ = round(v_, 4)
        except Exception:
            return None, None, f"bad value for {nm}"
        return "set", (key_, v_), f"{nm} {st.session_state.get(key_)} → {v_}"
    if kind == "set_focus":
        tid = _team_id(a.get("team")) if a.get("team") else None
        nm = _norm(a.get("player", ""))
        cand = players[(players["web_name"].map(_norm) == nm) | (players["second_name"].map(_norm) == nm)
                       | ((players["first_name"].map(_norm) + " " + players["second_name"].map(_norm)) == nm)]
        if tid is not None:
            cand = cand[cand["team"].astype(int) == tid]
        if len(cand) != 1:
            return None, None, f"couldn't identify player {a.get('player')}"
        val = float(np.clip(round(float(a.get("value", 1)) / 0.05) * 0.05, 0, 1.5))
        return "focus", (int(cand["id"].iloc[0]), val), f"{cand['web_name'].iloc[0]} chance-of-playing multiplier → {val:.2f}"
    return None, None, f"unknown action {kind}"


def apply_resolved(kind, payload):
    if kind == "stab":
        gw_, tid, val, _ = payload
        st.session_state.setdefault("stab_override", {})[f"{gw_}_{tid}"] = val
    elif kind == "set":
        pend = st.session_state.get("god_pending") or {}
        pend[payload[0]] = payload[1]
        st.session_state["god_pending"] = pend
    elif kind == "focus":
        st.session_state.setdefault("focus_pending", []).append(payload)


def _stab_now(gw_, tid):
    """Current slider value for a team's fixture in a gameweek (what the user sees), else its auto score."""
    r_ = fx[(fx["event"] == gw_) & ((fx["team_h"] == tid) | (fx["team_a"] == tid))]
    am_ = globals().get("auto_map", {}) or {}
    if r_.empty:
        return float(am_.get(tid, 0.0))
    return float((globals().get("stab_pairs") or {}).get((r_.iloc[0]["id"], tid), am_.get(tid, 0.0)))


def queue_opponent_prompt(gw_, tid, opp, old, new):
    """After a team's stability changes, offer the opponent: ask the AI, mirror the change, or leave it."""
    q_ = (f"Should {code.get(opp)}'s stability for GW{gw_} change too, given {code.get(tid)} moved {old:+.2f} → {new:+.2f}? "
          "Set it to what you think is best, relative to its current value.")
    st.session_state.setdefault("ai_followups", []).append(q_)
    st.session_state.setdefault("ai_mirror", []).append({"gw": gw_, "team": tid, "opp": opp, "delta": new - old})


def handle_actions(acts, auto=True):
    """Applies (or, if not auto / marked suggest, offers) the AI's actions. Returns (applied descriptions, suggestions, follow-up questions, errors)."""
    applied, sugg, follow, errs, touched, mirrors = [], [], [], [], set(), []
    for a_ in acts:
        kind, payload, desc = resolve_action(a_)
        if kind is None:
            errs.append(desc)
            continue
        if a_.get("suggest") or not auto:
            sugg.append({"kind": kind, "payload": payload, "desc": desc})
        else:
            old_ = _stab_now(payload[0], payload[1]) if kind == "stab" else None
            apply_resolved(kind, payload)
            applied.append(desc)
            if kind == "stab":
                mirrors.append({"gw": payload[0], "team": payload[1], "opp": payload[3], "delta": payload[2] - old_})
        if kind == "stab":
            touched.add((payload[0], payload[1]))
    for gw_, tid in list(touched):
        opp = next((s_["payload"][3] for s_ in sugg if s_["kind"] == "stab" and s_["payload"][:2] == (gw_, tid)), None)
        r_ = fx[(fx["event"] == gw_) & ((fx["team_h"] == tid) | (fx["team_a"] == tid))]
        if len(r_):
            opp = int(r_.iloc[0]["team_a"] if int(r_.iloc[0]["team_h"]) == tid else r_.iloc[0]["team_h"])
            if (gw_, opp) not in touched:
                follow.append(f"Should {code.get(opp)}'s stability for GW{gw_} change too, relative to the change for {code.get(tid)}?")
    st.session_state["ai_mirror"] = [m_ for m_ in mirrors if (m_["gw"], m_["opp"]) not in touched]
    return applied, sugg, follow, errs


def ask_any(messages):
    if ai_provider.startswith("Google"):
        return ask_gemini(messages, ai_key, ai_model, ai_web, ai_source)
    if ai_provider.startswith("Ollama"):
        return ask_ollama(messages, ai_host, ai_model, ai_web)
    return ask_ai(messages, ai_key.strip(), ai_model, ai_web, ai_source)


AI_SUGGESTIONS = ["Who should I pick up this week, and who should I drop?", "What's my win chance this gameweek and how can I raise it?",
                  "Explain how WPA and Theta Swole are calculated.", "Which of my players are most at risk of not playing?",
                  "What are the key FPL Draft rules I should know?"]

if page == "Ask AI":
    if not isinstance(globals().get("news_df"), pd.DataFrame) or news_df.empty:
        try:
            _fxn = fx[fx["event"] == selected_gw]
            _ids = sorted({int(x_) for x_ in pd.concat([_fxn["team_h"], _fxn["team_a"]])}) if len(_fxn) else sorted(teams["id"].astype(int))
            _nm = dict(zip(teams["id"].astype(int), teams["name"]))
            news_df = tag_news(fetch_news(tuple(code[i_] for i_ in _ids), tuple(_nm[i_] for i_ in _ids), 3), teams, players)
        except Exception:
            news_df = pd.DataFrame()
    st.subheader("Ask anything")
    st.caption("Ask about this program, your league, your players, FPL rules and strategy, football, or anything else. The assistant sees the "
               "app's documentation and a live snapshot of what's on screen (your squad, rankings, projections, league table), and can look up "
               f"current news if web search is on. Provider: **{ai_provider}** · model **{ai_model}**.")
    if not ai_ready:
        if ai_provider.startswith("Google"):
            st.info("**Free setup (2 minutes):** go to aistudio.google.com, sign in with a Google account, click **Get API key → Create API key**, "
                    "and paste it into **🤖 AI assistant → Gemini API key** in the sidebar. No card needed.")
        else:
            st.info("Add your key under **🤖 AI assistant** in the sidebar.")
    elif ai_provider.startswith("Ollama"):
        st.caption("Ollama runs on your computer: first answers can take a minute while the model loads. Setup: install from ollama.com, "
                   f"then in Terminal run `ollama pull {ai_model}`.")
    if ai_provider.startswith("Google") and ai_ready:
        if st.button(":material/power: Test Gemini connection", help="Sends a tiny test message to each free Gemini model, with and without web search, "
                                                        "and shows which ones your key can use. Uses a few requests of your free quota."):
            with st.spinner("Testing your key against each free model…"):
                st.session_state["gem_diag"] = gemini_diagnose(ai_key)
        if st.session_state.get("gem_diag") is not None:
            st.dataframe(st.session_state["gem_diag"], use_container_width=True, hide_index=True)
            ok_ = st.session_state["gem_diag"]
            ok_ = ok_[ok_["Result"].str.startswith("✅")]
            if len(ok_):
                st.caption(f"Working: {', '.join(sorted(set(ok_['Model'])))}. Put one of these in the sidebar's Model box.")
    msgs = st.session_state.setdefault("ai_msgs", [])
    cols_s = st.columns(len(AI_SUGGESTIONS))
    for k_, s_ in enumerate(AI_SUGGESTIONS):
        if cols_s[k_].button(s_, key=f"ai_sug_{k_}", disabled=not ai_ready, use_container_width=True):
            st.session_state["ai_pending"] = s_
    if st.button(":material/delete: New conversation", help=HELP["ai_clear"]):
        st.session_state["ai_msgs"] = []
        msgs = st.session_state["ai_msgs"]
    for m_ in msgs:
        with st.chat_message(m_["role"]):
            st.markdown(m_["content"])
    sugg_now = st.session_state.get("ai_suggestions", [])
    follow_now = st.session_state.get("ai_followups", [])
    mirror_now = st.session_state.get("ai_mirror", [])
    if sugg_now or follow_now or mirror_now:
        st.markdown("**Suggested by the AI:**")
        for k_, s_ in enumerate(list(sugg_now)):
            if st.button(f":material/check_circle: Apply: {s_['desc']}", key=f"ai_apply_{k_}_{s_['desc']}"):
                old_s = _stab_now(s_["payload"][0], s_["payload"][1]) if s_["kind"] == "stab" else None
                apply_resolved(s_["kind"], s_["payload"])
                msgs.append({"role": "assistant", "content": f"✅ Applied: {s_['desc']}"})
                st.session_state["ai_suggestions"] = [x_ for x_ in sugg_now if x_ is not s_]
                if s_["kind"] == "stab":
                    gw_s, tid_s, val_s, opp_s = s_["payload"]
                    pending_opp = any(x_["kind"] == "stab" and x_["payload"][:2] == (gw_s, opp_s) for x_ in st.session_state["ai_suggestions"])
                    if not pending_opp:
                        queue_opponent_prompt(gw_s, tid_s, opp_s, old_s, val_s)
                _rerun()
        for k_, mr_ in enumerate(list(mirror_now)):
            cur_o = _stab_now(mr_["gw"], mr_["opp"])
            new_o = float(np.clip(round((cur_o - mr_["delta"]) / 0.05) * 0.05, -1, 1))
            cm1, cm2 = st.columns([3, 1])
            if cm1.button(f":material/swap_vert: Mirror it: {code.get(mr_['opp'])} GW{mr_['gw']} {cur_o:+.2f} → {new_o:+.2f} (the same amount the other way)",
                          key=f"ai_mirror_{k_}"):
                apply_resolved("stab", (mr_["gw"], mr_["opp"], new_o, mr_["team"]))
                msgs.append({"role": "assistant", "content": f"✅ Applied: {code.get(mr_['opp'])} stability GW{mr_['gw']} {cur_o:+.2f} → {new_o:+.2f} (mirrored)"})
                st.session_state["ai_mirror"] = [x_ for x_ in mirror_now if x_ is not mr_]
                st.session_state["ai_followups"] = []
                _rerun()
            if cm2.button(f"Leave {code.get(mr_['opp'])} as is", key=f"ai_leave_{k_}"):
                st.session_state["ai_mirror"] = [x_ for x_ in mirror_now if x_ is not mr_]
                st.session_state["ai_followups"] = []
                _rerun()
        for k_, f_ in enumerate(follow_now):
            if st.button(f":material/chat: Ask the AI: {f_}", key=f"ai_follow_{k_}"):
                st.session_state["ai_pending"] = f_
                st.session_state["ai_followups"], st.session_state["ai_mirror"] = [], []
        if st.button("Dismiss suggestions", key="ai_dismiss"):
            st.session_state["ai_suggestions"], st.session_state["ai_followups"], st.session_state["ai_mirror"] = [], [], []
            _rerun()
    q_ = st.chat_input("Ask a question…", disabled=not ai_ready) or st.session_state.pop("ai_pending", None)
    if q_ and ai_ready:
        msgs.append({"role": "user", "content": q_})
        with st.chat_message("user"):
            st.markdown(q_)
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    ans_, src_ = ask_any([{"role": m_["role"], "content": m_["content"]} for m_ in msgs[-12:]])
                    if src_:
                        ans_ += "\n\n**Sources:** " + " · ".join(f"[{t_}]({u_})" for u_, t_ in list(src_.items())[:8])
                except Exception as e_:
                    ans_ = f"⚠️ {e_}"
            ans_, acts_ = parse_actions(ans_ or "")
            applied_, sugg_, follow_, errs_ = handle_actions(acts_, ai_autoset) if acts_ else ([], [], [], [])
            if applied_:
                ans_ += "\n\n✅ **Changed in the app:** " + "; ".join(applied_)
            if sugg_:
                ans_ += "\n\n💡 **Suggested (use the buttons below to apply):** " + "; ".join(s_["desc"] for s_ in sugg_)
            if errs_:
                ans_ += "\n\n⚠️ Couldn't apply: " + "; ".join(errs_)
            st.markdown(ans_ or "_(no answer returned)_")
        msgs.append({"role": "assistant", "content": ans_ or "(no answer)"})
        st.session_state["ai_suggestions"] = sugg_
        st.session_state["ai_followups"] = [] if any(s_["kind"] == "stab" for s_ in sugg_) else follow_
        if applied_ or sugg_ or follow_:
            _rerun()
