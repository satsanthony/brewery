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
    conn = get_db_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS brewery_info (
                id    SERIAL PRIMARY KEY,
                name  TEXT NOT NULL,
                city  TEXT NOT NULL,
                state TEXT NOT NULL,
                notes TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS csv_meta (
                key   VARCHAR(100) PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.commit()
        cur.close()
        logger.info("DB tables ready")
    except Exception as e:
        logger.error(f"init_db error: {e}")
    finally:
        conn.close()

def sync_brewery_csv():
    """Load brewery.csv from local file and re-populate brewery_info only when changed."""
    conn = get_db_connection()
    if not conn:
        logger.info("Database not available - skipping CSV sync")
        return
    try:
        if not os.path.exists(BREWERY_CSV_PATH):
            logger.warning(f"brewery.csv not found at {BREWERY_CSV_PATH}")
            return

        try:
            with open(BREWERY_CSV_PATH, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(BREWERY_CSV_PATH, 'r', encoding='latin-1') as f:
                content = f.read()

        new_hash = hashlib.md5(content.encode("utf-8").replace(b'\x97', b'-')).hexdigest()

        cur = conn.cursor()
        cur.execute("SELECT value FROM csv_meta WHERE key = 'brewery_csv_hash'")
        row = cur.fetchone()
        if row and row[0] == new_hash:
            logger.info("Brewery CSV unchanged — skipping sync")
            cur.close()
            return

        reader = csv.DictReader(io.StringIO(content))
        rows   = [r for r in reader if r.get("Name of Brewery", "").strip()]

        cur.execute("DELETE FROM brewery_info")
        for r in rows:
            cur.execute(
                "INSERT INTO brewery_info (name, city, state, notes) VALUES (%s, %s, %s, %s)",
                (r.get("Name of Brewery", "").strip(),
                 r.get("City", "").strip(),
                 r.get("State", "").strip(),
                 r.get("My notes", "").strip()),
            )
        cur.execute("""
            INSERT INTO csv_meta (key, value) VALUES ('brewery_csv_hash', %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (new_hash,))
        conn.commit()
        cur.close()
        logger.info(f"Brewery CSV synced: {len(rows)} rows")
    except Exception as e:
        logger.error(f"sync_brewery_csv error: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        conn.close()

def get_visitor_notes(api_name: str, city: str, state: str) -> str:
    """
    Return the notes for a brewery if name + city + state match a brewery_info row.
    Name matching is case-insensitive and checks whether one name contains the other,
    handling minor differences between the CSV and OpenBreweryDB naming.
    State matching handles both full names (California) and abbreviations (CA).
    Fallback to CSV file if database is not available.
    """
    # Map full state names to abbreviations
    state_abbrev_map = {
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

    # Normalize state to 2-letter abbreviation
    state_lower = state.strip().lower()
    if state_lower in state_abbrev_map:
        state_normalized = state_abbrev_map[state_lower]
    else:
        state_normalized = state_lower

    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            # Query with both exact match and abbreviation match
            cur.execute("""
                SELECT name, notes FROM brewery_info
                WHERE LOWER(city)=%s AND (LOWER(state)=%s OR LOWER(state)=%s)
            """,
                (city.strip().lower(), state_normalized, state_lower),
            )
            candidates = cur.fetchall()
            api_lower  = api_name.lower().strip()
            for row in candidates:
                db_lower = row["name"].lower().strip()
                if db_lower in api_lower or api_lower in db_lower:
                    return row["notes"] or ""
            cur.close()
            return ""
        except Exception as e:
            logger.error(f"get_visitor_notes DB error: {e}")
        finally:
            conn.close()

    return get_visitor_notes_from_csv(api_name, city, state)

def get_visitor_notes_from_csv(api_name: str, city: str, state: str) -> str:
    """Fallback method to read visitor notes directly from CSV file."""
    global _brewery_csv_cache, _csv_cache_hash

    try:
        if not os.path.exists(BREWERY_CSV_PATH):
            return ""

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
                return row.get("My notes", "").strip()

        return ""
    except Exception as e:
        logger.error(f"get_visitor_notes_from_csv error: {e}")
        return ""

# ── Startup ───────────────────────────────────────────────────────────────────
init_db()
sync_brewery_csv()

def search_open_brewery_db(location):
    try:
        # Increased per_page to 15 to ensure we find the most popular breweries
        url = f"https://api.openbrewerydb.org/v1/breweries?by_city={location}&per_page=8"
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
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
        - "food_info": Details about on-site food or nearby food trucks.
        - "top_beers": An array of 3 objects. Each MUST have "name", "abv", and "ibu". 
        
        If you cannot find specific ABV/IBU, provide your best estimate or "N/A".
        """
        response = model.generate_content(prompt)
        return json.loads(response.text)
    except Exception as e:
        logger.error(f"Gemini Error: {e}")
        return None

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
            web_info = google_web_search(f"{b['name']} {location} brewery beer list food")
            ai_data = get_gemini_structured_data(b['name'], location, web_info)

            if ai_data:
                final_results.append({
                    "name": b['name'],
                    "address": f"{b.get('street', 'Address not listed')}, {b.get('city', '')}, {b.get('state', '')}",
                    "description": ai_data.get('description', 'No description available.'),
                    "food": ai_data.get('food_info', 'No food information available.'),
                    "beers": ai_data.get('top_beers', []),
                    "visitor_notes": get_visitor_notes(b['name'], b.get('city', ''), b.get('state', '')),
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

    notes = get_visitor_notes(brewery_name, city, state)

    return jsonify({
        "brewery_name": brewery_name,
        "city": city,
        "state": state,
        "visitor_notes": notes,
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