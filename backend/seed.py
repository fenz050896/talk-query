"""Create sample SQLite database with demo tables and data."""
import os
import sys
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "talkquery.db")

def seed():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    # Remove old db if exists
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Create tables
    cursor.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL DEFAULT 'user',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_login TEXT
        );

        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    # Seed users
    users = [
        ("Alice", "alice@example.com", "admin", 1, "2025-01-15", "2026-05-26 09:30:00"),
        ("Bob", "bob@example.com", "user", 1, "2025-02-20", "2026-05-25 14:20:00"),
        ("Charlie", "charlie@example.com", "user", 1, "2025-03-10", "2026-05-26 08:00:00"),
        ("Diana", "diana@example.com", "user", 0, "2025-04-01", "2026-04-10 11:00:00"),
        ("Eve", "eve@example.com", "moderator", 1, "2025-05-15", "2026-05-26 10:45:00"),
        ("Frank", "frank@example.com", "user", 1, "2025-06-01", "2026-05-24 16:30:00"),
        ("Grace", "grace@example.com", "user", 1, "2025-07-20", "2026-05-26 07:15:00"),
        ("Henry", "henry@example.com", "user", 0, "2025-08-10", "2026-03-01 12:00:00"),
        ("Ivy", "ivy@example.com", "user", 1, "2025-09-05", "2026-05-25 22:00:00"),
        ("Jack", "jack@example.com", "admin", 1, "2025-10-01", "2026-05-26 09:00:00"),
    ]
    cursor.executemany(
        "INSERT INTO users (name, email, role, active, created_at, last_login) VALUES (?, ?, ?, ?, ?, ?)",
        users,
    )

    # Seed products
    products = [
        ("Laptop Pro", "Electronics", 15000000, 25),
        ("Mouse Wireless", "Electronics", 350000, 100),
        ("Keyboard Mechanical", "Electronics", 1200000, 45),
        ("Monitor 27 inch", "Electronics", 4500000, 15),
        ("Desk Chair", "Furniture", 2500000, 8),
        ("Standing Desk", "Furniture", 7500000, 5),
        ("Notebook A5", "Stationery", 25000, 200),
        ("Pen Set", "Stationery", 50000, 150),
        ("Water Bottle", "Accessories", 75000, 80),
        ("Backpack", "Accessories", 450000, 30),
    ]
    cursor.executemany(
        "INSERT INTO products (name, category, price, stock) VALUES (?, ?, ?, ?)",
        products,
    )

    # Seed orders
    orders = [
        (1, "Laptop Pro", 1, 15000000, "completed"),
        (1, "Mouse Wireless", 2, 700000, "completed"),
        (2, "Keyboard Mechanical", 1, 1200000, "shipped"),
        (3, "Monitor 27 inch", 1, 4500000, "completed"),
        (3, "Desk Chair", 1, 2500000, "completed"),
        (5, "Notebook A5", 5, 125000, "completed"),
        (5, "Pen Set", 2, 100000, "pending"),
        (6, "Standing Desk", 1, 7500000, "shipped"),
        (7, "Water Bottle", 3, 225000, "completed"),
        (7, "Backpack", 1, 450000, "completed"),
        (9, "Laptop Pro", 1, 15000000, "shipped"),
        (9, "Mouse Wireless", 1, 350000, "completed"),
        (10, "Keyboard Mechanical", 2, 2400000, "pending"),
        (10, "Monitor 27 inch", 1, 4500000, "pending"),
        # some more orders
        (1, "Notebook A5", 3, 75000, "completed"),
        (2, "Water Bottle", 1, 75000, "completed"),
        (3, "Pen Set", 1, 50000, "completed"),
        (5, "Backpack", 1, 450000, "shipped"),
        (6, "Desk Chair", 1, 2500000, "completed"),
        (7, "Laptop Pro", 1, 15000000, "completed"),
    ]
    cursor.executemany(
        "INSERT INTO orders (user_id, product, quantity, price, status) VALUES (?, ?, ?, ?, ?)",
        orders,
    )

    conn.commit()
    conn.close()

    # Print summary
    print(f"Database created at: {DB_PATH}")
    print("Tables: users (10 rows), products (10 rows), orders (20 rows)")


if __name__ == "__main__":
    seed()
