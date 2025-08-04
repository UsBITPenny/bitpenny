import os
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_cors import CORS
import json
import hashlib
import time
import requests

# Load environment variables from .env file
load_dotenv()

# Get secrets from environment variables
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
SECRET_KEY = os.getenv("SECRET_KEY")

app = Flask(__name__)
CORS(app)  # Allow frontend JS to access the API
app.secret_key = SECRET_KEY  # use secret key from env

# Example wallet address (replace with real one later)
wallet_address = "BITP123456789EXAMPLE"

# Dummy blockchain data (simulate transactions)
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

# --- Helper functions ---
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

# Homepage route (optional: load dashboard directly)
@app.route('/')
def index():
    return render_template("wallet.html")  # Make sure templates/wallet.html exists

# Wallet route - returns current wallet address
@app.route('/wallet', methods=['GET'])
def get_wallet():
    return jsonify({'address': wallet_address}), 200

# Balance route - returns total balance for address
@app.route('/balance/<address>', methods=['GET'])
def get_balance(address):
    balance = calculate_balance(address)
    return jsonify({'balance': balance}), 200

# Transactions route - returns all transactions for address
@app.route('/transactions/<address>', methods=['GET'])
def get_transactions(address):
    txs = get_transactions_for_wallet(address)
    return jsonify({'transactions': txs}), 200

# Example route: get blockchain (optional)
@app.route('/chain', methods=['GET'])
def get_chain():
    return jsonify({'chain': blockchain, 'length': len(blockchain)})

if __name__ == '__main__':
    app.run(debug=True)
