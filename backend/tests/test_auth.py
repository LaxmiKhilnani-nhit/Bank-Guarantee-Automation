"""Accounts, password rules, lockout, roles and server-side sessions."""
from __future__ import annotations

import datetime as dt
import unittest

from bgauto import auth, store
from support import TempDatabase


class PasswordTests(unittest.TestCase):
    def test_hash_and_verify_round_trip(self):
        stored = auth.hash_password("Correct#Horse9")
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotIn("Correct#Horse9", stored)
        self.assertTrue(auth.verify_password("Correct#Horse9", stored))
        self.assertFalse(auth.verify_password("Correct#Horse8", stored))

    def test_garbage_hash_is_rejected_not_raised(self):
        for value in ("", "bcrypt$...", "scrypt$broken", None):
            self.assertFalse(auth.verify_password("anything", value))

    def test_password_rules(self):
        self.assertEqual(
            auth.password_problems("short"),
            ["at least 8 characters", "an uppercase letter", "a digit", "a symbol"],
        )
        self.assertEqual(auth.password_problems("Correct#Horse9"), [])
        # The pilot's role-based demo passwords must be accepted (8-char floor).
        self.assertEqual(auth.password_problems("Admin123!"), [])
        self.assertEqual(auth.password_problems("Viewer123!"), [])
        self.assertEqual(auth.password_problems("Updater123!"), [])
        self.assertIn("a lowercase letter", auth.password_problems("ALLUPPER#123"))


class UserTests(TempDatabase):
    def setUp(self):
        super().setUp()
        self.admin = self.bootstrap_admin()

    def test_bootstrap_creates_one_admin(self):
        self.assertIsNotNone(self.admin)
        with store.connect() as conn:
            users = auth.list_users(conn)
        self.assertEqual([u.username for u in users], ["admin"])
        self.assertEqual(users[0].role, "admin")
        self.assertTrue(auth.verify_password("Admin#12345", self._hash("admin")))

    def test_bootstrap_is_idempotent(self):
        self.assertIsNone(self.bootstrap_admin())
        with store.connect() as conn:
            self.assertEqual(len(auth.list_users(conn)), 1)

    def _hash(self, username: str) -> str:
        with store.connect() as conn:
            return conn.execute(
                "SELECT password_hash FROM users WHERE username = ?", (username,)
            ).fetchone()[0]

    def test_roles_and_effective_role(self):
        with store.connect() as conn:
            uploader = auth.create_user(conn, "asha", "Uploader#123", "uploader")
            viewer = auth.create_user(conn, "vik", "Viewer#12345", "viewer")
        self.assertEqual(uploader.role, "uploader")
        self.assertTrue(uploader.has_role("uploader"))
        self.assertFalse(uploader.has_role("admin"))
        self.assertEqual(viewer.role, "viewer")
        # Admin passes every role check, by design.
        with store.connect() as conn:
            admin = auth.get_user(conn, auth.list_users(conn)[0].id)
        self.assertTrue(admin.has_role("viewer"))

    def test_duplicate_username_is_refused(self):
        with store.connect() as conn:
            with self.assertRaises(auth.AuthError) as caught:
                auth.create_user(conn, "ADMIN", "Another#123", "viewer")
        self.assertEqual(caught.exception.code, "duplicate")

    def test_weak_password_is_refused(self):
        with store.connect() as conn:
            with self.assertRaises(auth.AuthError) as caught:
                auth.create_user(conn, "weak", "password", "viewer")
        self.assertEqual(caught.exception.code, "weak_password")
        self.assertTrue(
            any("uppercase" in problem for problem in auth.password_problems("password"))
        )

    def test_wrong_password_counts_then_locks(self):
        with store.connect() as conn:
            for attempt in range(1, auth.MAX_FAILED_ATTEMPTS):
                with self.assertRaises(auth.AuthError):
                    auth.authenticate(conn, "admin", "Wrong#12345")
                row = conn.execute(
                    "SELECT failed_attempts, locked_until FROM users WHERE username='admin'"
                ).fetchone()
                self.assertEqual(row["failed_attempts"], attempt)
                self.assertIsNone(row["locked_until"])
            with self.assertRaises(auth.AuthError) as caught:
                auth.authenticate(conn, "admin", "Wrong#12345")
            self.assertEqual(caught.exception.code, "invalid")
            row = conn.execute(
                "SELECT failed_attempts, locked_until FROM users WHERE username='admin'"
            ).fetchone()
            self.assertEqual(row["failed_attempts"], auth.MAX_FAILED_ATTEMPTS)
            self.assertIsNotNone(row["locked_until"])
            # Even the right password is refused while locked.
            with self.assertRaises(auth.AuthError) as locked:
                auth.authenticate(conn, "admin", "Admin#12345")
            self.assertEqual(locked.exception.code, "locked")
            actions = [
                r[0]
                for r in conn.execute("SELECT action FROM audit_logs ORDER BY id")
            ]
            self.assertIn("login_failed", actions)
            self.assertIn("locked", actions)

    def test_lock_expires_and_success_resets(self):
        with store.connect() as conn:
            conn.execute(
                "UPDATE users SET failed_attempts = 4, locked_until = ? WHERE username='admin'",
                ((store.utcnow() - dt.timedelta(minutes=1)).isoformat(sep=" "),),
            )
            user = auth.authenticate(conn, "admin", "Admin#12345")
            self.assertEqual(user.role, "admin")
            row = conn.execute(
                "SELECT failed_attempts, locked_until, last_login FROM users WHERE username='admin'"
            ).fetchone()
            self.assertEqual(row["failed_attempts"], 0)
            self.assertIsNone(row["locked_until"])
            self.assertIsNotNone(row["last_login"])

    def test_inactive_account_cannot_sign_in(self):
        with store.connect() as conn:
            user = auth.create_user(conn, "gone", "Gone#123456", "viewer")
            auth.update_user(conn, user.id, is_active=False)
            with self.assertRaises(auth.AuthError) as caught:
                auth.authenticate(conn, "gone", "Gone#123456")
        self.assertEqual(caught.exception.code, "inactive")


