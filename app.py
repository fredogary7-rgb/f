import os
import secrets
import time
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, timedelta

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash)
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL est manquant : définissez la variable d'environnement DATABASE_URL (voir .env.example).")

ALLOWED_COUNTRIES = ["Burkina Faso"]

MIN_DEPOSIT = 3500
MIN_WITHDRAWAL = 1000
REFERRAL_LV1 = 0.15
REFERRAL_LV2 = 0.02
REFERRAL_LV3 = 0.01

PRODUCTS = [
    {"name": "Barka Énergie 1", "prix": "3,500", "journalier": "600", "total": "126,000", "jours": 210, "image": "f1.jpg"},
    {"name": "Barka Énergie 2", "prix": "8,000", "journalier": "1,400", "total": "294,000", "jours": 210, "image": "f2.jpg"},
    {"name": "Barka Énergie 3", "prix": "20,000", "journalier": "3,500", "total": "735,000", "jours": 210, "image": "f3.jpg"},
    {"name": "Barka Énergie 4", "prix": "50,000", "journalier": "9,000", "total": "1,890,000", "jours": 210, "image": "f4.jpg"},
    {"name": "Barka Énergie 5", "prix": "80,000", "journalier": "15,000", "total": "3,150,000", "jours": 210, "image": "f5.jpg"},
    {"name": "Barka Énergie 6", "prix": "120,000", "journalier": "23,000", "total": "4,830,000", "jours": 210, "image": "f1.jpg"},
    {"name": "Barka Énergie 7", "prix": "200,000", "journalier": "40,000", "total": "8,400,000", "jours": 210, "image": "f2.jpg"},
    {"name": "Barka Énergie 8", "prix": "300,000", "journalier": "60,000", "total": "12,600,000", "jours": 210, "image": "f3.jpg"},
]


@contextmanager
def get_db():
    """Contexte de connexion à la base (commit/rollback/close)."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Crée les tables et applique les migrations (avec retry en cas de conflit concurrent)."""
    for attempt in range(3):
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            id SERIAL PRIMARY KEY,
                            full_name VARCHAR(100) NOT NULL,
                            phone VARCHAR(20) UNIQUE NOT NULL,
                            password_hash VARCHAR(255) NOT NULL,
                            country VARCHAR(50) NOT NULL,
                            referral_code VARCHAR(20) UNIQUE NOT NULL,
                            referred_by VARCHAR(20),
                            balance NUMERIC DEFAULT 0,
                            withdrawal_number VARCHAR(100),
                            withdrawal_updated_at TIMESTAMP,
                            created_at TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone)")
                    # Migration des tables existantes
                    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS balance NUMERIC DEFAULT 0")
                    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS withdrawal_number VARCHAR(100)")
                    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS withdrawal_updated_at TIMESTAMP")
                    # Table des souscriptions (investissements)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS subscriptions (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER NOT NULL,
                            product_name VARCHAR(100) NOT NULL,
                            price NUMERIC NOT NULL,
                            daily_income NUMERIC NOT NULL,
                            total_income NUMERIC NOT NULL,
                            days INTEGER NOT NULL DEFAULT 210,
                            start_date TIMESTAMP DEFAULT NOW(),
                            credited_amount NUMERIC DEFAULT 0,
                            created_at TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id)")
                    # Colonne administrateur
                    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE")
                    # Table des dépôts (rechargements à valider par l'admin)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS deposits (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER NOT NULL,
                            amount NUMERIC NOT NULL,
                            phone VARCHAR(30),
                            status VARCHAR(20) NOT NULL DEFAULT 'pending',
                            created_at TIMESTAMP DEFAULT NOW(),
                            reviewed_at TIMESTAMP
                        )
                    """)
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_deposits_user ON deposits(user_id)")
            return
        except psycopg2.errors.DeadlockDetected:
            if attempt == 2:
                raise
            time.sleep(0.3 * (attempt + 1))


def generate_referral_code():
    """Génère un code de parrainage unique (ex: SUP1A2B3C)."""
    while True:
        code = "SUP" + secrets.token_hex(3).upper()
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM users WHERE referral_code = %s", (code,))
                if cur.fetchone() is None:
                    return code


def credit_income(user_id):
    """Crédite les revenus journaliers accumulés depuis le dernier crédit."""
    now = datetime.now()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, daily_income, days, start_date, credited_amount FROM subscriptions WHERE user_id = %s", (user_id,))
            subs = cur.fetchall()
            for s in subs:
                sid, daily, days, start, credited_amount = s
                elapsed_days = max(0, (now - start).days)
                earned = min(elapsed_days, days) * float(daily)
                to_credit = earned - float(credited_amount or 0)
                if to_credit > 0:
                    cur.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (to_credit, user_id))
                    cur.execute("UPDATE subscriptions SET credited_amount = %s WHERE id = %s", (earned, sid))


def credit_referral_commissions(user_id, amount):
    """Crédite les commissions de parrainage sur 3 niveaux (Lv1 15%, Lv2 2%, Lv3 1%)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT referred_by FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if not row or not row[0]:
                return
            # Niveau 1 (parrain direct)
            cur.execute("SELECT id, referred_by FROM users WHERE referral_code = %s", (row[0],))
            lv1 = cur.fetchone()
            if not lv1:
                return
            cur.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (amount * REFERRAL_LV1, lv1[0]))
            # Niveau 2
            if lv1[1]:
                cur.execute("SELECT id, referred_by FROM users WHERE referral_code = %s", (lv1[1],))
                lv2 = cur.fetchone()
                if lv2:
                    cur.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (amount * REFERRAL_LV2, lv2[0]))
                    # Niveau 3
                    if lv2[1]:
                        cur.execute("SELECT id FROM users WHERE referral_code = %s", (lv2[1],))
                        lv3 = cur.fetchone()
                        if lv3:
                            cur.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (amount * REFERRAL_LV3, lv3[0]))


