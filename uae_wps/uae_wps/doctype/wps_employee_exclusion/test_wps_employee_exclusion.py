# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

"""Tests for WPS Employee Exclusion covering a salary month.

Behaviour under test: a submitted exclusion covering the salary month omits that
employee from the EDR list and from wages_due.
Existing test files: none under wps_employee_exclusion/; case previously in
test_wps_salary_file.py as test_exclusion_is_an_omission.
User instruction (verbatim): "Implement the plan as specified, it is attached for
your reference. Do NOT edit the plan file itself."
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from uae_wps.uae_wps.doctype.wps_salary_file.test_helpers import (
	IGNORE_TEST_RECORD_DEPENDENCIES,
	agent,
	company,
	component,
	employee,
	mark_submitted,
)


class TestWPSEmployeeExclusion(IntegrationTestCase):
	def test_exclusion_covering_month_is_an_omission(self):
		company_name = company()
		kept = employee(company_name, agent(), eligible=1)
		excluded = employee(company_name, agent(), eligible=1)
		basic = component("Earning", "Fixed")
		payroll = mark_submitted(
			"Payroll Entry",
			{
				"doctype": "Payroll Entry",
				"company": company_name,
				"posting_date": "2026-09-30",
				"start_date": "2026-09-01",
				"end_date": "2026-09-30",
				"currency": "AED",
				"exchange_rate": 1,
			},
		)
		for emp, amount in ((kept, 1000), (excluded, 900)):
			mark_submitted(
				"Salary Slip",
				{
					"doctype": "Salary Slip",
					"employee": emp,
					"employee_name": frappe.db.get_value("Employee", emp, "employee_name"),
					"company": company_name,
					"payroll_entry": payroll,
					"posting_date": "2026-09-30",
					"start_date": "2026-09-01",
					"end_date": "2026-09-30",
					"currency": "AED",
					"exchange_rate": 1,
					"payroll_frequency": "Monthly",
					"total_working_days": 30,
					"payment_days": 30,
					"gross_pay": amount,
					"net_pay": amount,
					"earnings": [{"salary_component": basic, "amount": amount}],
				},
			)
		exclusion = frappe.get_doc(
			{
				"doctype": "WPS Employee Exclusion",
				"employee": excluded,
				"company": company_name,
				"from_date": "2026-09-01",
				"to_date": "2026-09-30",
				"reason": "Dispute",
			}
		).insert()
		exclusion.submit()
		wps_file = frappe.get_doc(
			{
				"doctype": "WPS Salary File",
				"company": company_name,
				"payroll_entry": payroll,
				"salary_month": "2026-09-01",
				"currency": "AED",
			}
		).insert()
		wps_file.fetch_salary_slips()
		wps_file.reload()
		self.assertEqual(wps_file.total_employees, 1)
		self.assertEqual(wps_file.employees[0].employee, kept)
		self.assertEqual(len(wps_file.omissions), 1)
		self.assertEqual(wps_file.omissions[0].employee, excluded)
		self.assertEqual(wps_file.omissions[0].reason, "Dispute")
		self.assertEqual(flt(wps_file.wages_due), 1000)
