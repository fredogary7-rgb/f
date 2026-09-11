from app import app, init_db, get_db

init_db()
print("Table 'users' prete.")

# Nettoyage initial (au cas où un test précédent aurait laissé des données)
with get_db() as conn:
    cur = conn.cursor()
    cur.execute("DELETE FROM deposits WHERE user_id IN (SELECT id FROM users WHERE phone IN (%s, %s, %s))",
                ("+22890000001", "+22507000001", "+22670000001"))
    cur.execute("DELETE FROM subscriptions WHERE user_id IN (SELECT id FROM users WHERE phone IN (%s, %s, %s))",
                ("+22890000001", "+22507000001", "+22670000001"))
    cur.execute("DELETE FROM users WHERE phone IN (%s, %s, %s)",
                ("+22890000001", "+22507000001", "+22670000001"))

client = app.test_client()

# 1. Page d'inscription accessible
r = client.get("/inscription")
print("GET /inscription ->", r.status_code)
assert r.status_code == 200

# 2. Inscription utilisateur A (sans parrainage)
data_a = {
    "full_name": "Test Alpha",
    "phone": "+22890000001",
    "country": "Burkina Faso",
    "password": "secret123",
    "confirm_password": "secret123",
    "referral_code": "",
}
r = client.post("/inscription", data=data_a, follow_redirects=True)
print("POST /inscription (A) ->", r.status_code)
assert "Compte créé" in r.get_data(as_text=True)

with get_db() as conn:
    cur = conn.cursor()
    cur.execute("SELECT referral_code FROM users WHERE phone = %s", (data_a["phone"],))
    code_a = cur.fetchone()[0]
print("Code parrainage A =", code_a)

# 3. Inscription utilisateur B AVEC le code de parrainage de A
data_b = {
    "full_name": "Test Bravo",
    "phone": "+22507000001",
    "country": "Burkina Faso",
    "password": "secret123",
    "confirm_password": "secret123",
    "referral_code": code_a,
}
r = client.post("/inscription", data=data_b, follow_redirects=True)
print("POST /inscription (B) ->", r.status_code)
assert "Compte créé" in r.get_data(as_text=True)

with get_db() as conn:
    cur = conn.cursor()
    cur.execute("SELECT referred_by FROM users WHERE phone = %s", (data_b["phone"],))
    referred = cur.fetchone()[0]
print("B parraine par =", referred)
assert referred == code_a

# 4. Test code de parrainage invalide (telephone unique)
data_bad = {
    "full_name": "Test Charlie",
    "phone": "+22670000001",
    "country": "Burkina Faso",
    "password": "secret123",
    "confirm_password": "secret123",
    "referral_code": "SUPXXXXXX",
}
r = client.post("/inscription", data=data_bad)
assert "code de parrainage est invalide" in r.get_data(as_text=True)
print("Code parrainage invalide bien rejete.")

# 4b. Test numéro de téléphone déjà enregistré
r = client.post("/inscription", data=data_a)
assert "déjà enregistré" in r.get_data(as_text=True)
print("Numéro de téléphone déjà enregistré bien détecté.")

# 5. Connexion de A
client.get("/deconnexion")
r = client.post("/connexion", data={"login": data_a["phone"], "password": "secret123"}, follow_redirects=True)
html = r.get_data(as_text=True)
print("POST /connexion (A) ->", r.status_code)
assert "Bienvenue" in html  # redirigé vers l'accueil

r = client.get("/dashboard")
html = r.get_data(as_text=True)
print("Dashboard affiche le code de parrainage ->", "parrainage" in html.lower())
assert code_a in html

# 5b. Test retrait (numéro de retrait, délai 24h)
r = client.get("/retrait")
print("GET /retrait ->", r.status_code)
assert r.status_code == 200
assert "Solde" in r.get_data(as_text=True)

r = client.post("/retrait", data={"action": "update_card", "withdrawal_number": "22990000001"}, follow_redirects=True)
assert "Numéro de retrait mis à jour" in r.get_data(as_text=True)
print("Numéro de retrait configuré.")

# Pas de retrait sans avoir payé un produit
r = client.post("/retrait", data={"action": "withdraw", "amount": "1000"}, follow_redirects=True)
assert "Investissez d'abord" in r.get_data(as_text=True)
print("Retrait bloqué sans produit payé.")

# 5c. Test dépôt (en attente) + validation admin + souscription
r = client.post("/recharger", data={"amount": "100000", "phone": "22900000001"}, follow_redirects=True)
assert "attente" in r.get_data(as_text=True)
print("Dépôt soumis, en attente de validation.")

# Marquer A comme admin et approuver le dépôt
with get_db() as conn:
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_admin = TRUE WHERE phone = %s", (data_a["phone"],))
    cur.execute("SELECT id FROM deposits WHERE user_id = (SELECT id FROM users WHERE phone = %s) ORDER BY id DESC LIMIT 1", (data_a["phone"],))
    deposit_id = cur.fetchone()[0]

r = client.post("/admin/review", data={"deposit_id": str(deposit_id), "action": "approve"}, follow_redirects=True)
assert "approuvé" in r.get_data(as_text=True)
print("Dépôt approuvé par l'admin et crédité.")

r = client.post("/souscrire", data={"product_name": "Barka Énergie 1"}, follow_redirects=True)
assert "Souscription réussie" in r.get_data(as_text=True)
print("Souscription à Barka Énergie 1 réussie.")

r = client.get("/portefeuille")
html = r.get_data(as_text=True)
assert "96,500" in html
assert "Total investi" in html
print("Solde et investissement corrects.")

# 5d. Retrait avec produit payé mais délai 24h (numéro modifié récemment)
r = client.post("/retrait", data={"action": "withdraw", "amount": "1000", "operator": "Orange Money", "number": "22990000001"}, follow_redirects=True)
assert "Délai de 24h" in r.get_data(as_text=True)
print("Délai de 24h après modification bien appliqué.")

# 6. Nettoyage final
with get_db() as conn:
    cur = conn.cursor()
    cur.execute("DELETE FROM deposits WHERE user_id IN (SELECT id FROM users WHERE phone IN (%s, %s, %s))",
                (data_a["phone"], data_b["phone"], data_bad["phone"]))
    cur.execute("DELETE FROM subscriptions WHERE user_id IN (SELECT id FROM users WHERE phone IN (%s, %s, %s))",
                (data_a["phone"], data_b["phone"], data_bad["phone"]))
    cur.execute("DELETE FROM users WHERE phone IN (%s, %s, %s)",
                (data_a["phone"], data_b["phone"], data_bad["phone"]))
print("Nettoyage OK.")
print("TOUS LES TESTS PASSENT")

