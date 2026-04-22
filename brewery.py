import sys
import traceback
import logging
import os
import json
import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template

import google.generativeai as genai

# Load environment variables
load_dotenv()
GEMINI_API_KEY           = os.getenv("GEMINI_API_KEY")
GOOGLE_CSE_API_KEY       = os.getenv("GOOGLE_CSE_API_KEY")
GOOGLE_CSE_CX            = os.getenv("GOOGLE_CSE_CX")

app = Flask(__name__)

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)
SELECTED_MODEL = 'gemini-3.1-flash-lite-preview'

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
                    "beers": ai_data.get('top_beers', [])
                })

        return jsonify({"results": final_results})
    except Exception:
        logger.error(traceback.format_exc())
        return jsonify({"error": "Internal Server Error"}), 500

if __name__ == '__main__':
    # host='0.0.0.0' tells Flask to listen on all public IPs on your network
    app.run(host='0.0.0.0', port=5000, debug=True)