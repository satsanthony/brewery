# Brewery Finder - Custom Feedback Feature Setup

This guide explains how to set up the custom feedback/visitor notes feature with your Railway PostgreSQL database.

## Overview

The brewery finder feature displays custom visitor feedback for breweries when users search by city. The feedback data comes from the `brewery.csv` file and is synced to a PostgreSQL database on Railway.

## Feature Components

1. **CSV Data Source**: `brewery.csv` - Contains brewery names, cities, states, and visitor notes
2. **PostgreSQL Database**: Railway PostgreSQL stores the data for reliable querying
3. **Auto Sync**: Data is automatically synced when the app starts if changes are detected
4. **Frontend Display**: Visitor notes appear in a "Visitor's Opinion" card below brewery results
5. **URL Linking**: Any URLs in the notes are automatically converted to clickable links

## Quick Setup (5 minutes)

### Step 1: Get Railway DATABASE_URL

1. Go to https://railway.app and sign in
2. Open your project
3. Click on the PostgreSQL service
4. Click "Connect" tab
5. Copy the `DATABASE_URL` (looks like: `postgresql://user:password@host:port/dbname`)

### Step 2: Configure .env File

1. Edit `.env` file in the brewery folder
2. Find the `DATABASE_URL=` line
3. Paste your Railway URL:
   ```
   DATABASE_URL=postgresql://user:password@host:port/dbname
   ```
4. Save the file

### Step 3: Run the App

```bash
python brewery.py
```

The app will:
- Create the `brewery_info` table in PostgreSQL
- Sync all brewery data from CSV to the database
- Start listening on http://localhost:5000

### Step 4: Verify Setup

Open http://localhost:5000/db_status

You should see:
```json
{
  "status": "connected",
  "brewery_count": 5
}
```

## CSV Data Format

Your `brewery.csv` must have these columns:

```
Name of Brewery,City,State,My notes
Smog City Brewing Co,Torrance,CA,"Nestled in Torrance... Plan your trip at https://www.smogcitybrewing.com/"
```

- **Name of Brewery**: Full brewery name
- **City**: City location
- **State**: State code (e.g., CA, NY, TX)
- **My notes**: Visitor feedback (supports URLs, special characters)

## How It Works

### Data Flow

```
1. brewery.csv (local file)
          ↓
2. App Startup (checks for changes)
          ↓
3. CSV Sync (DATABASE_URL must be configured)
          ↓
4. PostgreSQL brewery_info table
          ↓
5. User searches by city
          ↓
6. Visitor notes displayed with clickable URLs
```

### Search Flow

When a user searches for breweries in a city:

1. App queries OpenBreweryDB for breweries in that city
2. For each result, app checks PostgreSQL `brewery_info` table
3. Matches brewery by: city + state + partial name match
4. Retrieves visitor notes from database
5. Returns notes in JSON response
6. Frontend displays notes in styled "Visitor's Opinion" card

### URL Handling

Any URLs in the notes are automatically:
- Detected (https://example.com)
- Converted to clickable links
- Opened in new tab (target="_blank")

## Updating Brewery Data

### From Local CSV

1. Edit `brewery.csv` locally
2. Add/modify brewery entries
3. Restart the app
4. App detects changes and auto-syncs to database

### Trigger Manual Sync

If auto-sync doesn't work:

```bash
curl -X POST http://localhost:5000/sync_csv
```

Response:
```json
{"status": "CSV sync completed"}
```

## Troubleshooting

### Database Connection Issues

**Problem**: `status: "not_connected"` from `/db_status`

**Solution**:
1. Verify DATABASE_URL is correct in .env
2. Check URL format: `postgresql://user:password@host:port/dbname`
3. Ensure Railway PostgreSQL is running
4. Restart the app after updating .env

### Visitor Notes Not Showing

**Problem**: Notes appear blank in search results

**Possible Causes**:
1. Database not synced - Check `/db_status` should show `brewery_count > 0`
2. Brewery name doesn't match - Check CSV "Name of Brewery" matches OpenBreweryDB results
3. City/state mismatch - Ensure CSV city and state match search location

**Solution**:
1. Check `/db_status` endpoint
2. Verify CSV data format
3. Manually trigger sync: `POST /sync_csv`

### CSV Won't Sync

**Problem**: CSV changes aren't appearing in database

**Solution**:
1. Check CSV file exists at: `brewery.csv` (same folder as app)
2. Verify encoding: File should be readable as UTF-8 or Latin-1
3. Check app logs for error messages
4. Try manual sync: `POST /sync_csv`

## Production Deployment (Railway)

### Step 1: Push to GitHub

```bash
git add .
git commit -m "Add brewery visitor feedback feature"
git push origin main
```

### Step 2: Configure Railway

1. Create a new Railway project
2. Add PostgreSQL service
3. Connect to GitHub repo
4. Set environment variables:
   - `GEMINI_API_KEY`: Your Google Gemini API key
   - `GOOGLE_CSE_API_KEY`: Your Google search API key
   - `GOOGLE_CSE_CX`: Your Google search engine ID
   - `DATABASE_URL`: Will be auto-set by Railway (or set manually)

### Step 3: Deploy

Railway will automatically:
- Clone your repo
- Install dependencies
- Start the app with `python brewery.py`
- App syncs CSV on startup

## Available Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Home page with search form |
| `/search_brewery` | GET | Search breweries by city (params: location) |
| `/db_status` | GET | Check database connection & count |
| `/sync_csv` | POST | Manually trigger CSV sync |

## API Response Format

### Search Breweries Response

```json
{
  "results": [
    {
      "name": "Smog City Brewing Co",
      "address": "123 Main St, Torrance, CA",
      "description": "A craft brewery...",
      "food": "Food trucks available",
      "beers": [
        {"name": "IPA", "abv": "6.5%", "ibu": "65"}
      ],
      "visitor_notes": "Nestled in Torrance... https://example.com"
    }
  ]
}
```

## Database Schema

### brewery_info Table

```sql
CREATE TABLE brewery_info (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  city TEXT NOT NULL,
  state TEXT NOT NULL,
  notes TEXT
);
```

### csv_meta Table

```sql
CREATE TABLE csv_meta (
  key VARCHAR(100) PRIMARY KEY,
  value TEXT NOT NULL
);
```

Used to track CSV hash and prevent unnecessary syncs.

## Tips & Best Practices

1. **Keep CSV Updated**: Update locally, restart app to sync
2. **Test URLs**: Ensure URLs in notes are valid
3. **Check Encodings**: CSV should handle special characters (é, ñ, etc.)
4. **Monitor Syncs**: Watch app logs for sync progress
5. **Backup CSV**: Keep a backup of your CSV file

## Support

If you encounter issues:
1. Check `/db_status` endpoint
2. Review app logs for error messages
3. Verify CSV format matches expected columns
4. Ensure Railway PostgreSQL is running
5. Try restarting the app
