# Database Setup Guide - Railway PostgreSQL

This guide explains how to set up PostgreSQL on Railway for both the brewery and guilt_gulp applications.

## Overview

Both applications will use the **same PostgreSQL database** on Railway for:
- `brewery_info` table (brewery names, locations, visitor notes)
- `csv_meta` table (tracking CSV sync state)
- Any other data needed by guilt_gulp

## Prerequisites

- Railway account with a project created
- Brewery CSV file with: Name of Brewery, City, State, My notes columns

## Step 1: Create PostgreSQL Database on Railway

1. Go to https://railway.app
2. Open your project
3. Click **+ New** (top left)
4. Search for **PostgreSQL**
5. Click **PostgreSQL** to add service
6. Wait 1-2 minutes for initialization
7. You'll see it in your project dashboard

## Step 2: Get Connection Credentials

### For Local Development

1. Click **PostgreSQL** service
2. Go to **Connect** tab
3. Copy the **Connection String** (looks like: `postgresql://postgres:PASSWORD@metro.proxy.rlwy.net:PORT/railway`)
4. Save this value as `DATABASE_URL`

### For Production (Railway)

Railway auto-provides `DATABASE_URL` environment variable - no setup needed!

## Step 3: Configure Environment Variables

### Option A: Use Railway Dashboard (Recommended)

1. In your Railway project, go to **Settings** → **Environment Variables**
2. Add:
   ```
   DATABASE_URL=postgresql://postgres:PASSWORD@metro.proxy.rlwy.net:PORT/railway
   DATABASE_PUBLIC_URL=postgresql://postgres:PASSWORD@metro.proxy.rlwy.net:PORT/railway
   ```
3. Click **Save**
4. Railway auto-redeploys all services

### Option B: Configure Per Service

If you want separate configurations:

**For brewery service:**
1. Click **brewery** service
2. Go to **Variables** tab
3. Set `DATABASE_URL`

**For guilt_gulp service:**
1. Click **guilt_gulp** service
2. Go to **Variables** tab
3. Set `DATABASE_URL`

## Step 4: Verify Connection

Once deployed, test with these endpoints:

**Brewery app:**
```
https://your-brewery-app-url/db_status
https://your-brewery-app-url/debug_env
```

Expected response:
```json
{
  "status": "connected",
  "brewery_count": 5
}
```

**Guilt_gulp app:**
Check logs in Railway dashboard for successful database connection messages

## Step 5: Initial Data Sync

### For Brewery App

The app automatically:
1. Creates `brewery_info` table on startup
2. Creates `csv_meta` table on startup
3. Syncs brewery.csv data to database

To manually trigger sync:
```
POST https://your-brewery-app-url/sync_csv
```

### For Guilt_gulp App

Configure it to use the same DATABASE_URL. The sync process depends on your guilt_gulp implementation.

## Troubleshooting

### DATABASE_URL Not Set

**Problem:** `/debug_env` shows `DATABASE_URL: "NOT SET"`

**Solution:**
1. Check Railway Variables tab - is DATABASE_URL there?
2. If yes, force redeploy:
   - Click on your service
   - Go to **Deployments**
   - Click redeploy button
3. If no, add it from the Connect tab
4. Wait for deployment to complete

### Database Connection Failed

**Problem:** `/debug_breweries` returns `{"error": "Database not connected"}`

**Solution:**
1. Verify connection string is correct
2. Check PostgreSQL service is running (green status in Railway)
3. Try manually connecting with psql:
   ```bash
   psql postgresql://postgres:PASSWORD@metro.proxy.rlwy.net:PORT/railway
   ```
4. If psql fails, the connection string is wrong

### No Data After Sync

**Problem:** `/debug_breweries` returns 0 breweries

**Solution:**
1. Verify CSV file exists and is readable
2. Check CSV format: Name of Brewery, City, State, My notes
3. Trigger manual sync: `POST /sync_csv`
4. Check logs for sync errors

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

## Security Notes

- Never commit `.env` with DATABASE_URL to git
- `.env` is protected by `.gitignore`
- For production, use Railway's environment variables
- Rotate credentials if exposed

## Next Steps

1. Create PostgreSQL on Railway ✓
2. Get DATABASE_URL from Connect tab
3. Add DATABASE_URL to Railway environment variables
4. Deploy brewery and guilt_gulp apps
5. Test with `/db_status` and `/debug_breweries`
6. Monitor logs for any connection issues

## Support

If you encounter issues:
1. Check Railway logs (Deployments tab)
2. Use `/debug_env` to verify variables are set
3. Use `/debug_breweries` to verify database connection
4. Review this guide's troubleshooting section
