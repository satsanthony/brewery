#!/usr/bin/env python
"""
Railway PostgreSQL Setup Script
Helps configure the brewery app with Railway database
"""

import os
import sys
from pathlib import Path

def setup_railway_database():
    """Interactive setup for Railway database configuration."""

    print("\n" + "=" * 70)
    print("BREWERY APP - RAILWAY DATABASE SETUP")
    print("=" * 70)

    env_file = Path('.env')

    print("\nStep 1: Get your DATABASE_URL from Railway")
    print("-" * 70)
    print("1. Go to your Railway project dashboard")
    print("2. Click on PostgreSQL service")
    print("3. Go to 'Connect' tab")
    print("4. Copy the DATABASE_URL (starts with 'postgresql://')")
    print()

    database_url = input("Paste your DATABASE_URL here: ").strip()

    if not database_url.startswith('postgresql://'):
        print("\n[ERROR] Invalid DATABASE_URL format!")
        print("Expected format: postgresql://user:password@host:port/dbname")
        return False

    print("\n[OK] DATABASE_URL format valid")

    # Read existing .env
    env_content = ""
    if env_file.exists():
        with open(env_file, 'r') as f:
            env_content = f.read()

    # Update or add DATABASE_URL
    if 'DATABASE_URL=' in env_content:
        # Replace existing
        lines = env_content.split('\n')
        new_lines = []
        for line in lines:
            if line.startswith('DATABASE_URL='):
                new_lines.append(f'DATABASE_URL={database_url}')
            else:
                new_lines.append(line)
        env_content = '\n'.join(new_lines)
    else:
        # Add new
        env_content += f'\nDATABASE_URL={database_url}\n'

    # Write back
    with open(env_file, 'w') as f:
        f.write(env_content)

    print("\n[OK] Updated .env file with DATABASE_URL")

    print("\nStep 2: Initialize Database")
    print("-" * 70)
    print("The app will automatically:")
    print("1. Create brewery_info table if it doesn't exist")
    print("2. Sync brewery.csv data to the database")
    print("3. Track CSV changes for future syncs")

    print("\nStep 3: Verify Connection")
    print("-" * 70)
    print("Once you start the app, verify with:")
    print("  curl http://localhost:5000/db_status")
    print("\nExpected response:")
    print('  {"status":"connected","brewery_count":5}')

    print("\nStep 4: Manual Sync (if needed)")
    print("-" * 70)
    print("If you update the CSV, trigger sync with:")
    print("  curl -X POST http://localhost:5000/sync_csv")

    print("\n" + "=" * 70)
    print("SETUP COMPLETE!")
    print("=" * 70)
    print("\nNow restart the app:")
    print("  python brewery.py")
    print()

    return True

if __name__ == '__main__':
    success = setup_railway_database()
    sys.exit(0 if success else 1)
