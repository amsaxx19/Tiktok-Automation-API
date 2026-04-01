"""Unit tests for the price enrichment and Kalodata scraper modules."""

import pytest

from scraper.price_enricher import (
    parse_idr_price,
    estimate_price_from_description,
    parse_sold_count,
)
from scraper.kalodata import _parse_idr_string, KalodataProduct


# ── parse_idr_price ──────────────────────────────────────────


class TestParseIdrPrice:
    """Tests for the IDR price parser."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Rp 49.000", 49000),
            ("Rp49,000", 49000),
            ("Rp49000", 49000),
            ("Rp 1.250.000", 1250000),
            ("Rp 12.500", 12500),
            ("Rp99.900", 99900),
            ("Rp100.000", 100000),
            ("Rp 3.540.000", 3540000),
            ("Rp 15.000", 15000),
            ("Rp 5000", 5000),
        ],
    )
    def test_rp_format(self, text: str, expected: int):
        assert parse_idr_price(text) == expected

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("45rb", 45000),
            ("45ribu", 45000),
            ("100 rb", 100000),
        ],
    )
    def test_rb_ribu_format(self, text: str, expected: int):
        assert parse_idr_price(text) == expected

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("50k", 50000),
            ("50K", 50000),
        ],
    )
    def test_k_format(self, text: str, expected: int):
        assert parse_idr_price(text) == expected

    @pytest.mark.parametrize(
        "text",
        ["Rp 0", "", "hello", "Rp NaN"],
    )
    def test_zero_or_invalid(self, text: str):
        assert parse_idr_price(text) == 0


# ── estimate_price_from_description ──────────────────────────


class TestEstimatePriceFromDescription:
    """Tests for description-based price estimation."""

    @pytest.mark.parametrize(
        "desc,expected",
        [
            ("cuma 45rb, beli sekarang!", 45000),
            ("harga Rp 49.000", 49000),
            ("diskon jadi 35ribu", 35000),
            ("only 50k bro", 50000),
            ("hanya Rp 99.000 aja", 99000),
        ],
    )
    def test_known_patterns(self, desc: str, expected: int):
        assert estimate_price_from_description(desc) == expected

    def test_empty_string(self):
        assert estimate_price_from_description("") == 0

    def test_no_price(self):
        assert estimate_price_from_description("this has no price info") == 0


# ── parse_sold_count ─────────────────────────────────────────


class TestParseSoldCount:
    @pytest.mark.parametrize(
        "text,expected_nonempty",
        [
            ("10rb+ terjual", True),
            ("1.2rb terjual", True),
            ("100+ terjual", False),  # No rb/k suffix — not matched
            ("sold 50", False),  # English format — not matched
            ("no sold info here", False),
            ("5k terjual", True),
            ("1jt+ terjual", True),
        ],
    )
    def test_sold_patterns(self, text: str, expected_nonempty: bool):
        result = parse_sold_count(text)
        assert bool(result) == expected_nonempty


# ── _parse_idr_string (Kalodata module) ──────────────────────


class TestParseIdrString:
    """Tests for Kalodata's compact IDR parser (e.g. 'Rp3.54m')."""

    @pytest.mark.parametrize(
        "s,expected",
        [
            ("Rp3.54m", 3_540_000),
            ("Rp502.38k", 502_380),
            ("Rp0.00", 0),
            ("Rp5.08m", 5_080_000),
            ("Rp1.23b", 1_230_000_000),
            ("Rp100.50k", 100_500),
            ("Rp 3.54m", 3_540_000),
            ("NaN", 0),
            ("", 0),
        ],
    )
    def test_known_values(self, s: str, expected: int):
        assert _parse_idr_string(s) == expected


# ── KalodataProduct ──────────────────────────────────────────


class TestKalodataProduct:
    def test_price_idr_from_min(self):
        p = KalodataProduct(price_min_idr=49000)
        assert p.price_idr == 49000

    def test_price_idr_from_usd(self):
        p = KalodataProduct(price_min_usd=3.0)
        # 3.0 * 16300 = 48900
        assert p.price_idr == 48900

    def test_price_idr_zero(self):
        p = KalodataProduct()
        assert p.price_idr == 0

    def test_to_dict(self):
        p = KalodataProduct(product_id="123", title="Test")
        d = p.to_dict()
        assert d["product_id"] == "123"
        assert d["title"] == "Test"
