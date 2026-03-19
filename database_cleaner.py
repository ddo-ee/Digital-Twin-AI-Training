import sqlite3
from datetime import datetime, timedelta

def cleanup_database(db_path):
    try:
        # 1. Connect to your database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()


        # 3. Execute the delete command
        # cursor.execute("DELETE FROM zone_logs")
        # rows_deleted = cursor.rowcount
        
        # 4. CRITICAL: Reclaim disk space
        # Deleting rows doesn't shrink the file size until you VACUUM
        print("Reclaiming disk space (this may take a moment)...")
        cursor.execute("VACUUM")

        conn.commit()
        conn.close()
        # print(f"Success! Deleted {rows_deleted} rows and shrunk the database file.")

    except Exception as e:
        print(f"An error occurred: {e}")

# Run the function
cleanup_database('campus_security.db')