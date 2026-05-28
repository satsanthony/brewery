# Railway PostgreSQL Setup Guide

This guide explains how to connect the brewery app to your Railway PostgreSQL database and set up automatic CSV syncing.

## Step 1: Get Your Railway Database Credentials

1. Go to your Railway project dashboard
2. Click on your PostgreSQL database service
3. In the "Connect" tab, you'll see:
   - `DATABASE_URL` - Copy this entire connection string
   - `DATABASE_PUBLIC_URL` - (Optional) Use if DATABASE_URL doesn't work

## Step 2: Configure the .env File

1. Open `.env` in the brewery directory
2. Add your DATABASE_URL:
   ```
   DATABASE_URL=postgresql://user:password@host:port/dbname
   ```

3. Optional: If using the public URL:
   ```
   DATABASE_PUBLIC_URL=postgresql://user:password@host:port/dbname
   ```

## Step 3: Initialize the Database

When you start the app for the first time with DATABASE_URL configured:
- The `brewery_info` table will be automatically created
- The CSV file will be synced to the database

## Step 4: Verify the Connection

You can check the database connection status by visiting:
```
http://localhost:5000/db_status
```

This will show:
- Connection status
- Number of brewery entries in the database

## Step 5: Manual CSV Sync

To manually trigger a CSV sync to the database:
```
POST http://localhost:5000/sync_csv
```

This is useful when you update the CSV file locally.

## Automatic Sync on App Start

The app automatically:
1. Detects if the CSV file has changed (using MD5 hash)
2. Only re-syncs if changes are detected
3. Compares against the last synced version stored in `csv_meta` table

## Data Flow

```
brewery.csv (local file)
        ↓
[App Startup / CSV Change Detection]
        ↓
[Sync to PostgreSQL via DATABASE_URL]
        ↓
brewery_info table in Railway PostgreSQL
        ↓
[When user searches]
        ↓
get_visitor_notes() fetches from database
        ↓
Display visitor notes below search results
```

## CSV File Format

The brewery.csv must have these columns:
- `Name of Brewery` - Brewery name
- `City` - City location
- `State` - State/region code
- `My notes` - Visitor feedback (can contain URLs)

## Troubleshooting

### Database connection fails
- Check DATABASE_URL is correctly copied from Railway
- Ensure .env file is loaded (restart the app)
- Try the /db_status endpoint for more info

### CSV not syncing
- Check that brewery.csv exists in the brewery folder
- Try the /sync_csv endpoint manually
- Check app logs for error messages

### Visitor notes not showing
- Verify database connection via /db_status
- Check brewery_count is > 0
- Ensure brewery names/cities match in CSV

## Production Deployment

On Railway:
1. Set DATABASE_URL as an environment variable in Railway dashboard
2. No .env file is needed - Railway will use the env var
3. CSV syncing happens automatically on each app restart
4. For updates, replace the CSV file and restart the service
