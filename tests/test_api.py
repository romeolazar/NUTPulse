from __future__ import annotations

import base64
import os
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError as exc:  # the API tests need the application dependencies and httpx
    raise unittest.SkipTest(f"API dependencies are not installed: {exc}")

PASSWORD = "pässwörd-ünïcode-123"
_temp = tempfile.TemporaryDirectory()
os.environ.update(
    DATA_DIR=_temp.name,
    WEB_USERNAME="admin",
    WEB_PASSWORD=PASSWORD,
    AUTH_SECRET="test-secret",
    NUT_HOST="",
)

from app import main  # noqa: E402  (configuration is read from the environment at import time)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        _temp.cleanup()

    def login(self) -> TestClient:
        client = TestClient(main.app)
        response = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        return client

    def test_login_accepts_non_ascii_password(self) -> None:
        self.login()

    def test_non_ascii_credentials_are_rejected_without_a_server_error(self) -> None:
        for username, password in (("ädmin", PASSWORD), ("admin", "wrông"), ("админ", "пароль")):
            response = self.client.post("/api/auth/login", json={"username": username, "password": password})
            self.assertEqual(response.status_code, 401, (username, password))

    def test_forged_non_ascii_session_cookie_is_rejected(self) -> None:
        token = base64.urlsafe_b64encode("é|9999999999|é".encode()).decode()
        client = TestClient(main.app)
        client.cookies.set("nutpulse_session", token)
        self.assertEqual(client.get("/api/overview").status_code, 401)
        self.assertFalse(client.get("/api/auth/status").json()["authenticated"])

    def test_api_requires_authentication(self) -> None:
        self.assertEqual(TestClient(main.app).get("/api/history").status_code, 401)

    def test_overview_reports_estimated_power(self) -> None:
        client = self.login()
        variables = {"ups.load": "18", "ups.realpower.nominal": "390", "ups.status": "OL"}
        device_id = main.db.upsert_device("apc-api-test", "APC", variables, 1000)
        main.db.insert_sample(device_id, variables, 1000, 1.0)
        devices = {item["nut_name"]: item for item in client.get("/api/overview").json()["devices"]}
        device = devices["apc-api-test"]
        self.assertTrue(device["power_estimated"])
        self.assertAlmostEqual(device["real_power_w"], 70.2)
        self.assertIsNotNone(device["monthly_cost"])

    def test_history_year_range_has_monthly_breakdown(self) -> None:
        client = self.login()
        data = client.get("/api/history?range=1y").json()
        self.assertEqual(data["range"], "1y")
        self.assertEqual(len(data["monthly"]), 12)
        self.assertEqual({"month", "energy_kwh", "cost", "complete", "estimated"} <= set(data["monthly"][0]), True)
        self.assertIn("per_device", data)

    def test_history_unknown_range_falls_back_to_a_week(self) -> None:
        data = self.login().get("/api/history?range=bogus").json()
        self.assertEqual(data["range"], "7d")
        self.assertEqual(data["monthly"], [])

    def test_history_90_days_uses_hourly_data(self) -> None:
        client = self.login()
        variables = {"ups.realpower": "100", "ups.status": "OL"}
        device_id = main.db.upsert_device("hourly-api-test", "x", variables, 0)
        import time
        old = int(time.time()) - 40 * 86400
        for ts in range(old, old + 7200, 60):
            main.db.insert_sample(device_id, variables, ts, 1.0)
        main.db.rollup_hourly()
        main.db.prune_samples(0)
        data = client.get("/api/history?range=90d&device=hourly-api-test").json()
        self.assertGreater(data["energy_kwh"], 0.15)
        self.assertGreater(len(data["series"]), 0)


if __name__ == "__main__":
    unittest.main()
