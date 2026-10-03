"""Offline, attributed setup references for F1 25's 2026 Season Pack.

Numeric source snapshots reviewed 2026-10-02. See docs/setup-reference-research.md
for provenance, field ordering and limitations. This module neither downloads
content nor estimates performance. Its dictionaries are fresh for every request.
"""
from __future__ import annotations

from typing import Any

REVIEWED_AT = "2026-10-02"
GAME = "F1 25: 2026 Season Pack"
STYLES = ("stable", "rotation")
CALENDAR_TRACK_IDS = (0, 2, 13, 3, 29, 30, 6, 5, 4, 17, 7, 10, 9, 26, 11,
                      42, 20, 12, 15, 19, 16, 31, 32, 14)

# Source order: wings F/R, differentials on/off, geometry F/R camber and
# F/R toe, springs F/R, anti-roll bars F/R, heights F/R, BIAS/PRESSURE,
# tyre pressures FR/FL/RR/RL. Names deliberately handle the tyre-order swap.
SETUP_FIELDS = (
    "front_wing", "rear_wing", "on_throttle", "off_throttle",
    "front_camber", "rear_camber", "front_toe", "rear_toe",
    "front_suspension", "rear_suspension",
    "front_anti_roll_bar", "rear_anti_roll_bar",
    "front_suspension_height", "rear_suspension_height",
    "brake_bias", "brake_pressure",
    "front_right_tyre_pressure", "front_left_tyre_pressure",
    "rear_right_tyre_pressure", "rear_left_tyre_pressure",
)

_TRACK_NAMES = {
    0: "Melbourne",
    2: "Shanghai",
    3: "Sakhir",
    4: "Catalunya",
    5: "Monaco",
    6: "Montreal",
    7: "Silverstone",
    9: "Hungaroring",
    10: "Spa",
    11: "Monza",
    12: "Singapore",
    13: "Suzuka",
    14: "Abu Dhabi",
    15: "Texas",
    16: "Brazil",
    17: "Austria",
    19: "Mexico",
    20: "Baku",
    26: "Zandvoort",
    27: "Imola",
    29: "Jeddah",
    30: "Miami",
    31: "Las Vegas",
    32: "Losail",
    39: "Silverstone (Reverse)",
    40: "Austria (Reverse)",
    41: "Zandvoort (Reverse)",
    42: "Madrid",
}
_MATT_SHEET = (
    "https://docs.google.com/spreadsheets/d/"
    "1mmFai7jDGYpZ2cc_PBk3PBrpUFgbjEzB7z-q5P2VlFE/edit#gid=673562173"
)
_DERP_SHEET = (
    "https://docs.google.com/spreadsheets/d/"
    "1fUZKqMpARGJ1XEvsmGlOtN2_NVPOqLehPNiLH-YyYSI/edit#gid=2082870794"
)
_DERP_POST = (
    "https://www.reddit.com/r/F1Game/comments/1vfjlse/f1_26_setup_spreadsheet/"
)
_EA_GAME = "https://www.ea.com/games/f1/f1-25/news/f1-25-features-deep-dive"

