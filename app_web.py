import os
from flask import Flask, render_template, jsonify, request
from database_manager import MarketDatabase

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')

app = Flask(__name__, template_folder=TEMPLATE_DIR)
db = MarketDatabase()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/get_cart/<customer_id>')
def get_cart(customer_id):
    customer_id = customer_id.replace(" ", "_")

    sepet_df = db.cursor.execute(
        "SELECT product_name, quantity, total_price FROM ActiveCarts WHERE customer_id = ?",
        (customer_id,)
    ).fetchall()

    if not sepet_df:
        return jsonify({"status": "empty", "items": [], "total": 0})

    items = [{"name": row[0], "quantity": row[1], "price": row[2]} for row in sepet_df]
    total = sum(row[2] for row in sepet_df)

    return jsonify({"status": "active", "items": items, "total": total})

@app.route('/api/checkout', methods=['POST'])
def checkout():
    data = request.json
    customer_id = data.get('customer_id')

    if customer_id:
        customer_id = customer_id.replace(" ", "_")
        toplam, _ = db.checkout_customer(customer_id)
        return jsonify({"status": "success", "paid_amount": toplam})

    return jsonify({"status": "error"}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5050, debug=True)