import unittest

import httpx

from api.app import app
from tests.helpers import trading_dates


class ApiPipelineTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        dates = trading_dates(120)
        cls.price_history = [
            {"date": day, "close": 100 * 1.002**index}
            for index, day in enumerate(dates)
        ]

    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    def request_body(self, **overrides):
        return {
            "symbol": "301536.sz",
            "price_history": self.price_history,
            **overrides,
        }

    async def test_versioned_evaluation_routes(self):
        schema = (await self.client.get("/openapi.json")).json()
        processing_paths = {path for path in schema["paths"] if path != "/health"}
        self.assertEqual(
            processing_paths,
            {
                "/v1/evaluate/p1",
                "/v1/evaluate/p2",
                "/v1/evaluate/full",
            },
        )
        self.assertEqual((await self.client.post("/evaluate")).status_code, 404)

    async def test_p1_returns_only_p1(self):
        response = await self.client.post(
            "/v1/evaluate/p1",
            json=self.request_body(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["symbol"], "301536.SZ")
        self.assertEqual(body["analysis_type"], "p1")
        self.assertEqual(set(body["results"]), {"p1"})
        self.assertEqual(body["data_quality"]["price_history"]["observations"], 120)

    async def test_p2_returns_only_p2(self):
        response = await self.client.post(
            "/v1/evaluate/p2",
            json=self.request_body(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["analysis_type"], "p2")
        self.assertEqual(set(body["results"]), {"p2"})
        self.assertEqual(body["results"]["p2"]["direction_name"], "稳步上升")

    async def test_full_returns_and_aggregates_both_rules(self):
        response = await self.client.post(
            "/v1/evaluate/full",
            json=self.request_body(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["analysis_type"], "full")
        self.assertEqual(set(body["results"]), {"p1", "p2"})
        self.assertEqual(
            body["data_quality"]["evaluation_completeness"]["completed_rules"],
            ["P1", "P2"],
        )

    async def test_short_history_is_rejected(self):
        response = await self.client.post(
            "/v1/evaluate/p1",
            json=self.request_body(price_history=self.price_history[:119]),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "INVALID_ENGINE_INPUT")

    async def test_extra_fields_are_rejected(self):
        response = await self.client.post(
            "/v1/evaluate/p1",
            json=self.request_body(analysis_type="p1"),
        )
        self.assertEqual(response.status_code, 422)

        response = await self.client.post(
            "/v1/evaluate/p1",
            json=self.request_body(spread_expanding=True),
        )
        self.assertEqual(response.status_code, 422)

    async def test_invalid_price_point_is_rejected(self):
        invalid = [*self.price_history]
        invalid[-1] = {"date": invalid[-1]["date"], "close": 0}
        response = await self.client.post(
            "/v1/evaluate/p1",
            json=self.request_body(price_history=invalid),
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