def login_required(f):
    """Décorateur : exige une session connectée."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Veuillez vous connecter.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    """Décorateur : exige un compte administrateur."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Veuillez vous connecter.", "error")
            return redirect(url_for("login"))
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT is_admin FROM users WHERE id = %s", (session["user_id"],))
                row = cur.fetchone()
        if not row or not row[0]:
            flash("Accès réservé à l'administrateur.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return wrapper


@app.template_filter("fmt")
def fmt(n):
    return f"{int(float(n)):,}"


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/produit")
def produit():
    return render_template("produit.html", products=PRODUCTS)


@app.route("/produit/confirmation/<name>")
def produit_confirmation(name):
    product = next((p for p in PRODUCTS if p["name"] == name), None)
    if not product:
        return redirect(url_for("produit"))
    return render_template("confirmation.html", product=product)


@app.route("/portefeuille")
@login_required
def portefeuille():
    credit_income(session["user_id"])
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
            user = cur.fetchone()
            cur.execute("SELECT COALESCE(SUM(price), 0) AS total FROM subscriptions WHERE user_id = %s", (session["user_id"],))
            invested = cur.fetchone()["total"]

    if not user:
        session.clear()
        return redirect(url_for("login"))

    balance = float(user["balance"] or 0)
    balance_str = f"{int(balance):,}"
    invested_str = f"{int(float(invested or 0)):,}"
    return render_template("portefeuille.html", user=user, balance_str=balance_str, invested_str=invested_str)


@app.route("/retrait", methods=["GET", "POST"])
@login_required
def retrait():
    credit_income(session["user_id"])
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
            user = cur.fetchone()
            cur.execute("SELECT COUNT(*) AS cnt FROM subscriptions WHERE user_id = %s", (session["user_id"],))
            sub_count = cur.fetchone()["cnt"]

    if not user:
        session.clear()
        return redirect(url_for("login"))

    has_subscription = int(sub_count or 0) > 0

    # Statut du retrait (délai de 24h après modification du numéro)
    now = datetime.now()
    can_withdraw = False
    remaining_hours = 0
    if user.get("withdrawal_number"):
        updated = user.get("withdrawal_updated_at")
        if updated is None:
            can_withdraw = True
        else:
            elapsed = now - updated
            if elapsed >= timedelta(hours=24):
                can_withdraw = True
            else:
                remaining = timedelta(hours=24) - elapsed
                remaining_hours = int(remaining.total_seconds() // 3600) + 1

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "update_card":
            withdrawal_number = request.form.get("withdrawal_number", "").strip()
            if len(withdrawal_number) < 6:
                flash("Veuillez saisir un numéro de retrait valide.", "error")
            else:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE users
                            SET withdrawal_number = %s, withdrawal_updated_at = NOW()
                            WHERE id = %s
                        """, (withdrawal_number, session["user_id"]))
                flash("Numéro de retrait mis à jour. Un délai de 24h est requis avant tout retrait.", "success")
            return redirect(url_for("retrait"))

        if action == "withdraw":
            try:
                amount = float(request.form.get("amount", ""))
            except (ValueError, TypeError):
                amount = 0.0

            if amount <= 0:
                flash("Veuillez saisir un montant valide.", "error")
            elif amount < MIN_WITHDRAWAL:
                flash(f"Le retrait minimum est de FCFA {MIN_WITHDRAWAL:,}.", "error")
            elif not has_subscription:
                flash("Investissez d'abord dans un produit pour pouvoir retirer.", "error")
            elif not user.get("withdrawal_number"):
                flash("Configurez d'abord votre numéro de retrait.", "error")
            elif not can_withdraw:
                flash("Délai de 24h en cours avant tout retrait.", "error")
            elif amount > float(user["balance"] or 0):
                flash("Solde insuffisant.", "error")
            else:
                operator = request.form.get("operator", "").strip()
                number = request.form.get("number", "").strip() or user["withdrawal_number"]
                flash(f"Retrait de FCFA {int(amount):,} via {operator or 'votre opérateur'} ({number}) en attente.", "success")
            return redirect(url_for("retrait"))

    balance = float(user["balance"] or 0)
    balance_str = f"{int(balance):,}"
    return render_template(
        "retrait.html",
        user=user,
        balance_str=balance_str,
        can_withdraw=can_withdraw,
        remaining_hours=remaining_hours,
        has_subscription=has_subscription,
    )


@app.route("/retrait/operateur")
@login_required
def retrait_operateur():
    credit_income(session["user_id"])
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
            user = cur.fetchone()
            cur.execute("SELECT COUNT(*) AS cnt FROM subscriptions WHERE user_id = %s", (session["user_id"],))
            sub_count = cur.fetchone()["cnt"]

    if not user:
        session.clear()
        return redirect(url_for("login"))

    if int(sub_count or 0) == 0:
        flash("Investissez d'abord dans un produit avant de pouvoir retirer.", "error")
        return redirect(url_for("retrait"))

    balance_str = f"{int(float(user['balance'] or 0)):,}"
    return render_template("operateur.html", user=user, balance_str=balance_str, min_withdrawal=MIN_WITHDRAWAL)


@app.route("/souscrire", methods=["POST"])
@login_required
def souscrire():
    credit_income(session["user_id"])
    product_name = request.form.get("product_name", "").strip()
    product = next((p for p in PRODUCTS if p["name"] == product_name), None)
    if not product:
        flash("Produit introuvable.", "error")
        return redirect(url_for("produit"))

    price = int(product["prix"].replace(",", ""))

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT balance FROM users WHERE id = %s", (session["user_id"],))
            user = cur.fetchone()

    if not user:
        session.clear()
        return redirect(url_for("login"))

    if float(user["balance"] or 0) < price:
        flash("Solde insuffisant. Rechargez d'abord votre portefeuille.", "error")
        return redirect(url_for("portefeuille"))

    daily = int(product["journalier"].replace(",", ""))
    total = int(product["total"].replace(",", ""))
    days = product["jours"]

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET balance = balance - %s WHERE id = %s", (price, session["user_id"]))
            cur.execute("""
                INSERT INTO subscriptions (user_id, product_name, price, daily_income, total_income, days)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (session["user_id"], product_name, price, daily, total, days))

    # Commissions de parrainage (Lv1 15%, Lv2 2%, Lv3 1%)
    credit_referral_commissions(session["user_id"], price)

    flash("Souscription réussie ! Vos revenus journaliers démarrent.", "success")
    return redirect(url_for("portefeuille"))


@app.route("/recharger", methods=["GET", "POST"])
@login_required
def recharger():
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount", ""))
        except (ValueError, TypeError):
            amount = 0.0
        phone = request.form.get("phone", "").strip()
        if amount <= 0:
            flash("Veuillez saisir un montant valide.", "error")
            return redirect(url_for("recharger"))
        if amount < MIN_DEPOSIT:
            flash(f"Le dépôt minimum est de FCFA {MIN_DEPOSIT:,}.", "error")
            return redirect(url_for("recharger"))
        if not phone:
            flash("Veuillez saisir votre numéro de paiement.", "error")
            return redirect(url_for("recharger"))
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO deposits (user_id, amount, phone, status)
                    VALUES (%s, %s, %s, 'pending')
                """, (session["user_id"], amount, phone))
        flash("Dépôt soumis. En attente de validation par l'administrateur.", "success")
        return redirect(url_for("portefeuille"))
    return render_template("recharger.html", min_deposit=MIN_DEPOSIT)


@app.route("/admin")
@admin_required
def admin():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT d.id, d.amount, d.phone, d.status, d.created_at, u.full_name
                FROM deposits d JOIN users u ON u.id = d.user_id
                ORDER BY (d.status = 'pending') DESC, d.created_at DESC
            """)
            deposits = cur.fetchall()
    return render_template("admin.html", deposits=deposits)


