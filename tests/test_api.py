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
                    {"日期": day, "收盘价": 100 * 1.002**index}
                    for index, day in enumerate(cls.price_dates)
                ],
                "复权方式": "前复权",
                "latest_closed_date": cls.price_dates[-1],
            },
            "margin_history": {
                "rows": [
                    {"日期": day, "融资余额": 1.0 * 1.0001**index}
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

    async def test_only_evaluate_endpoint_remains(self):
        self.assertEqual((await self.client.get("/health")).status_code, 200)
        schema = (await self.client.get("/openapi.json")).json()
        processing_paths = {
            path for path in schema["paths"] if path != "/health"
        }
        self.assertEqual(processing_paths, {"/evaluate"})

    async def test_raw_data_evaluates_end_to_end(self):
        response = await self.client.post(
            "/evaluate",
            json={
                "symbol": "301536.SZ",
                "raw_data": self.raw_data,
                "spread_expanding": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        evaluated = response.json()
        self.assertEqual(evaluated["decision"], "pass")
        self.assertEqual(evaluated["overall_status"], "COMPLIANT")
        self.assertEqual(evaluated["rule_version"], "2026-07-01")
        self.assertEqual(set(evaluated["results"]), {"p1", "p2", "f1"})
        self.assertFalse(evaluated["report_constraints"]["may_recalculate"])
        self.assertFalse(evaluated["report_constraints"]["may_override_status"])

    async def test_empty_raw_data_degrades_all_rules(self):
        response = await self.client.post(
            "/evaluate",
            json={"symbol": "301536.SZ", "raw_data": {}},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["decision"], "information_insufficient")
        self.assertEqual(
            {result["status"] for result in body["results"].values()},
            {"INSUFFICIENT_INFORMATION"},
        )

    async def test_price_only_calculates_p1_p2(self):
        response = await self.client.post(
            "/evaluate",
            json={
                "symbol": "301536.SZ",
                "raw_data": {
                    "price_history": self.raw_data["price_history"],
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        evaluated = response.json()
        self.assertNotIn("missing_inputs", evaluated["results"]["p1"])
        self.assertNotIn("missing_inputs", evaluated["results"]["p2"])
        self.assertEqual(
            evaluated["results"]["f1"]["missing_inputs"],
            ["margin_history", "free_float_market_cap"],
        )

    async def test_bad_margin_does_not_block_price_rules(self):
        response = await self.client.post(
            "/evaluate",
            json={
                "symbol": "301536.SZ",
                "raw_data": {
                    "price_history": self.raw_data["price_history"],
                    "margin_history": [{"说明": "无日期"}],
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        evaluated = response.json()
        self.assertNotIn("missing_inputs", evaluated["results"]["p1"])
        self.assertIn(
            "margin_history",
            evaluated["data_quality"]["adapter_errors"],
        )


if __name__ == "__main__":
    unittest.main()
