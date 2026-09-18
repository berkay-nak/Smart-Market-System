import sqlite3
from datetime import datetime


class MarketDatabase:
    def __init__(self, db_name="smart_market.db"):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS Products (
                product_id INTEGER PRIMARY KEY AUTOINCREMENT,
                shelf_name TEXT UNIQUE,
                product_name TEXT,
                current_stock INTEGER,
                max_stock INTEGER
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS Transactions (
                transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT,
                shelf_name TEXT,
                action_type TEXT,
                quantity INTEGER,
                timestamp DATETIME
            )
        """)
        self.conn.commit()

    def add_initial_products(self):
        products = [
            ("RAF_1", "Kağıt Havlu", 2, 5),
            ("RAF_2", "Su Şişesi", 4, 5),
            ("RAF_3", "Cips", 15, 15)
        ]
        try:
            self.cursor.executemany("""
                INSERT INTO Products (shelf_name, product_name, current_stock, max_stock) 
                VALUES (?, ?, ?, ?)
            """, products)
            self.conn.commit()
            print("[BİLGİ] Başlangıç stokları eklendi.")
        except sqlite3.IntegrityError:
            pass  # Zaten ekliyse sessizce geç

    # --- YENİ EKLENEN ANA FONKSİYON ---
    def process_transaction(self, shelf_name, customer_id, action_type, quantity=1):
        """
        Kamera ve sensör bir olayı onayladığında bu fonksiyon çağrılacak.
        """
        # 1. İşlemi (Log) Transactions tablosuna kaydet
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute("""
            INSERT INTO Transactions (customer_id, shelf_name, action_type, quantity, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (customer_id, shelf_name, action_type, quantity, timestamp))

        # 2. Stok Miktarını Güncelle (Products Tablosu)
        if action_type == "PICKUP":
            self.cursor.execute("""
                UPDATE Products SET current_stock = current_stock - ? WHERE shelf_name = ?
            """, (quantity, shelf_name))
            print(f">>> BAŞARILI: {customer_id}, {shelf_name}'den {quantity} adet ürün ALDI. Stok düşüldü.")

        elif action_type == "PUTBACK":
            self.cursor.execute("""
                UPDATE Products SET current_stock = current_stock + ? WHERE shelf_name = ?
            """, (quantity, shelf_name))
            print(f">>> BAŞARILI: {customer_id}, {shelf_name}'e {quantity} adet ürün BIRAKTI. Stok artırıldı.")

        # Değişiklikleri kaydet
        self.conn.commit()


# --- SİMÜLASYON / TEST BÖLÜMÜ ---
if __name__ == "__main__":
    # 1. Veritabanını başlat
    db = MarketDatabase()
    db.add_initial_products()
    print("[SİSTEM] Veritabanı başarıyla oluşturuldu ve başlangıç stokları hazır.")