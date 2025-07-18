import os
import sys
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from werkzeug.security import check_password_hash, generate_password_hash

# --- KONFIGURASI APLIKASI ---
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')

# --- SCRIPT BANTU UNTUK MEMBUAT HASH PASSWORD ---
if '--generate-hash' in sys.argv:
    try:
        password_to_hash = sys.argv[sys.argv.index('--generate-hash') + 1]
        print(f"\nPassword: {password_to_hash}")
        print(f"Hash: {generate_password_hash(password_to_hash)}\n")
    except IndexError:
        print("Usage: python app.py --generate-hash <password>")
    sys.exit(0)

# --- KONEKSI DATABASE POSTGRESQL ---
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError("FATAL: DATABASE_URL tidak ditemukan di environment variables.")
engine = create_engine(DATABASE_URL)

# --- HALAMAN & LOGIKA APLIKASI ---

@app.route('/', methods=['GET', 'POST'])
def login():
    if 'username' in session:
        return redirect(url_for('flags'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        with engine.connect() as conn:
            result = conn.execute(text("SELECT * FROM users WHERE username = :username"), {"username": username}).fetchone()
        
        if result and check_password_hash(result._mapping['password_hash'], password):
            user_data = result._mapping
            session.clear()
            session['username'] = user_data['username']
            session['name'] = user_data['name']
            session['role'] = user_data['role']
            
            with engine.connect() as conn:
                conn.execute(text("""
                    INSERT INTO leaderboard (username, name) VALUES (:username, :name)
                    ON CONFLICT (username) DO NOTHING
                """), {"username": session['username'], "name": session['name']})
                conn.commit()
            return redirect(url_for('flags'))
            
        return render_template('login.html', error='User ID atau Access Key salah.')
    return render_template('login.html')

@app.route('/flags')
def flags():
    if 'username' not in session: return redirect(url_for('login'))
    
    with engine.connect() as conn:
        challenges_result = conn.execute(text("SELECT id, title, points, difficulty FROM challenges ORDER BY id")).fetchall()
        challenges = [row._mapping for row in challenges_result]
        
        result = conn.execute(text("SELECT answers FROM leaderboard WHERE username = :username"), 
                              {"username": session['username']}).fetchone()
    
    solved_flags = result[0].keys() if result and result[0] else []

    return render_template('flags.html', 
                           name=session.get('name'), 
                           challenges=challenges,
                           solved_flags=list(solved_flags),
                           role=session.get('role'))

# (Sisa rute aplikasi lainnya akan mengikuti pola yang sama)
# ...

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))