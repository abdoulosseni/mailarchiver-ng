.PHONY: help install keys secret-key up down logs fmt test nonreg

help:
	@echo "make install - installation complète sur un nouvel environnement (1 commande)"
	@echo "make keys    - génère les secrets manquants (idempotent : ne réécrit jamais)"
	@echo "make up      - démarre la stack (docker compose)"
	@echo "make down    - arrête la stack"
	@echo "make logs     - suit les logs"
	@echo "make test    - exécute les tests unitaires (crypto)"
	@echo "make nonreg  - suite de non-régression E2E (stack en marche)"

# Installation full-auto sur un environnement vierge : .env + secrets + démarrage.
# Le reste (schéma, partitions, compte admin, bucket, index, cluster) s'amorce seul.
install:
	@[ -f .env ] && echo ".env : conservé" || { cp .env.example .env; echo ".env : créé depuis .env.example (DURCIR les secrets avant la prod)"; }
	@$(MAKE) --no-print-directory keys
	docker compose up -d --build
	@echo ""
	@echo "Installation terminée. IHM : http://localhost:8080  (admin / admin — à changer)."
	@echo "Santé : docker compose ps   ·   Non-régression : make nonreg"

# IDEMPOTENT : ne réécrit JAMAIS une clé existante. Régénérer master.key rendrait
# illisibles toutes les archives chiffrées ; régénérer la paire de signature
# invaliderait l'intégrité des archives existantes. Pour une rotation maîtrisée,
# supprimer manuellement le fichier concerné (et prévoir le re-chiffrement).
# Génération via openssl (portable : aucune dépendance Python/cryptography côté
# hôte). echo conditionné au succès → échec franc si une commande échoue.
keys:
	@mkdir -p secrets
	@[ -f secrets/master.key ] && echo "  master.key            : conservé" || { openssl rand -base64 32 > secrets/master.key && echo "  master.key            : généré"; }
	@[ -f secrets/signing_private.pem ] && echo "  paire de signature    : conservée" || { openssl genpkey -algorithm ed25519 -out secrets/signing_private.pem 2>/dev/null && openssl pkey -in secrets/signing_private.pem -pubout -out secrets/signing_public.pem 2>/dev/null && echo "  paire de signature    : générée"; }
	@[ -f secrets/smtp_cert.pem ] && echo "  certificat STARTTLS   : conservé" || { openssl req -x509 -newkey rsa:2048 -nodes -keyout secrets/smtp_key.pem -out secrets/smtp_cert.pem -days 825 -subj "/CN=mailarchiver-ng" 2>/dev/null && echo "  certificat STARTTLS   : généré"; }
	@[ -f secrets/api_secret.key ] && echo "  api_secret.key        : conservé" || { openssl rand -hex 48 > secrets/api_secret.key && echo "  api_secret.key        : généré"; }
	@echo "Secrets dans ./secrets (gitignorés). Durcissement : API_SECRET_KEY=\$$(cat secrets/api_secret.key)."

secret-key: ## affiche une clé forte à coller dans API_SECRET_KEY
	@openssl rand -hex 48

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

fmt:
	ruff format .

test:
	pytest

nonreg:
	python tests/nonreg.py
