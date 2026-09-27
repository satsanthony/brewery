import traceback
import logging
import os
import json
import csv
import io
import hashlib
import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template
import psycopg2
from psycopg2.extras import RealDictCursor

import google.generativeai as genai

# Load environment variables
load_dotenv()
GEMINI_API_KEY           = os.getenv("GEMINI_API_KEY")
GOOGLE_CSE_API_KEY       = os.getenv("GOOGLE_CSE_API_KEY")
GOOGLE_CSE_CX            = os.getenv("GOOGLE_CSE_CX")
GOOGLE_PLACES_API_KEY    = os.getenv("GOOGLE_PLACES_API_KEY")
DATABASE_URL             = os.getenv("DATABASE_URL")
BREWERY_CSV_PATH         = os.path.join(os.path.dirname(__file__), "brewery.csv")

app = Flask(__name__)

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)
SELECTED_MODEL = 'gemini-3.1-flash-lite-preview'

# In-memory cache for CSV brewery data
_brewery_csv_cache = {}
_csv_cache_hash = None

# Map full US state names to their 2-letter abbreviations (and the reverse), used both
# for matching CSV visitor notes and for building OpenBreweryDB `by_state` queries.
STATE_ABBREV_MAP = {
    'alabama': 'al', 'alaska': 'ak', 'arizona': 'az', 'arkansas': 'ar',
    'california': 'ca', 'colorado': 'co', 'connecticut': 'ct', 'delaware': 'de',
    'florida': 'fl', 'georgia': 'ga', 'hawaii': 'hi', 'idaho': 'id',
    'illinois': 'il', 'indiana': 'in', 'iowa': 'ia', 'kansas': 'ks',
    'kentucky': 'ky', 'louisiana': 'la', 'maine': 'me', 'maryland': 'md',
    'massachusetts': 'ma', 'michigan': 'mi', 'minnesota': 'mn', 'mississippi': 'ms',
    'missouri': 'mo', 'montana': 'mt', 'nebraska': 'ne', 'nevada': 'nv',
    'new hampshire': 'nh', 'new jersey': 'nj', 'new mexico': 'nm', 'new york': 'ny',
    'north carolina': 'nc', 'north dakota': 'nd', 'ohio': 'oh', 'oklahoma': 'ok',
    'oregon': 'or', 'pennsylvania': 'pa', 'rhode island': 'ri', 'south carolina': 'sc',
    'south dakota': 'sd', 'tennessee': 'tn', 'texas': 'tx', 'utah': 'ut',
    'vermont': 'vt', 'virginia': 'va', 'washington': 'wa', 'west virginia': 'wv',
    'wisconsin': 'wi', 'wyoming': 'wy'
}
ABBREV_TO_STATE = {v: k for k, v in STATE_ABBREV_MAP.items()}

# ── PostgreSQL helpers ────────────────────────────────────────────────────────

def get_db_connection():
    if not DATABASE_URL:
        return None
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        logger.error(f"DB connect error: {e}")
        return None