# Matt212, F1 26 Safe Setups tab. All twenty adjustable source fields; no
# engine-braking, ballast or session fuel numbers are fabricated.
_STABLE_ROWS = {
    0: (21, 17, 100, 25, -3.4, -1.9, 0.02, 0.13, 34, 9, 9, 14, 21, 47, 55, 100, 24.5, 24.5, 22, 22),  # Australia
    2: (35, 28, 100, 25, -3.4, -1.9, 0.03, 0.13, 34, 11, 9, 15, 22, 49, 56, 99, 25.4, 26.9, 21.9, 22.6),  # China
    13: (24, 16, 100, 25, -3.4, -1.9, 0.01, 0.12, 38, 5, 7, 14, 21, 48, 56, 99, 27.9, 27.9, 22, 22),  # Japan
    3: (24, 18, 100, 25, -3.4, -1.9, 0.03, 0.13, 32, 10, 5, 15, 22, 49, 56, 99, 25, 25, 21.9, 21.9),  # Bahrain
    29: (16, 10, 100, 30, -3.4, -1.9, 0.03, 0.13, 36, 13, 5, 13, 22, 49, 56, 99, 24.3, 24.3, 22.3, 22.3),  # Saudi Arabia
    30: (25, 20, 100, 20, -3.4, -1.9, 0.01, 0.12, 38, 4, 6, 14, 21, 48, 56, 99, 26, 26, 22.7, 22.7),  # Miami (USA)
    6: (28, 20, 100, 25, -3.4, -1.9, 0.01, 0.12, 36, 4, 4, 11, 22, 49, 56, 99, 24, 24, 23.3, 23.3),  # Canada
    5: (50, 50, 100, 20, -3.4, -1.9, 0.01, 0.12, 39, 4, 4, 12, 22, 50, 56, 99, 28.5, 28.5, 22, 22),  # Monaco
    4: (46, 36, 100, 30, -3.4, -1.9, 0.01, 0.12, 39, 15, 14, 12, 22, 49, 56, 99, 26.3, 28.2, 21.5, 22.8),  # Barcelona (Spain)
    17: (26, 18, 100, 30, -3.4, -1.9, 0.01, 0.12, 38, 17, 11, 15, 22, 49, 56, 99, 24, 24, 23, 23),  # Austria
    7: (17, 10, 100, 25, -3.4, -1.9, 0.01, 0.12, 38, 5, 4, 12, 22, 49, 56, 99, 29.5, 29.5, 23.4, 23.4),  # Britain
    10: (9, 0, 100, 20, -3.4, -1.9, 0.06, 0.12, 38, 4, 4, 19, 24, 50, 56, 99, 23.5, 23.5, 21.8, 21.8),  # Belgium
    9: (49, 46, 100, 20, -3.4, -1.9, 0.01, 0.11, 40, 30, 5, 18, 21, 48, 56, 99, 28, 28, 23.5, 23.5),  # Hungary
    26: (48, 42, 100, 25, -3.4, -1.9, 0.01, 0.12, 39, 26, 6, 19, 21, 49, 56, 99, 24.2, 25.1, 22.1, 23.3),  # Netherlands
    11: (9, 0, 100, 30, -3.4, -1.9, 0.01, 0.11, 38, 3, 5, 15, 23, 49, 56, 99, 29.5, 29.5, 26.5, 26.5),  # Monza (Italy)
    42: (39, 31, 100, 25, -3.4, -1.9, 0.01, 0.12, 37, 4, 5, 12, 23, 51, 56, 99, 28.3, 28.3, 21.9, 21.9),  # Madrid (Spain)
    20: (12, 2, 100, 20, -3.4, -1.9, 0.01, 0.12, 36, 16, 5, 12, 22, 48, 56, 99, 24.3, 24.3, 21.5, 21.5),  # Azerbaijan
    12: (50, 49, 100, 25, -3.4, -1.9, 0.01, 0.11, 38, 4, 5, 12, 22, 48, 56, 99, 23.7, 23.7, 21.6, 21.6),  # Singapore
    15: (46, 36, 100, 40, -3.4, -1.9, 0.01, 0.11, 34, 11, 15, 5, 21, 48, 56, 99, 28, 28, 22.5, 22.5),  # Texas (USA)
    19: (44, 34, 100, 30, -3.4, -1.9, 0.01, 0.12, 35, 14, 7, 10, 23, 49, 56, 99, 24.2, 24.2, 22.1, 22.1),  # Mexico
    16: (35, 25, 100, 30, -3.4, -1.9, 0.01, 0.12, 37, 16, 5, 11, 22, 48, 56, 99, 28.3, 28.3, 21.7, 21.7),  # Brazil
    31: (9, 0, 100, 20, -3.4, -1.9, 0.01, 0.11, 37, 8, 7, 8, 23, 48, 55, 99, 24.7, 24.7, 22.5, 22.5),  # Las Vegas (USA)
    32: (45, 35, 100, 40, -3.4, -1.9, 0.01, 0.12, 39, 29, 3, 10, 22, 48, 56, 99, 28.5, 29.5, 21.3, 22.6),  # Qatar
    14: (45, 35, 100, 20, -3.4, -1.9, 0.01, 0.11, 37, 13, 16, 4, 21, 48, 56, 99, 27.5, 27.5, 22, 22),  # Abu Dhabi
    27: (39, 29, 100, 20, -3.4, -1.9, 0.01, 0.12, 38, 20, 3, 11, 22, 49, 56, 99, 27.5, 27.5, 24.5, 24.5),  # Imola (Italy)
    40: (30, 20, 100, 30, -3.4, -1.9, 0.01, 0.11, 36, 14, 16, 7, 23, 50, 56, 99, 24.7, 24.7, 22, 22),  # Austria Reverse
    39: (27, 18, 100, 30, -3.4, -1.9, 0.01, 0.11, 37, 4, 6, 17, 21, 50, 56, 99, 28.1, 28.1, 22.5, 22.5),  # Britain Reverse
    41: (50, 41, 100, 25, -3.4, -1.9, 0.01, 0.11, 37, 10, 9, 15, 22, 48, 56, 99, 25.7, 24.4, 23.4, 21.9),  # Netherlands Reverse
}
_STABLE_GUIDES = {
    0: "https://youtu.be/K_Iqa6UA2QQ",
    2: "https://youtu.be/mus9OXzqh4g",
    13: "https://youtu.be/oeUT8T63Bz4",
    3: "https://youtu.be/aDofDlvIQ0c",
    29: "https://youtu.be/plxOFkmOWTA",
    30: "https://youtu.be/n8T1LhUGrxg",
    6: "https://youtu.be/Fu6RHdA6oIk",
    5: "https://youtu.be/6taQoUIdsJU",
    4: "https://youtu.be/_pbfSwrP9Ig",
    17: "https://youtu.be/d3ihrRKE9oM",
    7: "https://youtu.be/YJX0TO4l1Uk",
    10: "https://youtu.be/c0-A7rv_5mA",
    9: "https://youtu.be/wo3p_cMWdVo",
    26: "https://youtu.be/LNiWmlP2Cxs",
    11: "https://youtu.be/Wj7PduClsWY",
    42: "https://youtu.be/-mRx0AymsZ8",
    20: "https://youtu.be/L9HynFjs7Ts",
    12: "https://youtu.be/7Z2WMZQc-4U",
    15: "https://youtu.be/SvAccEuekfQ",
    19: "https://youtu.be/RBxNYhnYPAA",
    16: "https://youtu.be/HkAVAbbJUJg",
    31: "https://youtu.be/SWvDM_UFMx4",
    32: "https://youtu.be/bPEwDYVytNw",
    14: "https://youtu.be/3BE_sv2H6DM",
    27: "https://youtu.be/wzD6QBsPOtw",
    40: "https://youtu.be/YtKMQ5OIej0",
    39: "https://youtu.be/AJXJmwXVFDk",
    41: "https://youtu.be/g4Eaf7BLdWE",
}

