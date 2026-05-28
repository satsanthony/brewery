# Brewery Visitor Feedback Feature - Setup Instructions

## What Was Done

The brewery finder now supports custom visitor feedback that displays below brewery search results. This feature:

✅ Reads from `brewery.csv` (Name, City, State, and Visitor Notes)
✅ Syncs data to PostgreSQL database on Railway
✅ Automatically detects CSV changes and updates database
✅ Displays visitor notes with clickable URLs
✅ Shows styled "A Visitor's Opinion" card below brewery results

## Your Next Steps (Quick Setup - ~5 minutes)

### 1. Get Your Railway DATABASE_URL

1. Visit https://railway.app (your existing project)
2. Click on PostgreSQL service
3. Go to "Connect" tab
4. Copy the `DATABASE_URL` string

### 2. Configure the Brewery App

**Option A: Using Setup Script (Recommended)**
```bash
python setup_railway.py
```
The script will:
- Ask for your DATABASE_URL
- Update the .env file
- Provide verification steps

**Option B: Manual Setup**
1. Edit `.env` file
2. Add your DATABASE_URL:
   ```
   DATABASE_URL=postgresql://user:password@host:port/dbname
   ```
3. Save and close

### 3. Start the App

```bash
python brewery.py
```

### 4. Verify It Works

Open http://localhost:5000/db_status

Expected response:
```json
{
  "status": "connected",
  "brewery_count": 5
}
```

### 5. Test the Feature

1. Go to http://localhost:5000
2. Search for a city (e.g., "Torrance")
3. Below each brewery result, you should see:
   - "A Visitor's Opinion" card
   - Custom feedback text
   - Clickable URLs in the feedback

## CSV Data Location

Your `brewery.csv` is located in: `./brewery/brewery.csv`

It contains these columns:
- Name of Brewery
- City
- State
- My notes (visitor feedback with URLs)

## How Sync Works

**Automatic Sync:**
- App checks CSV at startup
- Compares with last synced version
- Only syncs if CSV changed
- Updates PostgreSQL table

**Manual Sync:**
If you update the CSV and need immediate sync:
```bash
curl -X POST http://localhost:5000/sync_csv
```

## CSV Updates from GitHub

When you update `brewery.csv` and push to GitHub:

1. On Railway, the repo is auto-cloned
2. App restarts automatically
3. App detects CSV changes
4. Database syncs automatically
5. New feedback appears instantly

## Key Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /` | Main search page |
| `GET /search_brewery?location=City` | Search breweries |
| `GET /db_status` | Check database status |
| `POST /sync_csv` | Manual CSV sync |

## File Structure

```
brewery/
├── brewery.py              # Main Flask app
├── brewery.csv             # Visitor feedback data
├── .env                    # Configuration (add DATABASE_URL here)
├── requirements.txt        # Dependencies
├── setup_railway.py        # Setup helper script
├── BREWERY_SETUP.md        # Detailed guide
├── RAILWAY_SETUP.md        # Railway-specific guide
├── templates/
│   └── index.html         # Frontend (already configured)
└── static/
    └── images/
```

## Troubleshooting Quick Checklist

- [ ] DATABASE_URL is set in .env
- [ ] App is restarted after updating .env
- [ ] /db_status shows brewery_count > 0
- [ ] brewery.csv is readable and in correct folder
- [ ] Search terms match CSV brewery names

## Detailed Guides

For more information, read:
- `BREWERY_SETUP.md` - Complete feature guide
- `RAILWAY_SETUP.md` - Railway database specifics

## Next Steps After Initial Setup

### Update Visitor Feedback
1. Edit `brewery.csv`
2. Update brewery info and notes
3. Restart app (auto-syncs) OR run `curl -X POST .../sync_csv`

### Deploy to Production
1. Push updated `brewery.csv` to GitHub
2. Railway auto-deploys
3. Database syncs automatically

### Monitor Health
Check `/db_status` regularly to ensure:
- Database is connected
- Brewery count matches CSV
- No sync errors

## Example Visitor Notes Format

```
Name of Brewery,City,State,My notes
"Smog City Brewing Co","Torrance","CA","Nestled in tranquil Torrance. Great outdoor seating. The Squirrel Red Ale is a must-try. Visit: https://www.smogcitybrewing.com/"
```

**Note:** URLs are automatically detected and converted to clickable links in the frontend.

## Support Resources

- Railway Dashboard: https://railway.app
- PostgreSQL Docs: https://www.postgresql.org/docs/
- App Logs: Check terminal output when running `python brewery.py`
- Status Endpoint: `http://localhost:5000/db_status`

---

**Ready to start?**
Run: `python setup_railway.py`