class SessionTests(TempDatabase):
    def setUp(self):
        super().setUp()
        self.bootstrap_admin()
        with store.connect() as conn:
            self.user = auth.get_user(conn, auth.list_users(conn)[0].id)

    def test_session_round_trip_and_logout(self):
        with store.connect() as conn:
            token = auth.create_session(conn, self.user.id, ip="127.0.0.1")
            self.assertNotIn(token, str(conn.execute("SELECT * FROM sessions").fetchall()))
            self.assertEqual(auth.session_user(conn, token).id, self.user.id)
            auth.destroy_session(conn, token)
            self.assertIsNone(auth.session_user(conn, token))

    def test_expired_session_is_deleted(self):
        with store.connect() as conn:
            token = auth.create_session(conn, self.user.id, minutes=-1)
            self.assertIsNone(auth.session_user(conn, token))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_password_change_revokes_sessions(self):
        with store.connect() as conn:
            token = auth.create_session(conn, self.user.id)
            auth.set_password(conn, self.user.id, "Changed#12345")
            self.assertIsNone(auth.session_user(conn, token))
            self.assertTrue(
                auth.verify_password(
                    "Changed#12345",
                    conn.execute(
                        "SELECT password_hash FROM users WHERE id = ?", (self.user.id,)
                    ).fetchone()[0],
                )
            )

    def test_deactivating_a_user_revokes_sessions(self):
        with store.connect() as conn:
            token = auth.create_session(conn, self.user.id)
            auth.update_user(conn, self.user.id, is_active=False)
            self.assertIsNone(auth.session_user(conn, token))


if __name__ == "__main__":
    unittest.main()
