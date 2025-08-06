import os
from flask import Flask, jsonify, render_template
from flask_cors import CORS

# === Flask App Setup ===
app = Flask(__name__)
CORS(app)

# === Simulated Blockchain / Exchange Data ===
wallet_address = "BITP123456789EXAMPLE"

blockchain = [
    {
        "sender": "network",
        "recipient": wallet_address,
        "amount": 500000000,
        "timestamp": "2025-08-01 12:00:00"
    },
    {
        "sender": wallet_address,
        "recipient": "BITPxyz1234",
        "amount": 10000000,
        "timestamp": "2025-08-02 13:45:00"
    }
]

# === Helper Functions ===
def calculate_balance(address):
    total = 0
    for tx in blockchain:
        if tx["recipient"] == address:
            total += tx["amount"]
        elif tx["sender"] == address:
            total -= tx["amount"]
    return total

def get_transactions_for_wallet(address):
    return [
        tx for tx in blockchain
        if tx["sender"] == address or tx["recipient"] == address
    ]

# === Routes ===

# Serve the exchange dashboard
@app.route('/')
def index():
    return render_template("wallet.html")  # Must be in templates/wallet.html

# Return wallet address
@app.route('/wallet', methods=['GET'])
def get_wallet():
    return jsonify({'address': wallet_address}), 200

# Return balance for given address
@app.route('/balance/<address>', methods=['GET'])
def get_balance(address):
    balance = calculate_balance(address)
    return jsonify({'balance': balance}), 200

# Return all transactions for a wallet
@app.route('/transactions/<address>', methods=['GET'])
def get_transactions(address):
    txs = get_transactions_for_wallet(address)
    return jsonify({'transactions': txs}), 200

# Optional: return the blockchain itself
@app.route('/chain', methods=['GET'])
def get_chain():
    return jsonify({'chain': blockchain, 'length': len(blockchain)})

# === Run Server ===
if __name__ == '__main__':
    # Ensures it works locally and on Render
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=True)
