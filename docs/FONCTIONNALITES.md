# MailArchiver-NG — Documentation des fonctionnalités

## Archivage

### Réception SMTP intégrée (STARTTLS)
Serveur SMTP natif (port 2525) recevant les mails par *journaling*/BCC depuis un
MTA, sans infrastructure supplémentaire. **STARTTLS** pour le transport chiffré.
Le serveur accuse réception immédiatement et dépose le mail dans une file durable.

### Collecte IMAP / POP3
L'administrateur configure des **sources** (onglet *Sources*) : serveur, port,
identifiant, mot de passe (chiffré), SSL, dossier (IMAP), intervalle. Le
`fetch-worker` les relève périodiquement et archive les mails (même pipeline que
le SMTP). Bouton **« Relever maintenant »** pour un déclenchement manuel.
- IMAP : ne relève que les messages non lus (`UNSEEN`), marqués lus ensuite.
- POP3 : option « supprimer après collecte ».

### Déduplication des pièces jointes
Chaque pièce jointe est identifiée par l'empreinte SHA-256 de son contenu : une PJ
identique présente dans N mails n'est **stockée qu'une seule fois** (compteur de
références). Garbage collection lors de la purge.

### Compression Zlib + Chiffrement AES-256
Corps et pièces jointes sont **compressés (Zlib)** puis **chiffrés (AES-256-GCM)**
au repos (*envelope encryption* : une clé de données par blob, scellée par la clé
maître).

### Signature et vérification numériques
Chaque archive est **signée (Ed25519)** sur une empreinte stable (en-têtes + corps
+ pièces jointes). La signature est vérifiée à la consultation/export : l'en-tête
`X-Archive-Integrity` indique `valid` ou `INVALID` → archives inviolables.

### Politique de conservation
Paramètre global **`retention_days`** (onglet *Paramètres*, **admin uniquement**),
**1 an par défaut**, `0` = illimité. Le `retention-worker` purge automatiquement
les mails archivés depuis plus longtemps (base + index + blobs, journalisé). La
purge est dynamique : modifier le paramètre s'applique à toute l'archive.

## Recherche

### Recherche avancée
Générateur de requêtes (onglet *Recherche*) : texte (sujet/corps), expéditeur,
destinataire, **expéditeur ou destinataire**, sujet exact, plage de dates.
Résultats triés du **plus récent au plus ancien**, **pagination** (50/page) avec
total exact et **saut à une page** précise. Chargement automatique de la liste à
l'ouverture.

### Consultation et export
- **Consulter** : lecture du mail dans l'interface (modale) — sujet, de/à/cc,
  **date du mail** et **date d'archivage**, corps, pièces jointes, **toutes les
  en-têtes**, état d'intégrité.
- **Télécharger EML** : export au format `.eml` standard (reconstruit, déchiffré).

### Transfert SMTP
Depuis les résultats, transfert d'un mail vers n'importe quelle adresse via le
relais SMTP configuré dans les *Paramètres*.

## Comptes & accès (RBAC)

Trois rôles :
- **Administrateur** : accès à tous les mails + gestion (comptes, sources,
  paramètres). Compte par défaut `admin/admin` créé automatiquement à
  l'initialisation (à changer en production). Compte admin principal non
  supprimable / non désactivable.
- **Auditeur** : lit les mails d'un **périmètre d'adresses** défini par l'admin
  (en plus de sa propre adresse, requise).
- **Utilisateur** : ne voit que les mails qui le concernent (son adresse en
  from/to/cc).

Gestion par l'admin (onglet *Comptes*) : créer, **modifier le mot de passe**,
**modifier l'e-mail**, **modifier le périmètre audité**, **activer/désactiver**,
supprimer. Chaque utilisateur peut **changer son propre mot de passe** (onglet
*Mon compte*).

### Restauration
Bouton **« Restaurer »** (admin) sur un compte utilisateur ou auditeur : renvoie
**tous les mails de son périmètre** vers son adresse via SMTP, en **tâche de
fond** (job asynchrone suivi dans « Restaurations récentes »).

## Interface web

- Accès navigateur (SPA Vue 3), **thème clair (défaut) / sombre** commutable.
- **Authentification AD/LDAP** optionnelle (en complément des comptes locaux).
- **Temps réel** : onglet affichant les mails au fur et à mesure de leur
  archivage (flux SSE), filtré par périmètre.
- Design compact, footer.

## Import / Export

- **Export EML** : depuis la recherche (par mail).
- **Import EML** : réinjection de fichiers `.eml` dans le pipeline d'archivage
  (`POST /import/eml`).

## Surveillance & audit

Journalisation complète (table `audit_events`) : connexions, recherches,
consultations, exports, transferts, purges, créations/modifications/suppressions
de comptes, modifications de paramètres, relèves de sources.

## Internationalisation

Socle i18n côté API (négociation de langue) ; interface en français. Extensible
à d'autres langues.

## Performance

- Ingestion SMTP ~500 msg/s ; traitement (archivage complet) ~200 msg/s avec 8
  workers (machine de dev). Scaling horizontal via le nombre de workers.
- Capacité d'injection cible **100–200 mails/seconde** atteinte (voir
  `loadtest/`).

## Robustesse (validée)

Aucune perte de mail sous crash de workers ; reconnexion automatique
(RabbitMQ/PostgreSQL) ; dead-letter queue anti-poison ; 503 (et non 500) si la
base est momentanément indisponible ; auto-cicatrisation de l'index après une
panne OpenSearch ; ingestion indépendante de l'API.
