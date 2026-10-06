import re
from urllib.parse import urlparse
from typing import Optional
from .ban_config import state_patterns, keywords
from nltk.corpus import stopwords
import os
import nltk
import us
from nltk.corpus import stopwords

# Force a fixed, non-root NLTK data directory
NLTK_DATA_DIR = "/home/ec2-user/home/ETL-project/nltk_data"

os.makedirs(NLTK_DATA_DIR, exist_ok=True)
nltk.data.path.append(NLTK_DATA_DIR)

try:
    STOPWORDS = set(stopwords.words("english"))
except LookupError:
    nltk.download("stopwords", download_dir=NLTK_DATA_DIR, quiet=True)
    STOPWORDS = set(stopwords.words("english"))
# File for modular functions used to unpack and organize email data into pandas dataframe
# unpack emails in a safe fashion
def safe_get_plain(message):
    try:
        body = message.body
        if not body:
            return None

        plain = body.get("plain")
        if not plain or not isinstance(plain, list):
            return None

        return plain[0]

    except Exception as e:
        print(f"[WARN] failed to extract plain body: {repr(e)}")
        return None

# extract states / locations
# Google Alerts appends the outlet: "Headline - Source" / "Headline | Source - YouTube"
SOURCE_SUFFIX = re.compile(r"\s+[-–—|]\s*([^|\-–—]*)$")
YOUTUBE_SUFFIX = re.compile(r"\s+[-–—]\s+YouTube\s*$")
MAX_SOURCE_WORDS = 6  # longer trailing segments are headline text, e.g. "Opinion | <headline>"

def split_source(text: str):
    """Split a headline into (body, source). Source is '' if none found."""
    text = YOUTUBE_SUFFIX.sub("", text)
    m = SOURCE_SUFFIX.search(text)
    if not m or len(m.group(1).split()) > MAX_SOURCE_WORDS:
        return text, ""
    return text[:m.start()], m.group(1)

def first_state_match(text: str) -> Optional[str]:
    for state, pattern in state_patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            return state
    return None

# extract states / locations: headline body wins; outlet name is only a fallback
def extract_state(text: str) -> str:
    body, source = split_source(text)
    return first_state_match(body) or first_state_match(source) or "National / Unknown"

# extract data source names
def extract_datasource(url: str) -> str:
    domain = urlparse(url).netloc
    return domain.replace("www.", "")
# # --- 3. Target extractor ---
# remove stop words
STOPWORDS = set(stopwords.words("english"))
def clean_headline(text: str) -> str:
    # Step 1: remove trailing " - Source Name" or " – Source Name"
    text = re.sub(r"\s*[-–]\s+[A-Za-z0-9&.,'’ ]+$", "", text)
    # Step 2: remove stopwords
    words = text.lower().split()
    words = [w for w in words if w not in STOPWORDS]
    return " ".join(words)
######################################################################################################################################333
# compile patterns once (preserves insertion order = priority)
COMPILED_PATTERNS = [(canon, [re.compile(p, re.IGNORECASE) for p in pats])
                     for canon, pats in keywords.items()]

def extract_target_generic(text: str) -> Optional[str]:
    """Free-text subject near "ban" for headlines no keyword matched.
    Stored as target_raw for spotting new categories; never charted."""
    body = split_source(text)[0]
    body = re.sub(r"[\s\-–—|:]+$", "", body)  # truncated headlines ending "... ban -"
    t = " ".join(w for w in body.lower().split() if w not in STOPWORDS)
    # 1) "ban on X" / "bans X"
    m = re.search(r"\bban(?:s|ning|ned)?\s+(?:on\s+)?([a-z0-9][a-z0-9\- ]*)", t)
    words = [w for w in (m.group(1).split() if m else []) if w not in STOPWORDS]
    # 2) "X ban" (subject before the word ban)
    if not words:
        m = re.search(r"((?:[a-z0-9'\-]+\s+){1,3})ban(?:s|ned|ning)?\b", t)
        words = m.group(1).split() if m else []
    return " ".join(words[:3]) if words else None

def find_keyword_targets(headline: str, max_keywords: int = 3):
    """Return up to `max_keywords` canonical targets found in headline, by priority."""
    found = []
    for canon, patterns in COMPILED_PATTERNS:
        if any(p.search(headline) for p in patterns):
            found.append(canon)
            if len(found) >= max_keywords:
                break
    return found

def choose_target(headline: str) -> Optional[str]:
    # Dictionary categories only, matched on the headline body so outlet names
    # ("- Politico", "- Guns.com") don't create topics. Unmatched -> None;
    # the free-text guess goes to target_raw via extract_target_generic.
    kws = find_keyword_targets(split_source(headline)[0], max_keywords=3)
    return ", ".join(kws) if kws else None

# headlines that only matched the alert because "ban" is part of a product/name
NOISE_PATTERN = re.compile(r"\bray[-\s]?ban\b", re.IGNORECASE)

def is_noise_headline(headline: str) -> bool:
    return bool(NOISE_PATTERN.search(headline or ""))

# us 3.2.0 has no entry for DC, so it needs an explicit abbreviation
STATE_ABBREV_OVERRIDES = {"District of Columbia": "DC"}

def state_to_abbrev(state: str) -> Optional[str]:
    if state in STATE_ABBREV_OVERRIDES:
        return STATE_ABBREV_OVERRIDES[state]
    match = us.states.lookup(state)
    return match.abbr if match else None
