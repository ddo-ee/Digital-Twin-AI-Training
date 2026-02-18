import sqlite3

DB_NAME = "campus_cctv.db"

def init_db():
    """Creates the database table if it doesn't exist."""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS cameras 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      name TEXT, 
                      url TEXT, 
                      camera_group TEXT)''')
        conn.commit()

def add_camera(name, url, group="General"):
    """Adds a new camera to the database."""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO cameras (name, url, camera_group) VALUES (?, ?, ?)", 
                  (name, url, group))
        conn.commit()

def get_all_cameras():
    """Returns a list of all cameras."""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM cameras")
        return c.fetchall()

def delete_camera(camera_id):
    """Removes a camera by ID."""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM cameras WHERE id=?", (camera_id,))
        conn.commit()

# Run initialization immediately when imported
init_db()