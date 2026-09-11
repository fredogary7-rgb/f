# Barka Énergie — Inscription & Connexion

Application web **Flask** + base de données **PostgreSQL (Neon)**.

## Fonctionnalités
- **Inscription** : logo, nom complet, téléphone, **pays** (Togo, Côte d'Ivoire, Burkina Faso), mot de passe, **code de parrainage**.
- **Connexion** : par téléphone + mot de passe.
- **Tableau de bord** : affiche les infos du compte et le code de parrainage personnel.
- Mots de passe **hachés** (werkzeug), codes de parrainage **uniques** et générés automatiquement.

## Structure
```
├── app.py                  # Backend Flask (routes + base de données)
├── requirements.txt        # Dépendances Python
├── .env                    # DATABASE_URL + SECRET_KEY (non commité)
├── templates/              # Pages HTML (Jinja2)
│   ├── base.html
│   ├── register.html       # Inscription
│   ├── login.html          # Connexion
│   └── dashboard.html      # Tableau de bord
└── static/
    ├── f.jpg               # Logo
    └── css/style.css       # Thème néon Barka Énergie
```

## Installation
```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt
```

Configurez le fichier `.env` avec :
```
DATABASE_URL=postgresql://...
SECRET_KEY=...
```

## Lancer l'application
```bash
python app.py
```
Puis ouvrez **http://127.0.0.1:5000**

## Routes
| URL | Description |
|-----|-------------|
| `/` | Redirige vers la connexion ou le tableau de bord |
| `/inscription` | Création de compte |
| `/connexion` | Connexion |
| `/dashboard` | Tableau de bord (protégé) |
| `/deconnexion` | Déconnexion |

## Sécurité
- Le fichier `.env` contient des identifiants sensibles : ne le committez **jamais** (il est dans `.gitignore`).
- Pensez à changer le mot de passe de la base Neon si vous l'avez partagé.