@app.route("/admin/review", methods=["POST"])
@admin_required
def admin_review():
    deposit_id = request.form.get("deposit_id")
    action = request.form.get("action")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM deposits WHERE id = %s", (deposit_id,))
            dep = cur.fetchone()

    if dep and dep["status"] == "pending":
        if action == "approve":
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (dep["amount"], dep["user_id"]))
                    cur.execute("UPDATE deposits SET status = 'approved', reviewed_at = NOW() WHERE id = %s", (deposit_id,))
            flash("Dépôt approuvé et crédité.", "success")
        elif action == "reject":
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE deposits SET status = 'rejected', reviewed_at = NOW() WHERE id = %s", (deposit_id,))
            flash("Dépôt rejeté.", "success")
    return redirect(url_for("admin"))


@app.route("/equipe")
@login_required
def equipe():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
            user = cur.fetchone()
            if not user:
                session.clear()
                return redirect(url_for("login"))

            cur.execute("SELECT id, full_name, phone, referral_code, created_at FROM users WHERE referred_by = %s ORDER BY created_at DESC", (user["referral_code"],))
            lv1 = cur.fetchall()

            lv2 = []
            lv3 = []
            if lv1:
                lv1_codes = [u["referral_code"] for u in lv1]
                ph = ",".join(["%s"] * len(lv1_codes))
                cur.execute(f"SELECT id, full_name, phone, referral_code FROM users WHERE referred_by IN ({ph})", tuple(lv1_codes))
                lv2 = cur.fetchall()
                if lv2:
                    lv2_codes = [u["referral_code"] for u in lv2]
                    ph2 = ",".join(["%s"] * len(lv2_codes))
                    cur.execute(f"SELECT id, full_name, phone FROM users WHERE referred_by IN ({ph2})", tuple(lv2_codes))
                    lv3 = cur.fetchall()

            def level_total(rows):
                if not rows:
                    return 0.0
                ids = [u["id"] for u in rows]
                ph = ",".join(["%s"] * len(ids))
                cur.execute(f"SELECT COALESCE(SUM(price),0) AS t FROM subscriptions WHERE user_id IN ({ph})", tuple(ids))
                return float(cur.fetchone()["t"])

            total_commission = level_total(lv1) * REFERRAL_LV1 + level_total(lv2) * REFERRAL_LV2 + level_total(lv3) * REFERRAL_LV3

    return render_template(
        "equipe.html",
        user=user,
        lv1=lv1,
        total_commission=f"{int(total_commission):,}",
        lv1_rate=int(REFERRAL_LV1 * 100),
        lv2_rate=int(REFERRAL_LV2 * 100),
        lv3_rate=int(REFERRAL_LV3 * 100),
    )


