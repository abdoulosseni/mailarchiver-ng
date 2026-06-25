"""Identité authentifiée + authentification AD/LDAP.

Les comptes locaux (dont l'admin par défaut) sont gérés en base via users.py.
LDAP/AD reste disponible en parallèle pour les organisations qui l'utilisent :
le rôle admin est dérivé de l'appartenance au groupe LDAP_ADMIN_GROUP.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ldap3 import ALL, Connection, Server


@dataclass
class AuthenticatedUser:
    username: str
    display_name: str
    role: str  # 'admin' | 'user' | 'auditor'
    is_admin: bool
    email: str | None = None
    audited_emails: list | None = None  # adresses auditées (rôle 'auditor')


class LdapAuthenticator:
    def __init__(self) -> None:
        self.enabled = os.environ.get("LDAP_ENABLED", "false").lower() == "true"
        self.server_uri = os.environ.get("LDAP_SERVER", "")
        self.base_dn = os.environ.get("LDAP_BASE_DN", "")
        self.user_dn_template = os.environ.get("LDAP_USER_DN_TEMPLATE", "uid={username}," + self.base_dn)
        self.admin_group = os.environ.get("LDAP_ADMIN_GROUP", "")

    def authenticate(self, username: str, password: str) -> AuthenticatedUser | None:
        if not self.enabled or not username or not password:
            return None

        user_dn = self.user_dn_template.format(username=username)
        server = Server(self.server_uri, get_info=ALL)
        conn = Connection(server, user=user_dn, password=password)
        if not conn.bind():
            return None

        is_admin = False
        email = None
        conn.search(user_dn, "(objectClass=*)", attributes=["memberOf", "mail"])
        if conn.entries:
            entry = conn.entries[0]
            if self.admin_group and "memberOf" in entry:
                is_admin = self.admin_group in entry.memberOf.values
            if "mail" in entry and entry.mail.value:
                email = entry.mail.value
        conn.unbind()

        return AuthenticatedUser(
            username=username,
            display_name=username,
            role="admin" if is_admin else "user",
            is_admin=is_admin,
            email=email,
        )
