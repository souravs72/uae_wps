# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

"""Tests for WPS Refund Request RFR generation.

Behaviour under test: over remaining credit refused; missing ACK refused; FDR
names SIF and ACK; second refund capped by prior submitted RFR.
Existing test files: none under wps_refund_request/; case previously in
test_wps_salary_file.py as test_refund_request_writes_rfr.
User instruction (verbatim): "Implement the plan as specified, it is attached for
your reference. Do NOT edit the plan file itself."
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from uae_wps.uae_wps.doctype.wps_salary_file.test_helpers import (
	IGNORE_TEST_RECORD_DEPENDENCIES,
	bank_account,
	fetched_file,
	generate_sif,
	prepare_sif,
	submit_ack,
)


def _accepted_file_with_refund_codes():
	wps_file = prepare_sif(fetched_file())
	ba = bank_account(wps_file.company, wps_file.wps_agent)
	frappe.db.set_value("WPS Salary File", wps_file.name, "employer_bank_account", ba)
	fmt = frappe.get_doc("WPS File Format", wps_file.wps_file_format)
	fmt.append("refund_reason_codes", {"code": "001", "label": "Overpayment"})
	fmt.save()
	generate_sif(wps_file)
	wps_file.reload()
	submit_ack(wps_file.name, ack_file_name="ACK999.ACK", received_on="2026-06-01")
	wps_file.reload()
	return wps_file


class TestWPSRefundRequest(IntegrationTestCase):
	def test_refund_above_credit_refused(self):
		wps_file = _accepted_file_with_refund_codes()
		emp = wps_file.employees[0].employee
		refund = frappe.get_doc(
			{
				"doctype": "WPS Refund Request",
				"wps_salary_file": wps_file.name,
				"employees": [
					{"employee": emp, "refund_amount": 100, "refund_reason_code": "001"},
				],
			}
		).insert()
		self.assertEqual(flt(refund.employees[0].max_refund_amount), 1000)
		with self.assertRaises(frappe.ValidationError):
			refund.employees[0].refund_amount = 1001
			refund.save()

	def test_refund_without_ack_refused(self):
		wps_file = prepare_sif(fetched_file())
		ba = bank_account(wps_file.company, wps_file.wps_agent)
		frappe.db.set_value("WPS Salary File", wps_file.name, "employer_bank_account", ba)
		fmt = frappe.get_doc("WPS File Format", wps_file.wps_file_format)
		fmt.append("refund_reason_codes", {"code": "001", "label": "Overpayment"})
		fmt.save()
		generate_sif(wps_file)
		wps_file.reload()
		frappe.db.set_value(
			"WPS Salary File",
			wps_file.name,
			{"status": "Accepted", "ack_file_name": "", "accepted_on": "2026-06-01"},
		)
		emp = wps_file.employees[0].employee
		with self.assertRaises(frappe.ValidationError) as raised:
			frappe.get_doc(
				{
					"doctype": "WPS Refund Request",
					"wps_salary_file": wps_file.name,
					"employees": [
						{"employee": emp, "refund_amount": 10, "refund_reason_code": "001"},
					],
				}
			).insert()
		self.assertIn("ACK", str(raised.exception))

	def test_rfr_names_sif_and_ack(self):
		wps_file = _accepted_file_with_refund_codes()
		emp = wps_file.employees[0].employee
		refund = frappe.get_doc(
			{
				"doctype": "WPS Refund Request",
				"wps_salary_file": wps_file.name,
				"employees": [
					{"employee": emp, "refund_amount": 100, "refund_reason_code": "001"},
				],
			}
		).insert()
		result = refund.generate_rfr()
		refund.reload()
		self.assertEqual(refund.status, "Generated")
		self.assertTrue(result["file_name"].endswith(".RFR"))
		content = frappe.get_doc("File", {"file_url": refund.rfr_file}).get_content()
		if isinstance(content, bytes):
			content = content.decode("ascii")
		lines = [line for line in content.split("\r\n") if line]
		self.assertTrue(lines[0].startswith("FDR,"))
		self.assertIn(wps_file.file_name, lines[0])
		self.assertIn("ACK999.ACK", lines[0])
		self.assertTrue(lines[-1].startswith("FCR,"))

	def test_second_refund_capped_by_prior(self):
		wps_file = _accepted_file_with_refund_codes()
		emp = wps_file.employees[0].employee
		first = frappe.get_doc(
			{
				"doctype": "WPS Refund Request",
				"wps_salary_file": wps_file.name,
				"employees": [
					{"employee": emp, "refund_amount": 600, "refund_reason_code": "001"},
				],
			}
		).insert()
		first.generate_rfr()
		second = frappe.get_doc(
			{
				"doctype": "WPS Refund Request",
				"wps_salary_file": wps_file.name,
				"employees": [
					{"employee": emp, "refund_amount": 400, "refund_reason_code": "001"},
				],
			}
		).insert()
		self.assertEqual(flt(second.employees[0].max_refund_amount), 400)
		with self.assertRaises(frappe.ValidationError):
			second.employees[0].refund_amount = 401
			second.save()