@app.route("/partager")
@login_required
def partager():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT referral_code FROM users WHERE id = %s", (session["user_id"],))
            user = cur.fetchone()
    if not user:
        session.clear()
        return redirect(url_for("login"))
    return render_template("partager.html", referral_code=user["referral_code"])


@app.route("/echange")
@login_required
def echange():
    return render_template("echange.html")


@app.route("/politique")
def politique():
    return render_template("politique.html")


@app.route("/inscription", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        country = request.form.get("country", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        referral = request.form.get("referral_code", "").strip().upper()

        errors = []
        if not full_name:
            errors.append("Le nom complet est requis.")
        if not phone:
            errors.append("Le numéro de téléphone est requis.")
        if country not in ALLOWED_COUNTRIES:
            errors.append("Veuillez choisir un pays valide.")
        if len(password) < 6:
            errors.append("Le mot de passe doit contenir au moins 6 caractères.")
        if password != confirm_password:
            errors.append("Les mots de passe ne correspondent pas.")

        if not errors:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM users WHERE phone = %s", (phone,))
                    if cur.fetchone():
                        errors.append("Ce numéro de téléphone est déjà enregistré.")

        referred_by = None
        if referral and not errors:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT referral_code FROM users WHERE UPPER(referral_code) = %s", (referral,))
                    row = cur.fetchone()
                    if row:
                        referred_by = row[0]
                    else:
                        errors.append("Le code de parrainage est invalide.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "register.html",
                countries=ALLOWED_COUNTRIES,
                full_name=full_name, phone=phone,
                country=country, referral=referral,
            )

        referral_code = generate_referral_code()
        password_hash = generate_password_hash(password)
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO users
                            (full_name, phone, password_hash, country, referral_code, referred_by)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (full_name, phone, password_hash, country,
                          referral_code, referred_by))
        except psycopg2.errors.UniqueViolation:
            # Double soumission / concurrence : le numéro existe déjà
            flash("Ce numéro de téléphone est déjà enregistré.", "error")
            return render_template(
                "register.html", countries=ALLOWED_COUNTRIES,
                full_name=full_name, phone=phone,
                country=country, referral=referral,
            )

        flash("Compte créé avec succès ! Vous pouvez maintenant vous connecter.", "success")
        return redirect(url_for("login"))

    referral_param = request.args.get("parrain", "").strip().upper()
    return render_template(
        "register.html", countries=ALLOWED_COUNTRIES,
        full_name="", phone="", country="", referral=referral_param,
    )


@app.route("/connexion", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        login_field = request.form.get("login", "").strip()
        password = request.form.get("password", "")

        if not login_field or not password:
            flash("Veuillez remplir tous les champs.", "error")
            return render_template("login.html", login_field=login_field)

        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE phone = %s", (login_field,))
                user = cur.fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("home"))

        flash("Identifiant ou mot de passe incorrect.", "error")
        return render_template("login.html", login_field=login_field)

    return render_template("login.html", login_field="")


@app.route("/dashboard")
@login_required
def dashboard():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
            user = cur.fetchone()

    if not user:
        session.clear()
        return redirect(url_for("login"))

    return render_template("dashboard.html", user=user)


@app.route("/deconnexion")
def logout():
    session.clear()
    flash("Vous êtes déconnecté.", "success")
    return redirect(url_for("login"))


# Création des tables au démarrage (fonctionne avec `python app.py` et `gunicorn app:app`)
init_db()

if __name__ == "__main__":
    # Mode debug uniquement si FLASK_DEBUG=1 (jamais en production)
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, host="0.0.0.0", port=5000)