# Derp3339, F1 26 tab: race values, qualifying values, source creation date,
# published brake-bias option(s), author-theory flag. LLLL geometry is expanded
# to -3.50/-2.00/0.00/0.10; source context and normalization remain visible.
# Where bias is a range, use its lower endpoint and mark the result adapted.
_ROTATION_ROWS = {
    0: ((42, 15, 100, 60, -3.5, -2, 0, 0.1, 41, 38, 1, 5, 21, 47, 56, 98, 29.5, 29.5, 20.5, 20.5),
         (30, 0, 100, 45, -3.5, -2, 0, 0.1, 41, 38, 1, 5, 21, 47, 56, 98, 29.5, 29.5, 20.5, 20.5),
         "2026-06-24", (56,), False),  # Australia
    2: ((50, 22, 100, 65, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 22, 46, 57, 98, 29.5, 29.5, 20.5, 20.5),
         (50, 22, 100, 65, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 22, 46, 57, 98, 29.5, 29.5, 20.5, 20.5),
         None, (57,), True),  # China
    13: ((45, 16, 100, 50, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 23, 48, 56, 96, 25, 25, 20.5, 20.5),
         (45, 16, 100, 45, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 23, 48, 56, 96, 25.5, 25.5, 20.5, 20.5),
         "2026-06-26", (56,), False),  # Japan
    3: ((50, 26, 100, 50, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 22, 46, 57, 97, 26.5, 26.5, 20.5, 20.5),
         (50, 26, 100, 50, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 22, 46, 57, 97, 23, 23, 20.5, 20.5),
         "2026-08-01", (57,), False),  # Bahrain
    29: ((41, 0, 100, 50, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 20, 42, 58, 100, 29.5, 29.5, 22, 22),
         (41, 0, 100, 50, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 20, 42, 58, 100, 29.5, 29.5, 20.5, 20.5),
         "2026-07-13", (58,), False),  # Saudi Arabia
    30: ((50, 17, 100, 85, -3.5, -2, 0, 0.1, 41, 41, 1, 8, 22, 43, 57, 99, 29.5, 29.5, 20.5, 20.5),
         (50, 17, 100, 85, -3.5, -2, 0, 0.1, 41, 41, 1, 8, 22, 43, 57, 99, 29.5, 29.5, 20.5, 20.5),
         "2026-07-27", (57,), False),  # Miami
    6: ((50, 24, 100, 55, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 21, 44, 57, 98, 29.5, 29.5, 20.5, 20.5),
         (50, 24, 100, 55, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 21, 44, 57, 98, 26, 26, 20.5, 20.5),
         "2026-07-05", (57,), False),  # Canada
    5: ((50, 50, 100, 30, -3.5, -2, 0, 0.1, 38, 41, 1, 4, 21, 46, 56, 100, 22.5, 22.5, 20.5, 20.5),
         (50, 50, 100, 30, -3.5, -2, 0, 0.1, 38, 41, 1, 4, 21, 46, 56, 100, 22.5, 22.5, 20.5, 20.5),
         None, (56,), False),  # Monaco
    4: ((50, 23, 100, 45, -3.5, -2, 0, 0.1, 41, 38, 1, 5, 21, 43, 56, 98, 29.5, 29.5, 20.5, 20.5),
         (50, 23, 100, 45, -3.5, -2, 0, 0.1, 41, 38, 1, 5, 21, 43, 56, 98, 29.5, 29.5, 20.5, 20.5),
         "2026-06-27", (56,), False),  # Catalunya
    17: ((50, 24, 100, 60, -3.5, -2, 0, 0.1, 41, 41, 1, 6, 20, 47, 56, 97, 29.5, 29.5, 20.5, 20.5),
         (50, 12, 100, 60, -3.5, -2, 0, 0.1, 41, 41, 1, 6, 20, 47, 56, 98, 29.5, 29.5, 20.5, 20.5),
         "2026-06-25", (56, 57), False),  # Austria
    7: ((36, 5, 100, 55, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 21, 42, 59, 97, 29.5, 29.5, 20.5, 20.5),
         (36, 5, 100, 55, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 21, 42, 59, 97, 29.5, 29.5, 20.5, 20.5),
         "2026-06-29", (59, 60), False),  # Britain
    10: ((22, 0, 100, 45, -3.5, -2, 0, 0.1, 41, 41, 1, 5, 20, 46, 57, 100, 29.5, 29.5, 26.5, 26.5),
         (22, 0, 100, 45, -3.5, -2, 0, 0.1, 41, 41, 1, 5, 20, 46, 57, 100, 29.5, 29.5, 26.5, 26.5),
         "2026-06-23", (57,), False),  # Belgium
    9: ((50, 41, 100, 40, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 22, 42, 56, 98, 29.5, 29.5, 20.5, 20.5),
         (50, 41, 100, 40, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 22, 42, 56, 98, 29, 29, 20.5, 20.5),
         "2026-07-27", (56,), False),  # Hungary
    26: ((50, 31, 100, 35, -3.5, -2, 0, 0.1, 41, 41, 1, 5, 20, 45, 55, 100, 29.5, 29.5, 20.5, 20.5),
         (50, 31, 100, 35, -3.5, -2, 0, 0.1, 41, 41, 1, 5, 20, 45, 55, 100, 29.5, 29.5, 20.5, 20.5),
         "2026-07-11", (55, 57), False),  # Netherlands
    11: ((16, 0, 100, 55, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 23, 40, 57, 99, 29.5, 29.5, 26.5, 26.5),
         (16, 0, 100, 55, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 23, 40, 57, 99, 29.5, 29.5, 26.5, 26.5),
         "2026-09-02", (57,), False),  # Italy
    42: ((50, 28, 100, 55, -3.5, -2, 0, 0.1, 38, 41, 1, 6, 22, 43, 56, 99, 29.5, 29.5, 20.5, 20.5),
         (50, 28, 100, 55, -3.5, -2, 0, 0.1, 38, 41, 1, 6, 22, 43, 56, 99, 29.5, 29.5, 20.5, 20.5),
         "2026-08-08", (56,), False),  # Madrid
    20: ((18, 0, 100, 80, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 22, 43, 56, 99, 29.5, 29.5, 20.5, 20.5),
         (18, 0, 100, 80, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 22, 43, 56, 99, 29.5, 29.5, 20.5, 20.5),
         "2026-08-12", (56, 57), False),  # Azerbaijan
    12: ((50, 39, 100, 45, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 21, 44, 56, 98, 29.5, 29.5, 20.5, 20.5),
         (50, 39, 100, 45, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 21, 44, 56, 98, 26, 26, 20.5, 20.5),
         "2026-07-04", (56,), False),  # Singapore
    15: ((50, 34, 100, 50, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 20, 45, 56, 97, 29.5, 29.5, 20.5, 20.5),
         (50, 34, 100, 50, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 20, 45, 56, 100, 29.5, 29.5, 20.5, 20.5),
         "2026-06-28", (56, 57), False),  # United States
    19: ((50, 23, 100, 80, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 21, 41, 57, 99, 29.5, 29.5, 20.5, 20.5),
         (50, 23, 100, 75, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 21, 41, 57, 99, 29.5, 29.5, 20.5, 20.5),
         "2026-08-17", (57, 59), False),  # Mexico
    16: ((50, 28, 100, 35, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 20, 44, 55, 98, 29.5, 29.5, 20.5, 20.5),
         (50, 28, 100, 35, -3.5, -2, 0, 0.1, 41, 41, 1, 4, 20, 44, 55, 100, 29.5, 29.5, 20.5, 20.5),
         "2026-07-11", (55, 56), False),  # Brazil
    31: ((15, 0, 100, 55, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 24, 45, 56, 97, 25.5, 25.5, 20.5, 20.5),
         (15, 0, 100, 55, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 24, 45, 56, 100, 25.5, 25.5, 20.5, 20.5),
         "2026-07-26", (56,), False),  # Las Vegas
    32: ((50, 18, 100, 40, -3.5, -2, 0, 0.1, 41, 41, 1, 7, 20, 44, 56, 98, 29.5, 29.5, 20.5, 20.5),
         (50, 18, 100, 40, -3.5, -2, 0, 0.1, 41, 41, 1, 7, 20, 44, 56, 100, 29.5, 29.5, 20.5, 20.5),
         "2026-07-09", (56, 57), False),  # Qatar
    14: ((50, 21, 100, 80, -3.5, -2, 0, 0.1, 41, 41, 1, 9, 21, 41, 57, 100, 29.5, 29.5, 20.5, 20.5),
         (50, 21, 100, 80, -3.5, -2, 0, 0.1, 41, 41, 1, 9, 21, 41, 57, 100, 29.5, 29.5, 20.5, 20.5),
         "2026-08-23", (57,), False),  # Abu Dhabi
    27: ((50, 24, 100, 40, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 22, 46, 57, 97, 28.5, 28.5, 20.5, 20.5),
         (50, 24, 100, 40, -3.5, -2, 0, 0.1, 41, 41, 1, 1, 22, 46, 57, 100, 28.5, 28.5, 20.5, 20.5),
         "2026-07-02", (57,), False),  # Imola
}