def init_db():
    """Initialize database tables on startup."""
    conn = get_db_connection()
    if not conn:
        logger.warning("Cannot initialize database - no connection available")
        return False

    try:
        cur = conn.cursor()

        # Create brewery_info table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS brewery_info (
                id    SERIAL PRIMARY KEY,
                name  TEXT NOT NULL,
                city  TEXT NOT NULL,
                state TEXT NOT NULL,
                notes TEXT
            )
        """)

        # Add columns for pre-existing tables that predate them.
        cur.execute("ALTER TABLE brewery_info ADD COLUMN IF NOT EXISTS address TEXT")
        cur.execute("ALTER TABLE brewery_info ADD COLUMN IF NOT EXISTS gastronomy TEXT")

        # Collapse any duplicate rows (same name/city/state) left over from the
        # old delete-all-then-reinsert sync, keeping the earliest row's id, so a
        # unique index can be created on that triple.
        cur.execute("""
            DELETE FROM brewery_info a
            USING brewery_info b
            WHERE a.id > b.id
              AND LOWER(a.name) = LOWER(b.name)
              AND LOWER(a.city) = LOWER(b.city)
              AND LOWER(a.state) = LOWER(b.state)
        """)

        # Unique index used as the upsert conflict target in sync_brewery_csv,
        # which also guarantees duplicates can't reappear even if the sync runs
        # concurrently from multiple workers.
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS brewery_info_name_city_state_idx
            ON brewery_info (LOWER(name), LOWER(city), LOWER(state))
        """)
        logger.info("brewery_info table created/verified")

        # Create csv_meta table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS csv_meta (
                key   VARCHAR(100) PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        logger.info("csv_meta table created/verified")

        conn.commit()
        cur.close()
        logger.info("[SUCCESS] Database tables ready")
        return True

    except Exception as e:
        logger.error(f"[ERROR] init_db failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        conn.rollback()
        return False
    finally:
        conn.close()

def sync_brewery_csv():
    """Load brewery.csv from local file and re-populate brewery_info only when changed."""
    conn = get_db_connection()
    if not conn:
        logger.warning("Database not available - CSV sync skipped")
        return False

    try:
        if not os.path.exists(BREWERY_CSV_PATH):
            logger.error(f"brewery.csv not found at {BREWERY_CSV_PATH}")
            return False

        # Read CSV file with encoding fallback
        try:
            with open(BREWERY_CSV_PATH, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            logger.info("UTF-8 decode failed, trying Latin-1")
            with open(BREWERY_CSV_PATH, 'r', encoding='latin-1') as f:
                content = f.read()

        logger.info(f"CSV file loaded: {len(content)} bytes")

        # Calculate hash for change detection
        new_hash = hashlib.md5(content.encode("utf-8").replace(b'\x97', b'-')).hexdigest()

        # Check if CSV has changed
        cur = conn.cursor()
        cur.execute("SELECT value FROM csv_meta WHERE key = 'brewery_csv_hash'")
        row = cur.fetchone()

        if row and row[0] == new_hash:
            logger.info("CSV unchanged - skipping sync")
            cur.close()
            return True

        # Parse CSV
        reader = csv.DictReader(io.StringIO(content))
        rows = [r for r in reader if r.get("Name of Brewery", "").strip()]
        logger.info(f"CSV has {len(rows)} brewery entries")

        # Upsert each row instead of wiping the table first. This keeps existing
        # ids stable, only writes net-new/changed data, and - combined with the
        # unique index from init_db - can't produce duplicates even if two
        # workers run this at the same time (the old DELETE-then-INSERT could
        # race and double-insert rows).
        for r in rows:
            cur.execute("""
                INSERT INTO brewery_info (name, city, state, notes, address, gastronomy)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (LOWER(name), LOWER(city), LOWER(state))
                DO UPDATE SET
                    name       = EXCLUDED.name,
                    city       = EXCLUDED.city,
                    state      = EXCLUDED.state,
                    notes      = EXCLUDED.notes,
                    address    = COALESCE(NULLIF(EXCLUDED.address, ''), brewery_info.address),
                    gastronomy = COALESCE(NULLIF(EXCLUDED.gastronomy, ''), brewery_info.gastronomy)
            """, (r.get("Name of Brewery", "").strip(),
                  r.get("City", "").strip(),
                  r.get("State", "").strip(),
                  r.get("My notes", "").strip(),
                  r.get("Address", "").strip(),
                  r.get("Gastronomy", "").strip()))

        # Remove rows for breweries no longer in the CSV.
        cur.execute("SELECT id, name, city, state FROM brewery_info")
        csv_keys = {
            (r.get("Name of Brewery", "").strip().lower(),
             r.get("City", "").strip().lower(),
             r.get("State", "").strip().lower())
            for r in rows
        }
        stale_ids = [
            row_id for row_id, name, city, state in cur.fetchall()
            if (name.lower(), city.lower(), state.lower()) not in csv_keys
        ]
        if stale_ids:
            cur.execute("DELETE FROM brewery_info WHERE id = ANY(%s)", (stale_ids,))
            logger.info(f"Removed {len(stale_ids)} brewery rows no longer in CSV")

        # Update hash
        cur.execute("""
            INSERT INTO csv_meta (key, value) VALUES ('brewery_csv_hash', %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (new_hash,))

        conn.commit()
        cur.close()
        logger.info(f"[SUCCESS] CSV synced: {len(rows)} breweries loaded")
        return True

    except Exception as e:
        logger.error(f"[ERROR] CSV sync failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        conn.rollback()
        return False
    finally:
        conn.close()

def get_brewery_extra_info(api_name: str, city: str, state: str) -> dict:
    """
    Return {"notes": ..., "address": ..., "gastronomy": ...} for a brewery if
    name + city + state match a brewery_info row. Name matching is
    case-insensitive and checks whether one name contains the other,
    handling minor differences between the CSV and OpenBreweryDB naming.
    State matching handles both full names (California) and abbreviations
    (CA). Falls back to the CSV file if the database is not available.
    """
    # Normalize state to 2-letter abbreviation
    state_lower = state.strip().lower()
    if state_lower in STATE_ABBREV_MAP:
        state_normalized = STATE_ABBREV_MAP[state_lower]
    else:
        state_normalized = state_lower

    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            # Query with both exact match and abbreviation match
            cur.execute("""
                SELECT name, notes, address, gastronomy FROM brewery_info
                WHERE LOWER(city)=%s AND (LOWER(state)=%s OR LOWER(state)=%s)
            """,
                (city.strip().lower(), state_normalized, state_lower),
            )
            candidates = cur.fetchall()
            api_lower  = api_name.lower().strip()
            for row in candidates:
                db_lower = row["name"].lower().strip()
                if db_lower in api_lower or api_lower in db_lower:
                    return {
                        "notes": row["notes"] or "",
                        "address": row["address"] or "",
                        "gastronomy": row["gastronomy"] or "",
                    }
            cur.close()
            return {"notes": "", "address": "", "gastronomy": ""}
        except Exception as e:
            logger.error(f"get_brewery_extra_info DB error: {e}")
        finally:
            conn.close()

    return get_brewery_extra_info_from_csv(api_name, city, state)

def get_brewery_extra_info_from_csv(api_name: str, city: str, state: str) -> dict:
    """Fallback method to read visitor notes/address directly from CSV file."""
    global _brewery_csv_cache, _csv_cache_hash

    try:
        if not os.path.exists(BREWERY_CSV_PATH):
            return {"notes": "", "address": "", "gastronomy": ""}

        try:
            with open(BREWERY_CSV_PATH, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(BREWERY_CSV_PATH, 'r', encoding='latin-1') as f:
                content = f.read()

        file_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        if file_hash != _csv_cache_hash:
            _brewery_csv_cache.clear()
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                row_city = row.get("City", "").strip().lower()
                row_state = row.get("State", "").strip().lower()
                key = f"{row_city}|{row_state}"
                if key not in _brewery_csv_cache:
                    _brewery_csv_cache[key] = []
                _brewery_csv_cache[key].append(row)
            _csv_cache_hash = file_hash

        api_lower = api_name.lower().strip()
        city_lower = city.strip().lower()
        state_lower = state.strip().lower()
        key = f"{city_lower}|{state_lower}"

        for row in _brewery_csv_cache.get(key, []):
            db_name = row.get("Name of Brewery", "").strip()
            if db_name.lower() in api_lower or api_lower in db_name.lower():
                return {
                    "notes": row.get("My notes", "").strip(),
                    "address": row.get("Address", "").strip(),
                    "gastronomy": row.get("Gastronomy", "").strip(),
                }

        return {"notes": "", "address": "", "gastronomy": ""}
    except Exception as e:
        logger.error(f"get_brewery_extra_info_from_csv error: {e}")
        return {"notes": "", "address": "", "gastronomy": ""}

def get_csv_rows_for_location(location: str) -> list:
    """
    Return raw CSV rows whose City/State match the parsed location filters,
    used to surface breweries that have visitor notes but no OpenBreweryDB
    record (e.g. Malibu Brewing Co).
    """
    try:
        if not os.path.exists(BREWERY_CSV_PATH):
            return []

        try:
            with open(BREWERY_CSV_PATH, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(BREWERY_CSV_PATH, 'r', encoding='latin-1') as f:
                content = f.read()

        params = parse_location_query(location)
        target_city = params.get('by_city', '').replace('_', ' ').strip().lower()
        target_state_raw = params.get('by_state', '').replace('_', ' ').strip().lower()
        target_state = STATE_ABBREV_MAP.get(target_state_raw, target_state_raw)

        matches = []
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            name = row.get("Name of Brewery", "").strip()
            if not name:
                continue
            row_city = row.get("City", "").strip().lower()
            row_state_raw = row.get("State", "").strip().lower()
            row_state = STATE_ABBREV_MAP.get(row_state_raw, row_state_raw)

            if target_city and row_city != target_city:
                continue
            if target_state and row_state != target_state:
                continue
            matches.append(row)

        return matches
    except Exception as e:
        logger.error(f"get_csv_rows_for_location error: {e}")
        return []

# Beer menus for breweries that have no OpenBreweryDB record, keyed by the
# brewery's exact name as it appears in brewery.csv (lowercased). Populate
# manually from the brewery's own menu when OpenBreweryDB has no listing.
LOCAL_ONLY_BEER_MENUS = {
    "malibu brewing co": [
        {"name": "Sand & Sea — Mexican-Style Lager", "abv": "4.4%", "ibu": "N/A"},
        {"name": "Pacific Gold — Pilsner", "abv": "4.8%", "ibu": "N/A"},
        {"name": "Happy Days — Honey Blonde Ale", "abv": "5.2%", "ibu": "N/A"},
        {"name": "Lineup — Pale Ale", "abv": "5.9%", "ibu": "N/A"},
        {"name": "Wild Grove — Hazy IPA", "abv": "6.0%", "ibu": "N/A"},
        {"name": "First Point — American IPA", "abv": "6.9%", "ibu": "N/A"},
        {"name": "Ladyface — West Coast IPA", "abv": "7.2%", "ibu": "N/A"},
        {"name": "Party Wave — Hoppy Lager", "abv": "7.4%", "ibu": "N/A"},
        {"name": "Tower 17 — Hazy Double IPA", "abv": "8.2%", "ibu": "N/A"},
        {"name": "Oktoberfest — Bavarian-Style Festbier", "abv": "5.9%", "ibu": "N/A"},
        {"name": "Thank You, Fritz — California Common", "abv": "5.2%", "ibu": "N/A"},
        {"name": "Big Rock — Amber Lager", "abv": "5.9%", "ibu": "N/A"},
        {"name": "Overly Ambitious — Belgian Dark Strong", "abv": "9.9%", "ibu": "N/A"},
        {"name": "Undertow — Dry Stout", "abv": "4.7%", "ibu": "N/A"},
        {"name": "Canyon — Rosé Lager", "abv": "4.2%", "ibu": "N/A"},
        {"name": "Westward — Berliner Weisse", "abv": "3.7%", "ibu": "N/A"},
        {"name": "Hatch — Green Chile Lager", "abv": "4.4%", "ibu": "N/A"},
        {"name": "Zuma — Agave Hard Seltzer", "abv": "5.1%", "ibu": "N/A"},
        {"name": "Mali-Bougie — Hard Cider (Balcom Canyon Cidery collab)", "abv": "6.3%", "ibu": "N/A"},
    ],
}

def get_local_only_breweries(location: str, existing_names: list) -> list:
    """
    Return CSV-only breweries for this location that have no OpenBreweryDB
    record (so they were not already found by search_open_brewery_db).
    """
    existing_lower = [n.lower().strip() for n in existing_names]
    local_only = []

    for row in get_csv_rows_for_location(location):
        name = row.get("Name of Brewery", "").strip()
        name_lower = name.lower()
        if any(name_lower in n or n in name_lower for n in existing_lower):
            continue
        local_only.append(row)

    return local_only

# ── Startup ───────────────────────────────────────────────────────────────────
init_db()
sync_brewery_csv()

def parse_location_query(location):
    """
    Parse a user-typed location into OpenBreweryDB `by_city`/`by_state` filters.
    Accepts "City, State" (e.g. "Chino, CA"), a bare state name/abbreviation
    (e.g. "California"), or a bare city (e.g. "Chino"). The API's `by_state`
    filter requires the full state name, so abbreviations are expanded.
    """
    location = location.strip()

    def normalize_state(raw_state):
        key = raw_state.strip().lower()
        full_name = ABBREV_TO_STATE.get(key, key if key in STATE_ABBREV_MAP else key)
        return full_name.replace(' ', '_')

    if ',' in location:
        city_part, state_part = location.split(',', 1)
        params = {'by_city': city_part.strip().replace(' ', '_')}
        if state_part.strip():
            params['by_state'] = normalize_state(state_part)
        return params

    lowered = location.lower()
    if lowered in STATE_ABBREV_MAP or lowered in ABBREV_TO_STATE:
        return {'by_state': normalize_state(location)}

    return {'by_city': location.replace(' ', '_')}

def search_open_brewery_db(location):
    try:
        params = parse_location_query(location)
        params['by_country'] = 'united_states'
        # 200 is OpenBreweryDB's max per_page - use it so results for a city
        # aren't artificially truncated.
        params['per_page'] = 200

        url = "https://api.openbrewerydb.org/v1/breweries"
        response = requests.get(url, params=params)
        response.raise_for_status()
        results = response.json()

        # Defensive filter in case the API's by_country match is inexact.
        return [b for b in results if b.get('country') in (None, 'United States')]
    except Exception as e:
        logger.error(f"Open Brewery DB Error: {e}")
        return []

def google_web_search(query):
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {'key': GOOGLE_CSE_API_KEY, 'cx': GOOGLE_CSE_CX, 'q': query}
        response = requests.get(url, params=params)
        response.raise_for_status()
        results = response.json().get('items', [])
        return [{"snippet": r['snippet']} for r in results]
    except Exception as e:
        logger.error(f"Google Search Error: {e}")
        return []

PLACES_STATUS_MAP = {
    'OPERATIONAL': 'open',
    'CLOSED_TEMPORARILY': 'temporarily_closed',
    'CLOSED_PERMANENTLY': 'permanently_closed',
}

def get_business_status(name, city, state):
    """
    Look up a brewery's live operating status via the Google Places "Find Place
    From Text" endpoint, which reports business_status (OPERATIONAL,
    CLOSED_TEMPORARILY, CLOSED_PERMANENTLY) straight from Google Maps data.
    OpenBreweryDB has no such field and is frequently stale, so this is the
    source of truth for closures. Defaults to "open" if the key is missing,
    the place can't be matched, or the lookup fails.
    """
    if not GOOGLE_PLACES_API_KEY:
        return 'open'

    try:
        url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
        params = {
            'input': f"{name} {city} {state}",
            'inputtype': 'textquery',
            'fields': 'business_status',
            'key': GOOGLE_PLACES_API_KEY,
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        candidates = data.get('candidates', [])
        if not candidates:
            return 'open'

        business_status = candidates[0].get('business_status', 'OPERATIONAL')
        return PLACES_STATUS_MAP.get(business_status, 'open')
    except Exception as e:
        logger.error(f"Google Places Error: {e}")
        return 'open'

def get_gemini_structured_data(brewery_name, location, web_data):
    try:
        model = genai.GenerativeModel(
            model_name=SELECTED_MODEL,
            generation_config={"response_mime_type": "application/json"}
        )
        
        prompt = f"""
        Research the brewery '{brewery_name}' in '{location}'.
        Search Context: {json.dumps(web_data)}.

        Using the search context AND your internal knowledge, return a JSON object with:
        - "description": A professional 2-sentence summary of the brewery.
        - "food_info": Details about on-site food or nearby food trucks, no more than 3 lines.
        - "top_beers": An array of 3 objects. Each MUST have "name", "abv", and "ibu".

        If you cannot find specific ABV/IBU, provide your best estimate or "N/A".
        """
        response = model.generate_content(prompt)
        return json.loads(response.text)
    except Exception as e:
        logger.error(f"Gemini Error: {e}")
        return None

def get_food_info(brewery_name, city, state):
    """
    Look up food/food-truck info for a brewery via Google search, summarized
    to at most 3 lines by Gemini. Used for CSV-only breweries that have no
    OpenBreweryDB/AI record to source "food_info" from.
    """
    try:
        web_data = google_web_search(f"{brewery_name} {city} {state} brewery food menu food truck")
        if not web_data:
            return "No food information available."

        model = genai.GenerativeModel(
            model_name=SELECTED_MODEL,
            generation_config={"response_mime_type": "application/json"}
        )
        prompt = f"""
        Search Context about food at the brewery '{brewery_name}' in '{city}, {state}':
        {json.dumps(web_data)}

        Return a JSON object with one field:
        - "food_info": A summary of on-site food or nearby food trucks, no more than 3 lines.
          If the search context has no food information, set this to "No food information available."
        """
        response = model.generate_content(prompt)
        data = json.loads(response.text)
        return data.get("food_info") or "No food information available."
    except Exception as e:
        logger.error(f"get_food_info error: {e}")
        return "No food information available."

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/search_brewery', methods=['GET'])
def search_brewery():
    location = request.args.get('location')
    if not location:
        return jsonify({"error": "No location provided"}), 400

    try:
        breweries = search_open_brewery_db(location)
        final_results = []

        for b in breweries:
            status = get_business_status(b['name'], b.get('city', ''), b.get('state', ''))
            if status == 'permanently_closed':
                continue

            web_info = google_web_search(f"{b['name']} {location} brewery beer list food")
            ai_data = get_gemini_structured_data(b['name'], location, web_info)
            extra = get_brewery_extra_info(b['name'], b.get('city', ''), b.get('state', ''))
            # Prefer the visitor-verified address from the CSV; fall back to
            # OpenBreweryDB's street address when the CSV has none on file.
            address = extra["address"] or f"{b.get('street', 'Address not listed')}, {b.get('city', '')}, {b.get('state', '')}"

            if ai_data:
                final_results.append({
                    "name": b['name'],
                    "address": address,
                    "description": ai_data.get('description', 'No description available.'),
                    # Prefer the visitor-verified Gastronomy note from the CSV
                    # over the AI-generated food_info when one is on file.
                    "food": extra["gastronomy"] or ai_data.get('food_info', 'No food information available.'),
                    "beers": ai_data.get('top_beers', []),
                    "visitor_notes": extra["notes"],
                    "status": status,
                })

        # Breweries with visitor notes in the CSV but no OpenBreweryDB record
        # (e.g. Malibu Brewing Co) don't show up in the loop above at all, so
        # surface them directly from the CSV instead.
        seen_names = [r['name'] for r in final_results]
        for row in get_local_only_breweries(location, seen_names):
            name = row.get("Name of Brewery", "").strip()
            city = row.get("City", "").strip()
            state = row.get("State", "").strip()
            notes = row.get("My notes", "").strip()
            address = row.get("Address", "").strip() or f"{city}, {state}"
            gastronomy = row.get("Gastronomy", "").strip()

            status = get_business_status(name, city, state)
            if status == 'permanently_closed':
                continue

            final_results.append({
                "name": name,
                "address": address,
                # No OpenBreweryDB/AI data exists for this brewery, so the
                # visitor's own notes stand in as the description.
                "description": notes or "No description available.",
                "food": gastronomy or get_food_info(name, city, state),
                "beers": LOCAL_ONLY_BEER_MENUS.get(name.lower(), []),
                "visitor_notes": "",
                "status": status,
            })

        return jsonify({"results": final_results})
    except Exception:
        logger.error(traceback.format_exc())
        return jsonify({"error": "Internal Server Error"}), 500

@app.route('/sync_csv', methods=['POST'])
def sync_csv():
    """Manual endpoint to trigger CSV sync to database."""
    try:
        sync_brewery_csv()
        return jsonify({"status": "CSV sync completed"}), 200
    except Exception as e:
        logger.error(f"Sync endpoint error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/db_status', methods=['GET'])
def db_status():
    """Check database connection status."""
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM brewery_info")
            count = cur.fetchone()[0]
            cur.close()
            return jsonify({
                "status": "connected",
                "brewery_count": count
            }), 200
        except Exception as e:
            return jsonify({
                "status": "error",
                "message": str(e)
            }), 500
        finally:
            conn.close()
    else:
        return jsonify({
            "status": "not_connected",
            "message": "DATABASE_URL not configured"
        }), 503

@app.route('/debug_visitor_notes', methods=['GET'])
def debug_visitor_notes():
    """Debug endpoint to check visitor notes retrieval."""
    brewery_name = request.args.get('name', 'Mammoth Brewing Company')
    city = request.args.get('city', 'Mammoth Lakes')
    state = request.args.get('state', 'California')

    extra = get_brewery_extra_info(brewery_name, city, state)
    notes = extra["notes"]

    return jsonify({
        "brewery_name": brewery_name,
        "city": city,
        "state": state,
        "visitor_notes": notes,
        "address": extra["address"],
        "has_notes": bool(notes and len(notes.strip()) > 0)
    }), 200

@app.route('/debug_breweries', methods=['GET'])
def debug_breweries():
    """Debug endpoint to show all breweries in database."""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database not connected"}), 503

    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, city, state, LENGTH(notes) as notes_length FROM brewery_info ORDER BY city, name")
        breweries = cur.fetchall()
        cur.close()

        result = []
        for id, name, city, state, notes_len in breweries:
            result.append({
                "id": id,
                "name": name,
                "city": city,
                "state": state,
                "notes_length": notes_len
            })

        return jsonify({
            "total_breweries": len(result),
            "breweries": result
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/debug_env', methods=['GET'])
def debug_env():
    """Debug endpoint to check environment variables."""
    db_url = os.getenv("DATABASE_URL")
    db_public = os.getenv("DATABASE_PUBLIC_URL")
    gemini_key = os.getenv("GEMINI_API_KEY")

    return jsonify({
        "DATABASE_URL": "SET" if db_url else "NOT SET",
        "DATABASE_PUBLIC_URL": "SET" if db_public else "NOT SET",
        "GEMINI_API_KEY": "SET" if gemini_key else "NOT SET",
        "environment_variables_found": {
            "DATABASE_URL": bool(db_url),
            "DATABASE_PUBLIC_URL": bool(db_public),
            "GEMINI_API_KEY": bool(gemini_key)
        }
    }), 200

if __name__ == '__main__':
    # host='0.0.0.0' tells Flask to listen on all public IPs on your network
    # use_reloader=False prevents environment variable issues with debug reloading
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)