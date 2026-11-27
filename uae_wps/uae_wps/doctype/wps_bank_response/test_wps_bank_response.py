# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

"""Tests for WPS Bank Response ACK/NAK against a submitted SIF.

Behaviour under test: ACK sets Accepted/Paid late; NAK sets Rejected and keeps SIF
bytes; second response refused; generate refused after Accepted; future received_on
refused; cancel ACK refused when a submitted refund exists.
Existing test files: none under wps_bank_response/; cases previously in
test_wps_salary_file.py.
User instruction (verbatim): "Implement the plan as specified, it is attached for
your reference. Do NOT edit the plan file itself."
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, now_datetime

from uae_wps.uae_wps.doctype.wps_salary_file.test_helpers import (
	IGNORE_TEST_RECORD_DEPENDENCIES,
	backdate_generated_at,
	bank_account,
	fetched_file,
	generate_sif,
	prepare_sif,
	submit_ack,
)


class TestWPSBankResponse(IntegrationTestCase):
	def test_ack_sets_accepted_and_paid_late(self):
		wps_file = prepare_sif(fetched_file())
		generate_sif(wps_file)
		wps_file.reload()
		submit_ack(wps_file.name, ack_file_name="ACK123.ACK", received_on="2026-06-02")
		wps_file.reload()
		self.assertEqual(wps_file.status, "Accepted")
		self.assertEqual(wps_file.ack_file_name, "ACK123.ACK")
		self.assertEqual(str(wps_file.accepted_on), "2026-06-02")
		self.assertEqual(wps_file.payment_status, "Paid late")

	def test_nak_sets_rejected_without_changing_sif(self):
		wps_file = prepare_sif(fetched_file())
		generate_sif(wps_file)
		wps_file.reload()
		original_hash = wps_file.file_hash
		backdate_generated_at(wps_file.name)
		frappe.get_doc(
			{
				"doctype": "WPS Bank Response",
				"wps_salary_file": wps_file.name,
				"response": "NAK",
				"received_on": "2026-06-02",
				"bank_lines": "Rejected by agent",
			}
		).insert().submit()
		wps_file.reload()
		self.assertEqual(wps_file.status, "Rejected")
		self.assertEqual(wps_file.file_hash, original_hash)
		self.assertFalse(wps_file.ack_file_name)
		self.assertIn("Unpaid", wps_file.payment_status)
		self.assertRaises(frappe.ValidationError, wps_file.generate_sif)

	def test_second_response_refused(self):
		wps_file = prepare_sif(fetched_file())
		generate_sif(wps_file)
		wps_file.reload()
		submit_ack(wps_file.name)
		self.assertRaises(
			frappe.ValidationError,
			frappe.get_doc(
				{
					"doctype": "WPS Bank Response",
					"wps_salary_file": wps_file.name,
					"response": "NAK",
					"received_on": "2026-06-02",
				}
			).insert,
		)

	def test_generate_refused_after_accepted(self):
		wps_file = prepare_sif(fetched_file())
		generate_sif(wps_file)
		wps_file.reload()
		submit_ack(wps_file.name)
		wps_file.reload()
		self.assertEqual(wps_file.status, "Accepted")
		self.assertRaises(frappe.ValidationError, wps_file.generate_sif)

	def test_received_on_cannot_be_future(self):
		wps_file = prepare_sif(fetched_file())
		generate_sif(wps_file)
		wps_file.reload()
		future = str(add_days(getdate(now_datetime()), 2))
		with self.assertRaises(frappe.ValidationError) as raised:
			frappe.get_doc(
				{
					"doctype": "WPS Bank Response",
					"wps_salary_file": wps_file.name,
					"response": "ACK",
					"ack_file_name": "ACKFUT.ACK",
					"received_on": future,
				}
			).insert()
		self.assertIn("after today", str(raised.exception))

	def test_ack_received_on_before_generated_at_refused(self):
		wps_file = prepare_sif(fetched_file())
		generate_sif(wps_file)
		wps_file.reload()
		# Leave generated_at as now; receive date before generation must fail.
		with self.assertRaises(frappe.ValidationError) as raised:
			frappe.get_doc(
				{
					"doctype": "WPS Bank Response",
					"wps_salary_file": wps_file.name,
					"response": "ACK",
					"ack_file_name": "ACKOLD.ACK",
					"received_on": "2026-06-01",
				}
			).insert()
		self.assertIn("generated", str(raised.exception).lower())

	def test_cancel_ack_blocked_when_refund_exists(self):
		wps_file = prepare_sif(fetched_file())
		ba = bank_account(wps_file.company, wps_file.wps_agent)
		frappe.db.set_value("WPS Salary File", wps_file.name, "employer_bank_account", ba)
		fmt = frappe.get_doc("WPS File Format", wps_file.wps_file_format)
		fmt.append("refund_reason_codes", {"code": "001", "label": "Overpayment"})
		fmt.save()
		generate_sif(wps_file)
		wps_file.reload()
		response = submit_ack(wps_file.name, ack_file_name="ACK999.ACK")
		wps_file.reload()
		emp = wps_file.employees[0].employee
		refund = frappe.get_doc(
			{
				"doctype": "WPS Refund Request",
				"wps_salary_file": wps_file.name,
				"employees": [
					{"employee": emp, "refund_amount": 50, "refund_reason_code": "001"},
				],
			}
		).insert()
		refund.generate_rfr()
		with self.assertRaises(frappe.ValidationError) as raised:
			response.cancel()
		self.assertIn("Refund Request", str(raised.exception))
