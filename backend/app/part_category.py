"""What KIND of part this is, derived from item_desc.

The nine similarity features describe a part's CONTEXT -- machine, criticality,
lead time, price, supplier -- but none of them says what the thing IS. Measured
on the Jan'26 + archived pool that left 510 parts in a single similarity bucket,
all at distance 0.000 from one another, so a cable could be retrieved as a
mounting block's stocking precedent.

Category is a CONSTRAINT, not a distance dimension. similarity.py uses it to
decide who is ELIGIBLE to be a peer; it never enters FEATURE_WEIGHTS, so the
weights are untouched and still sum to 1.0. A weighted feature would only push a
mismatched peer down the ranking -- a hard filter is what actually stops a screw
being a cable's evidence.

Rules live in part_category_config and are engineer-owned. Only confirmed=1 rows
are ever read, exactly as machine_criticality_config works: an agent or an
engineer may PROPOSE a rule, but a second person must confirm it before it can
move a number.
"""

from __future__ import annotations

import re

MODEL_VERSION = "cat-v1"
UNCATEGORISED = ""

# Longest a stored pattern may be. A user-supplied regex is arbitrary code for
# the matching engine, and nested quantifiers like (a+)+$ backtrack
# catastrophically; the cap bounds the damage. Enforced again at the Pydantic
# layer so a bad rule is rejected at the API, not discovered mid-batch.
MAX_PATTERN_LEN = 200

# Ordered: FIRST MATCH WINS, so specific before generic. `holder` and `assembly`
# are last on purpose -- BRACKET and ASSY appear inside descriptions of far more
# specific parts, and "SENSOR BRACKET ASSY" is a sensor. Ordering IS the
# contract here, the same way it is for echo.py's intent routing.
#
# Measured 76% coverage over 3,005 reviewed parts (Jan'26 + five archived
# months). The abbreviations matter: SNR and SOL alone are worth ~7pp, and
# appear in descriptions like "CARRIAGE AB FLOW SNR(L)" and "HT WC COL SOL(L)".
DEFAULT_RULES: list[tuple[int, str, str]] = [   # (priority, category, pattern)
    (10, "sensor", r"\b(SENSOR|SNR|DETECTOR|ENCODER|THERMOCOUPLE|PROBE|FLOWMETER|SCALE)\b"),
    (20, "valve", r"\b(VALVE|SOLENOID|SOL|REGULATOR|MANIFOLD)\b"),
    (30, "cable", r"\b(CABLE|HARNESS|WIRE|CORD|FLEX)\b"),
    (40, "hose_tube", r"\b(HOSE|TUBING|TUBE|PIPE|FITTING|COUPLER)\b"),
    (50, "motor", r"\b(MOTOR|SERVO|ACTUATOR|STEPPER)\b"),
    (60, "pump", r"\b(PUMP|BLOWER|COMPRESSOR)\b"),
    (70, "belt", r"\b(BELT)\b"),
    (80, "pulley", r"\b(PULLEY|SPROCKET|GEAR|COUPLING)\b"),
    (90, "bearing", r"\b(BEARING|BUSHING|GUIDE|RAIL|SLIDE)\b"),
    (100, "seal", r"\b(SEAL|O.?RING|GASKET|PACKING|DIAPHRAGM)\b"),
    (110, "filter", r"\b(FILTER|STRAINER|CARTRIDGE)\b"),
    (120, "nozzle", r"\b(NOZZLE|TIP|JET|PICKER|COLLET)\b"),
    (130, "fastener", r"\b(SCREW|BOLT|NUT|WASHER|STUD|CLAMP|CLIP|PIN|SPRING)\b"),
    (140, "pcb", r"\b(PCB|BOARD|CARD|MODULE|DRIVER|CONTROLLER|AMPLIFIER|CPU|ASIO)\b"),
    (150, "connector", r"\b(CONNECTOR|SOCKET|PLUG|TERMINAL|ADAPTER|ADAPTOR)\b"),
    (160, "switch", r"\b(SWITCH|RELAY|BREAKER|FUSE)\b"),
    (170, "lamp", r"\b(LAMP|LED|LIGHT|BULB)\b"),
    (180, "thermal", r"\b(FAN|HEATER|CHILLER|COOLER|RADIATOR|PELTIER)\b"),
    (190, "optic", r"\b(LENS|MIRROR|CAMERA|OPTIC|GLASS|WINDOW|PRISM|LASER|ZERODUR)\b"),
    (200, "power", r"\b(POWER ?SUPPLY|BATTERY|TRANSFORMER|INVERTER|UPS|PSU)\b"),
    (210, "tool", r"\b(BLADE|CUTTER|PUNCH|DIE|ANVIL|CHUCK|GRIPPER|TWEEZER)\b"),
    (220, "holder", r"\b(HOLDER|BRACKET|MOUNT|PLATE|BLOCK|SHAFT|ARM|BASE|COVER|"
                    r"PLATEN|CARRIAGE|PEDESTAL|STAGE|TABLE|CHUTE|TRACK)\b"),
    (230, "assembly", r"\b(ASSY|ASSEMBLY|KIT|SET)\b"),
]


def load_rules(conn) -> tuple[list[tuple[int, str, re.Pattern]], list[str]]:
    """Confirmed rules in priority order, plus the patterns that would not compile.

    `WHERE confirmed=1` is the safety property, not a nicety: an unconfirmed
    proposal must not change which peers an engineer is shown.

    A stored pattern is engineer-entered text. One that fails to compile is
    skipped and reported rather than raised -- a single bad rule must not take
    down scoring for the whole batch.
    """
    rules: list[tuple[int, str, re.Pattern]] = []
    broken: list[str] = []
    for r in conn.execute(
            "SELECT pattern, category, priority FROM part_category_config "
            "WHERE confirmed=1 ORDER BY priority, pattern"):
        pattern = str(r["pattern"] or "")
        if not pattern or len(pattern) > MAX_PATTERN_LEN:
            broken.append(pattern[:80])
            continue
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error:
            broken.append(pattern[:80])
            continue
        rules.append((int(r["priority"]), str(r["category"]), compiled))
    return rules, broken


def categorise(item_desc, rules) -> str:
    """First matching rule wins; '' when nothing matches.

    '' is meaningful downstream: an uncategorised part is never blocked from a
    peer and its category is never narrated as a match, mirroring the one-sided
    missing rule in similarity.feature_distances.
    """
    text = str(item_desc or "").strip()
    if not text:
        return UNCATEGORISED
    for _priority, category, pattern in rules:
        if pattern.search(text):
            return category
    return UNCATEGORISED


def categories(rules) -> list[str]:
    """Distinct categories the active lexicon can produce, in priority order."""
    seen: dict[str, None] = {}
    for _priority, category, _pattern in rules:
        seen.setdefault(category, None)
    return list(seen)