def _source(title: str, url: str, author: str,
            published: str | None, kind: str) -> dict[str, Any]:
    return {"title": title, "url": url, "author": author,
            "published": published, "kind": kind}


def reference_for_track(
    track_id: int,
    condition: str = "dry",
    style: str = "stable",
    profile: str = "race",
) -> dict[str, Any]:
    """Return an attributed reference, or an explicit empty unavailable result.

    Published means the numeric reference is published, not independently
    driven or proven faster. Missing tracks/styles/weather never borrow an
    unrelated circuit. Hybrid uses race values. Qualifying values only differ
    where that creator supplies them.
    """
    condition = str(condition).strip().lower()
    style = str(style).strip().lower()
    profile = str(profile).strip().lower()
    valid_track = isinstance(track_id, int) and not isinstance(track_id, bool)
    track = _TRACK_NAMES.get(track_id, f"Track {track_id}") if valid_track else "Unknown track"
    result: dict[str, Any] = {
        "setup": {}, "id": f"f1-2026-{style}-{track_id}-{condition}-{profile}",
        "title": f"{track} setup reference", "track_id": track_id,
        "game": GAME, "condition": condition, "style": style,
        "profile": profile, "source_profile": "race",
        "status": "unavailable", "verification": "unavailable",
        "reason": "",
        "reviewed_at": REVIEWED_AT, "sources": [], "notes": [],
        "source_context": {
            "mode": "unknown", "input": "unknown",
            "car": "2026 F1 car; team not specified",
            "equal_performance": "unknown",
        },
        "source_ranges": {},
        "source_notation": {},
    }
    if style not in STYLES:
        result["notes"] = ["This setup style has no reference in the reviewed library."]
        result["reason"] = result["notes"][0]
        return result
    if profile not in ("race", "quali", "hybrid"):
        result["notes"] = ["This session profile has no reference in the reviewed library."]
        result["reason"] = result["notes"][0]
        return result
    if condition != "dry":
        result["notes"] = [
            "No applicable published wet setup was verified in this reference library."
            if condition in ("wet", "intermediate", "inter", "rain", "light_rain", "heavy_rain")
            else "No published reference was reviewed for these conditions.",
            "Dry values are not substituted for a wet or unknown-condition setup.",
        ]
        result["reason"] = result["notes"][0]
        return result
    rows = _STABLE_ROWS if style == "stable" else _ROTATION_ROWS
    if not valid_track or track_id not in rows:
        result["notes"] = ["This circuit and setup style have no published reference in the reviewed library."]
        result["reason"] = result["notes"][0]
        return result

    result["status"] = "published"
    result["verification"] = "source_reviewed_not_driven"
    result["notes"] = [
        "Published starting point; pace and balance still need checking with your car and driving style.",
        "Source review does not establish a fastest setup or controlled race validation.",
        "Team upgrades and equal-performance settings are not documented per source row.",
    ]
    if style == "stable":
        values = _STABLE_ROWS[track_id]
        result["title"] = f"{track} — Matt212 stable race reference"
        result["source_context"]["mode"] = "Race baseline with 50% race guidance and a hotlap guide"
        result["sources"] = [
            _source("F1 26 Safe Setups — circuit row", _MATT_SHEET,
                    "Matt212", None, "creator_spreadsheet"),
            _source(f"F1 26 {track} setup and track guide", _STABLE_GUIDES[track_id],
                    "Matt212", None, "creator_video"),
        ]
        result["notes"].append("The creator labels this tab for easier control; its row publication dates and input device are unspecified.")
        if profile == "quali":
            result["notes"].append("The creator supplies no separate qualifying values in this tab; this is the unchanged race reference.")
        if track_id in (31, 40):
            result["notes"].append("The source tyre cell omits one separator; its four numeric pressures are retained in FR/FL/RR/RL order.")
    else:
        race, quali, published, bias_options, theory = _ROTATION_ROWS[track_id]
        values = quali if profile == "quali" else race
        result["source_profile"] = "quali" if profile == "quali" else "race"
        result["title"] = f"{track} — Derp3339 rotation reference"
        result["source_context"]["mode"] = "League qualifying" if profile == "quali" else "League race"
        result["source_context"]["input"] = "Primarily wheel; occasional controller testing, according to the creator"
        result["sources"] = [
            _source("F1 26 setups — circuit row", _DERP_SHEET,
                    "Derp3339", published, "creator_spreadsheet"),
            _source("Creator's F1 26 setup context and notation", _DERP_POST,
                    "Derp3339", "2026-08-04", "creator_context"),
            _source("2026 China guide — geometry endpoint corroboration",
                    "https://www.rickf1racing.com/p/setud-circuito-de-china-2026.html",
                    "RickF1Racing", None, "geometry_corroboration"),
        ]
        result["source_notation"]["suspension_geometry"] = "LLLL"
        result["sources"][0]["date_kind"] = "creator_row_creation_date"
        result["notes"].extend([
            "This is the creator's league baseline with more rotation; it can require more rear-balance control.",
            "The creator defines LLLL as all four geometry sliders fully left. Its numeric interpretation is camber -3.50/-2.00 and toe 0.00/0.10, corroborated by a current 2026 circuit guide.",
            "Race and qualifying columns are kept separate; hybrid uses race values.",
        ])
        if profile == "quali":
            result["notes"].append("The creator recommends race wings and race brake pressure for qualifying when parc ferme applies.")
        if len(bias_options) > 1:
            result["status"] = "adapted"
            result["source_ranges"]["brake_bias"] = list(bias_options)
            result["notes"].append(
                f"The source gives brake bias {bias_options[0]}–{bias_options[-1]}%; "
                f"this reference selects {bias_options[0]}%, the lower endpoint."
            )
        if theory:
            result["status"] = "adapted"
            result["verification"] = "author_theory"
            result["notes"].append("The creator marks this circuit as theory; it is not a verified tested setup.")
        elif published is None:
            result["notes"].append("The source row supplies no usable publication date.")
    if profile == "hybrid":
        result["notes"].append("Hybrid uses the creator's race reference unchanged.")
    result["setup"] = dict(zip(SETUP_FIELDS, values, strict=True))
    result["sources"].append(
        _source("F1 25: 2026 Season Pack game context", _EA_GAME,
                "EA SPORTS", "2026-05-26", "official_game_context")
    )
    return result
