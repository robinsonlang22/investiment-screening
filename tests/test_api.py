import unittest

import httpx

from api.app import app
from tests.helpers import trading_dates


class ApiPipelineTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.price_dates = trading_dates(120)
        cls.margin_dates = cls.price_dates[-21:]
        cls.raw_data = {
            "price_history": {
                "rows": [
                    {
                        "日期": day,
                        "收盘价": 100 * 1.002**index,
                    }
                    for index, day in enumerate(cls.price_dates)
                ],
                "复权方式": "前复权",
                "latest_closed_date": cls.price_dates[-1],
            },
            "margin_history": {
                "rows": [
                    {
                        "日期": day,
                        "融资余额": 1.0 * 1.0001**index,
                    }
                    for index, day in enumerate(cls.margin_dates)
                ],
                "单位": "亿元",
            },
            "market_cap": {
                "rows": [
                    {
                        "日期": cls.margin_dates[-1],
                        "自由流通市值": 20,
                    }
                ],
                "单位": "亿元",
            },
        }

    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_health_and_only_two_processing_endpoints(self):
        self.assertEqual((await self.client.get("/health")).status_code, 200)
        schema = (await self.client.get("/openapi.json")).json()
        processing_paths = {
            path for path in schema["paths"] if path != "/health"
        }
        self.assertEqual(processing_paths, {"/prepare", "/evaluate"})

    async def test_prepare_reports_missing_data_for_research_retry(self):
        response = await self.client.post(
            "/prepare",
            json={
                "symbol": "301536.SZ",
                "raw_data": {
                    "price_history": self.raw_data["price_history"],
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["valid"])
        self.assertFalse(body["complete"])
        self.assertEqual(body["processable_rules"], ["P1", "P2"])
        self.assertTrue(body["retryable"])
        self.assertIn("margin_history", body["missing_fields"])
        self.assertIn("free_float_market_cap", body["missing_fields"])
        self.assertIn("daily_margin_balance_21d", body["recommended_queries"])

    async def test_prepare_then_evaluate_end_to_end(self):
        prepared_response = await self.client.post(
            "/prepare",
            json={
                "symbol": "301536.SZ",
                "raw_data": self.raw_data,
            },
        )
        self.assertEqual(prepared_response.status_code, 200)
        prepared = prepared_response.json()
        self.assertTrue(prepared["valid"])
        self.assertTrue(prepared["complete"])
        self.assertFalse(prepared["retryable"])
        self.assertEqual(prepared["missing_fields"], [])
        self.assertEqual(len(prepared["normalized_data"]["price_rows"]), 120)
        self.assertEqual(len(prepared["normalized_data"]["margin_rows"]), 21)

        evaluated_response = await self.client.post(
            "/evaluate",
            json={
                "symbol": "301536.SZ",
                "normalized_data": prepared["normalized_data"],
                "data_quality": prepared["data_quality"],
                "spread_expanding": True,
            },
        )
        self.assertEqual(evaluated_response.status_code, 200)
        evaluated = evaluated_response.json()
        self.assertEqual(evaluated["decision"], "pass")
        self.assertEqual(evaluated["overall_status"], "COMPLIANT")
        self.assertEqual(evaluated["rule_version"], "2026-07-01")
        self.assertEqual(set(evaluated["results"]), {"p1", "p2", "f1"})
        self.assertFalse(evaluated["report_constraints"]["may_recalculate"])
        self.assertFalse(evaluated["report_constraints"]["may_override_status"])

    async def test_evaluate_degrades_missing_inputs_by_rule(self):
        response = await self.client.post(
            "/evaluate",
            json={
                "symbol": "301536.SZ",
                "normalized_data": {},
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["decision"], "information_insufficient")
        self.assertEqual(
            {result["status"] for result in body["results"].values()},
            {"INSUFFICIENT_INFORMATION"},
        )

    async def test_price_only_evaluates_p1_p2_and_degrades_f1(self):
        prepared_response = await self.client.post(
            "/prepare",
            json={
                "symbol": "301536.SZ",
                "raw_data": {
                    "price_history": self.raw_data["price_history"],
                },
            },
        )
        prepared = prepared_response.json()
        evaluated_response = await self.client.post(
            "/evaluate",
            json={
                "symbol": "301536.SZ",
                "normalized_data": prepared["normalized_data"],
                "data_quality": prepared["data_quality"],
            },
        )
        self.assertEqual(evaluated_response.status_code, 200)
        evaluated = evaluated_response.json()
        self.assertNotIn("missing_inputs", evaluated["results"]["p1"])
        self.assertNotIn("missing_inputs", evaluated["results"]["p2"])
        self.assertIn("metrics", evaluated["results"]["p1"])
        self.assertIn("metrics", evaluated["results"]["p2"])
        self.assertEqual(
            evaluated["results"]["f1"]["status"],
            "INSUFFICIENT_INFORMATION",
        )
        self.assertEqual(
            evaluated["results"]["f1"]["missing_inputs"],
            ["margin_history", "free_float_market_cap"],
        )


if __name__ == "__main__":
    unittest.main()
