# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, get_datetime

from uae_wps.payroll import load_component_rows
from uae_wps.uae_wps.doctype.wps_salary_file.test_helpers import (
	IGNORE_TEST_RECORD_DEPENDENCIES,
	agent as _agent,
	backdate_generated_at,
	company as _company,
	component as _component,
	draft_payroll as _draft_payroll,
	employee as _employee,
	empty_file as _empty_file,
	fetched_file as _fetched_file,
	file_format as _format,
	generate_sif as _generate_sif,
	mark_submitted as _mark_submitted,
	prepare_sif as _prepare_sif,
)


class TestWPSSalaryFile(IntegrationTestCase):
	def test_fetch_copies_one_submitted_slip(self):
		company = _company()
		agent = _agent()
		employee = _employee(company, agent, eligible=1)
		basic = _component("Earning", "Fixed")
		housing = _component("Earning", "Variable")
		loan = _component("Deduction", "Excluded")
		payroll = _mark_submitted(
			"Payroll Entry",
			{
				"doctype": "Payroll Entry",
				"company": company,
				"posting_date": "2026-01-31",
				"start_date": "2026-01-01",
				"end_date": "2026-01-31",
				"currency": "AED",
				"exchange_rate": 1,
			},
		)
		slip = _mark_submitted(
			"Salary Slip",
			{
				"doctype": "Salary Slip",
				"employee": employee,
				"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
				"company": company,
				"payroll_entry": payroll,
				"posting_date": "2026-01-31",
				"start_date": "2026-01-01",
				"end_date": "2026-01-31",
				"currency": "AED",
				"exchange_rate": 1,
				"payroll_frequency": "Monthly",
				"total_working_days": 31,
				"payment_days": 20,
				"leave_without_pay": 2,
				"gross_pay": 5500,
				"net_pay": 5400,
				"earnings": [
					{"salary_component": basic, "amount": 5000},
					{"salary_component": housing, "amount": 500},
				],
				"deductions": [{"salary_component": loan, "amount": 100}],
			},
		)

		wps_file = frappe.get_doc(
			{
				"doctype": "WPS Salary File",
				"company": company,
				"payroll_entry": payroll,
				"salary_month": "2026-01-01",
				"currency": "AED",
				"pay_earnings_total": 1,
			}
		).insert()
		result = wps_file.fetch_salary_slips()
		wps_file.reload()
		load_component_rows(wps_file)

		self.assertEqual(result["total_employees"], 1)
		self.assertEqual(flt(result["total_fixed_salary"]), 5000)
		self.assertEqual(flt(result["total_variable_salary"]), 500)
		self.assertEqual(flt(result["total_salary"]), 5500)
		self.assertEqual(wps_file.status, "Validated")
		self.assertEqual(str(wps_file.due_date), "2026-02-01")
		row = wps_file.employees[0]
		self.assertEqual(row.salary_slip, slip)
		self.assertEqual(row.mohre_person_id, "12345678901234")
		self.assertEqual(row.agent_routing_code, "302620122")
		self.assertEqual(row.employee_iban, "AE070331234567890123456")
		self.assertEqual(row.days_in_period, 31)
		self.assertEqual(flt(row.unpaid_leave_days), 2)
		self.assertEqual(flt(row.net_pay), 5400)
		self.assertEqual(
			[
				(component.salary_component, component.component_type, flt(component.amount))
				for component in row.salary_components
			],
			[(basic, "Fixed", 5000), (housing, "Variable", 500), (loan, "Excluded", 100)],
		)
		self.assertEqual(flt(frappe.db.get_value("Salary Slip", slip, "net_pay")), 5400)
		self.assertEqual(frappe.db.get_value("Salary Slip", slip, "docstatus"), 1)

	def test_fetch_leaves_out_ineligible_employee(self):
		company = _company()
		employee = _employee(company, None, eligible=0)
		payroll = _mark_submitted(
			"Payroll Entry",
			{
				"doctype": "Payroll Entry",
				"company": company,
				"posting_date": "2026-02-28",
				"start_date": "2026-02-01",
				"end_date": "2026-02-28",
				"currency": "AED",
				"exchange_rate": 1,
			},
		)
		_mark_submitted(
			"Salary Slip",
			{
				"doctype": "Salary Slip",
				"employee": employee,
				"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
				"company": company,
				"payroll_entry": payroll,
				"posting_date": "2026-02-28",
				"start_date": "2026-02-01",
				"end_date": "2026-02-28",
				"currency": "AED",
				"exchange_rate": 1,
				"total_working_days": 28,
				"payment_days": 28,
				"net_pay": 1000,
			},
		)
		wps_file = frappe.get_doc(
			{
				"doctype": "WPS Salary File",
				"company": company,
				"payroll_entry": payroll,
				"salary_month": "2026-02-01",
				"currency": "AED",
			}
		).insert()

		self.assertRaises(frappe.ValidationError, wps_file.fetch_salary_slips)
		self.assertFalse(frappe.db.exists("WPS Salary File Employee", {"parent": wps_file.name}))

	def test_fetch_rejects_draft_payroll(self):
		company = _company()
		payroll = frappe.get_doc(
			{
				"doctype": "Payroll Entry",
				"company": company,
				"posting_date": "2026-03-31",
				"start_date": "2026-03-01",
				"end_date": "2026-03-31",
				"currency": "AED",
				"exchange_rate": 1,
			}
		)
		payroll.flags.ignore_validate = True
		payroll.flags.ignore_mandatory = True
		payroll.insert(ignore_permissions=True, ignore_mandatory=True)
		wps_file = frappe.get_doc(
			{
				"doctype": "WPS Salary File",
				"company": company,
				"payroll_entry": payroll.name,
				"salary_month": "2026-03-01",
				"currency": "AED",
			}
		).insert()

		self.assertRaises(frappe.ValidationError, wps_file.fetch_salary_slips)

	def test_status_cannot_be_edited(self):
		company = _company()
		wps_file = _empty_file(company, _draft_payroll(company))
		wps_file.status = "Sent to Bank"
		self.assertRaises(frappe.ValidationError, wps_file.save)

	def test_fetch_rejects_generated_file(self):
		wps_file = _fetched_file()
		parent = wps_file.employees[0].name
		frappe.db.set_value("WPS Salary File", wps_file.name, "status", "Generated")
		wps_file.reload()
		self.assertRaises(frappe.ValidationError, wps_file.fetch_salary_slips)
		self.assertTrue(frappe.db.exists("WPS Salary Component Detail", {"parent": parent}))

	def test_delete_removes_component_rows(self):
		wps_file = _fetched_file()
		parent = wps_file.employees[0].name
		self.assertTrue(frappe.db.exists("WPS Salary Component Detail", {"parent": parent}))
		wps_file.delete()
		self.assertFalse(frappe.db.exists("WPS Salary Component Detail", {"parent": parent}))

	def test_fetch_does_not_take_another_files_rows(self):
		first = _fetched_file()
		second = _fetched_file()
		second.reload()
		victim = second.employees[0].name
		before = frappe.db.count("WPS Salary Component Detail", {"parent": victim})
		first.reload()
		first.employees[0].name = victim
		self.assertRaises(frappe.ValidationError, first.save)
		first.fetch_salary_slips()
		self.assertEqual(frappe.db.count("WPS Salary Component Detail", {"parent": victim}), before)

	def test_generate_sif_writes_edr_and_scr(self):
		wps_file = _prepare_sif(_fetched_file())
		result = _generate_sif(wps_file)
		wps_file.reload()
		created = get_datetime(wps_file.generated_at)

		self.assertEqual(wps_file.status, "Generated")
		self.assertEqual(wps_file.docstatus, 1)
		self.assertEqual(result["file_name"], wps_file.file_name)
		self.assertEqual(wps_file.file_name, f"1234567890123{created.strftime('%y%m%d%H%M%S')}.SIF")
		content = frappe.get_doc("File", {"file_url": wps_file.sif_file}).get_content()
		text = content.decode("ascii") if isinstance(content, bytes) else content
		lines = text.split("\r\n")
		self.assertEqual(lines[-1], "")
		edr, scr = lines[0].split(","), lines[1].split(",")
		self.assertEqual(
			edr,
			[
				"EDR",
				"12345678901234",
				"302620122",
				"AE070331234567890123456",
				"2026-05-01",
				"2026-05-31",
				"31",
				"1000.00",
				"0.00",
				"0",
			],
		)
		self.assertEqual(scr[0], "SCR")
		self.assertEqual(scr[1], "1234567890123")
		self.assertEqual(scr[2], "302620122")
		self.assertEqual(scr[3], created.strftime("%Y-%m-%d"))
		self.assertEqual(scr[4], created.strftime("%H%M"))
		self.assertEqual(scr[5], "052026")
		self.assertEqual(scr[6], "1")
		self.assertEqual(scr[7], "1000.00")
		self.assertEqual(scr[8], "AED")
		self.assertRaises(frappe.ValidationError, wps_file.generate_sif)

	def test_blank_category_follows_the_salary_slip(self):
		company = _company()
		employee = _employee(company, _agent(), eligible=1)
		basic = _component("Earning", "")
		bonus = _component("Earning", "")
		loan = _component("Deduction", "")
		payroll = _mark_submitted(
			"Payroll Entry",
			{
				"doctype": "Payroll Entry",
				"company": company,
				"posting_date": "2026-06-30",
				"start_date": "2026-06-01",
				"end_date": "2026-06-30",
				"currency": "AED",
				"exchange_rate": 1,
			},
		)
		_mark_submitted(
			"Salary Slip",
			{
				"doctype": "Salary Slip",
				"employee": employee,
				"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
				"company": company,
				"payroll_entry": payroll,
				"posting_date": "2026-06-30",
				"start_date": "2026-06-01",
				"end_date": "2026-06-30",
				"currency": "AED",
				"exchange_rate": 1,
				"payroll_frequency": "Monthly",
				"total_working_days": 30,
				"payment_days": 30,
				"gross_pay": 1500,
				"net_pay": 1400,
				"earnings": [
					{"salary_component": basic, "amount": 1000, "depends_on_payment_days": 1},
					{"salary_component": bonus, "amount": 500, "depends_on_payment_days": 0},
				],
				"deductions": [{"salary_component": loan, "amount": 100}],
			},
		)
		wps_file = frappe.get_doc(
			{
				"doctype": "WPS Salary File",
				"company": company,
				"payroll_entry": payroll,
				"salary_month": "2026-06-01",
				"currency": "AED",
			}
		).insert()
		wps_file.fetch_salary_slips()
		wps_file.reload()
		load_component_rows(wps_file)
		self.assertEqual(wps_file.total_fixed_salary, 1000)
		self.assertEqual(wps_file.total_variable_salary, 400)
		self.assertEqual(wps_file.total_salary, 1400)
		self.assertEqual(flt(wps_file.employees[0].credit_amount), 1400)
		by_component = {
			row.salary_component: (row.component_type, flt(row.amount))
			for row in wps_file.employees[0].salary_components
		}
		self.assertEqual(by_component[basic], ("Fixed", 1000))
		self.assertEqual(by_component[bonus], ("Variable", 400))
		self.assertEqual(by_component[loan], ("Excluded", 100))
		self.assertIn("payment days", wps_file.validation_summary)
		self.assertRaises(frappe.ValidationError, wps_file.submit)

	def test_joiner_shortens_pay_period(self):
		company = _company()
		employee = _employee(company, _agent(), eligible=1)
		frappe.db.set_value("Employee", employee, "date_of_joining", "2026-07-15")
		basic = _component("Earning", "Fixed")
		payroll = _mark_submitted(
			"Payroll Entry",
			{
				"doctype": "Payroll Entry",
				"company": company,
				"posting_date": "2026-07-31",
				"start_date": "2026-07-01",
				"end_date": "2026-07-31",
				"currency": "AED",
				"exchange_rate": 1,
			},
		)
		_mark_submitted(
			"Salary Slip",
			{
				"doctype": "Salary Slip",
				"employee": employee,
				"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
				"company": company,
				"payroll_entry": payroll,
				"posting_date": "2026-07-31",
				"start_date": "2026-07-01",
				"end_date": "2026-07-31",
				"currency": "AED",
				"exchange_rate": 1,
				"payroll_frequency": "Monthly",
				"total_working_days": 31,
				"payment_days": 17,
				"leave_without_pay": 2,
				"gross_pay": 1000,
				"net_pay": 1000,
				"earnings": [{"salary_component": basic, "amount": 1000}],
			},
		)
		wps_file = frappe.get_doc(
			{
				"doctype": "WPS Salary File",
				"company": company,
				"payroll_entry": payroll,
				"salary_month": "2026-07-01",
				"currency": "AED",
			}
		).insert()
		wps_file.fetch_salary_slips()
		wps_file.reload()
		row = wps_file.employees[0]
		self.assertEqual(str(row.pay_start_date), "2026-07-15")
		self.assertEqual(str(row.pay_end_date), "2026-07-31")
		self.assertEqual(row.days_in_period, 17)
		self.assertEqual(flt(row.unpaid_leave_days), 2)

	def test_ineligible_employee_is_an_omission(self):
		company = _company()
		eligible = _employee(company, _agent(), eligible=1)
		ineligible = _employee(company, None, eligible=0)
		basic = _component("Earning", "Fixed")
		payroll = _mark_submitted(
			"Payroll Entry",
			{
				"doctype": "Payroll Entry",
				"company": company,
				"posting_date": "2026-08-31",
				"start_date": "2026-08-01",
				"end_date": "2026-08-31",
				"currency": "AED",
				"exchange_rate": 1,
			},
		)
		for employee, amount in ((eligible, 1000), (ineligible, 800)):
			_mark_submitted(
				"Salary Slip",
				{
					"doctype": "Salary Slip",
					"employee": employee,
					"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
					"company": company,
					"payroll_entry": payroll,
					"posting_date": "2026-08-31",
					"start_date": "2026-08-01",
					"end_date": "2026-08-31",
					"currency": "AED",
					"exchange_rate": 1,
					"payroll_frequency": "Monthly",
					"total_working_days": 31,
					"payment_days": 31,
					"gross_pay": amount,
					"net_pay": amount,
					"earnings": [{"salary_component": basic, "amount": amount}],
				},
			)
		wps_file = frappe.get_doc(
			{
				"doctype": "WPS Salary File",
				"company": company,
				"payroll_entry": payroll,
				"salary_month": "2026-08-01",
				"currency": "AED",
			}
		).insert()
		wps_file.fetch_salary_slips()
		wps_file.reload()
		self.assertEqual(wps_file.total_employees, 1)
		self.assertEqual(len(wps_file.omissions), 1)
		self.assertEqual(wps_file.omissions[0].employee, ineligible)
		self.assertEqual(wps_file.omissions[0].reason, "Not WPS eligible")
		self.assertEqual(flt(wps_file.wages_due), 1000)
		self.assertEqual(flt(wps_file.percent_of_wages_due), 100)

	def test_generate_rejects_zero_salary(self):
		wps_file = _prepare_sif(_fetched_file())
		wps_file.employees[0].fixed_amount = 0
		wps_file.employees[0].variable_amount = 0
		wps_file.save()
		self.assertRaises(frappe.ValidationError, wps_file.generate_sif)
		wps_file.reload()
		self.assertEqual(wps_file.status, "Validated")
		self.assertFalse(wps_file.sif_file)

	def test_generate_refuses_variable_without_evp(self):
		wps_file = _prepare_sif(_fetched_file())
		wps_file.employees[0].fixed_amount = 600
		wps_file.employees[0].variable_amount = 400
		wps_file.employees[0].net_pay = 1000
		wps_file.save()
		with self.assertRaises(frappe.ValidationError) as raised:
			wps_file.generate_sif()
		self.assertIn("does not write an EVP record", str(raised.exception))

	def test_withheld_slip_is_an_omission(self):
		company = _company()
		paid = _employee(company, _agent(), eligible=1)
		withheld = _employee(company, _agent(), eligible=1)
		basic = _component("Earning", "Fixed")
		payroll = _mark_submitted(
			"Payroll Entry",
			{
				"doctype": "Payroll Entry",
				"company": company,
				"posting_date": "2026-10-31",
				"start_date": "2026-10-01",
				"end_date": "2026-10-31",
				"currency": "AED",
				"exchange_rate": 1,
			},
		)
		for employee, amount in ((paid, 1000), (withheld, 800)):
			slip = _mark_submitted(
				"Salary Slip",
				{
					"doctype": "Salary Slip",
					"employee": employee,
					"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
					"company": company,
					"payroll_entry": payroll,
					"posting_date": "2026-10-31",
					"start_date": "2026-10-01",
					"end_date": "2026-10-31",
					"currency": "AED",
					"exchange_rate": 1,
					"payroll_frequency": "Monthly",
					"total_working_days": 31,
					"payment_days": 31,
					"gross_pay": amount,
					"net_pay": amount,
					"earnings": [{"salary_component": basic, "amount": amount}],
				},
			)
			if employee == withheld:
				frappe.db.set_value("Salary Slip", slip, "salary_withholding", "WH-TEST")
		wps_file = frappe.get_doc(
			{
				"doctype": "WPS Salary File",
				"company": company,
				"payroll_entry": payroll,
				"salary_month": "2026-10-01",
				"currency": "AED",
			}
		).insert()
		wps_file.fetch_salary_slips()
		wps_file.reload()
		self.assertEqual(wps_file.total_employees, 1)
		self.assertEqual(wps_file.employees[0].employee, paid)
		self.assertEqual(len(wps_file.omissions), 1)
		self.assertEqual(wps_file.omissions[0].employee, withheld)
		self.assertEqual(wps_file.omissions[0].reason, "Salary Withholding")
		self.assertEqual(flt(wps_file.omissions[0].entitled_amount), 800)
		self.assertEqual(flt(wps_file.wages_due), 1800)
		self.assertEqual(flt(wps_file.wages_in_file), 1000)
		self.assertEqual(flt(wps_file.percent_of_wages_due), 55.56)

	def test_unpaid_until_accepted(self):
		wps_file = _prepare_sif(_fetched_file())
		_generate_sif(wps_file)
		wps_file.reload()
		self.assertEqual(wps_file.status, "Generated")
		self.assertIn("Unpaid", wps_file.payment_status)
		wps_file.mark_as_sent_to_bank()
		wps_file.reload()
		self.assertEqual(wps_file.status, "Sent to Bank")
		self.assertIn("Unpaid", wps_file.payment_status)

	def test_rename_status_patch(self):
		from uae_wps.patches.v1_0.rename_salary_file_statuses import execute

		wps_file = _fetched_file()
		frappe.db.set_value("WPS Salary File", wps_file.name, "status", "Submitted")
		other = _fetched_file()
		frappe.db.set_value("WPS Salary File", other.name, "status", "Downloaded")
		execute()
		self.assertEqual(frappe.db.get_value("WPS Salary File", wps_file.name, "status"), "Sent to Bank")
		self.assertEqual(frappe.db.get_value("WPS Salary File", other.name, "status"), "Generated")


	def test_leaver_shortens_pay_period(self):
		company_name = _company()
		emp = _employee(company_name, _agent(), eligible=1)
		frappe.db.set_value("Employee", emp, "relieving_date", "2026-07-20")
		basic = _component("Earning", "Fixed")
		payroll = _mark_submitted(
			"Payroll Entry",
			{
				"doctype": "Payroll Entry",
				"company": company_name,
				"posting_date": "2026-07-31",
				"start_date": "2026-07-01",
				"end_date": "2026-07-31",
				"currency": "AED",
				"exchange_rate": 1,
			},
		)
		_mark_submitted(
			"Salary Slip",
			{
				"doctype": "Salary Slip",
				"employee": emp,
				"employee_name": frappe.db.get_value("Employee", emp, "employee_name"),
				"company": company_name,
				"payroll_entry": payroll,
				"posting_date": "2026-07-31",
				"start_date": "2026-07-01",
				"end_date": "2026-07-31",
				"currency": "AED",
				"exchange_rate": 1,
				"payroll_frequency": "Monthly",
				"total_working_days": 31,
				"payment_days": 20,
				"leave_without_pay": 1,
				"gross_pay": 1000,
				"net_pay": 1000,
				"earnings": [{"salary_component": basic, "amount": 1000}],
			},
		)
		wps_file = frappe.get_doc(
			{
				"doctype": "WPS Salary File",
				"company": company_name,
				"payroll_entry": payroll,
				"salary_month": "2026-07-01",
				"currency": "AED",
			}
		).insert()
		wps_file.fetch_salary_slips()
		wps_file.reload()
		row = wps_file.employees[0]
		self.assertEqual(str(row.pay_start_date), "2026-07-01")
		self.assertEqual(str(row.pay_end_date), "2026-07-20")
		self.assertEqual(row.days_in_period, 20)
		self.assertEqual(flt(row.unpaid_leave_days), 1)

	def test_pay_earnings_total_keeps_gap(self):
		company_name = _company()
		emp = _employee(company_name, _agent(), eligible=1)
		basic = _component("Earning", "Fixed")
		bonus = _component("Earning", "Variable")
		loan = _component("Deduction", "Excluded")
		payroll = _mark_submitted(
			"Payroll Entry",
			{
				"doctype": "Payroll Entry",
				"company": company_name,
				"posting_date": "2026-06-30",
				"start_date": "2026-06-01",
				"end_date": "2026-06-30",
				"currency": "AED",
				"exchange_rate": 1,
			},
		)
		_mark_submitted(
			"Salary Slip",
			{
				"doctype": "Salary Slip",
				"employee": emp,
				"employee_name": frappe.db.get_value("Employee", emp, "employee_name"),
				"company": company_name,
				"payroll_entry": payroll,
				"posting_date": "2026-06-30",
				"start_date": "2026-06-01",
				"end_date": "2026-06-30",
				"currency": "AED",
				"exchange_rate": 1,
				"payroll_frequency": "Monthly",
				"total_working_days": 30,
				"payment_days": 30,
				"gross_pay": 1500,
				"net_pay": 1400,
				"earnings": [
					{"salary_component": basic, "amount": 1000},
					{"salary_component": bonus, "amount": 500},
				],
				"deductions": [{"salary_component": loan, "amount": 100}],
			},
		)
		wps_file = frappe.get_doc(
			{
				"doctype": "WPS Salary File",
				"company": company_name,
				"payroll_entry": payroll,
				"salary_month": "2026-06-01",
				"currency": "AED",
				"pay_earnings_total": 1,
			}
		).insert()
		wps_file.fetch_salary_slips()
		wps_file.reload()
		self.assertEqual(flt(wps_file.total_variable_salary), 500)
		self.assertEqual(flt(wps_file.total_salary), 1500)
		self.assertIn("1400", wps_file.validation_summary)
		self.assertIn("1500", wps_file.validation_summary)
		self.assertIn("Pay Earnings Total", wps_file.validation_summary)

	def test_nak_cancel_amend_requires_refetch(self):
		wps_file = _prepare_sif(_fetched_file())
		_generate_sif(wps_file)
		wps_file.reload()
		backdate_generated_at(wps_file.name)
		response = (
			frappe.get_doc(
				{
					"doctype": "WPS Bank Response",
					"wps_salary_file": wps_file.name,
					"response": "NAK",
					"received_on": "2026-06-02",
				}
			)
			.insert()
		)
		response.submit()
		wps_file.reload()
		self.assertEqual(wps_file.status, "Rejected")
		response.cancel()
		wps_file.reload()
		wps_file.cancel()
		amended = frappe.copy_doc(wps_file)
		amended.amended_from = wps_file.name
		amended.docstatus = 0
		amended.status = "Draft"
		amended.sif_file = None
		amended.file_name = None
		amended.file_hash = None
		amended.bank_response = None
		amended.ack_file_name = None
		amended.accepted_on = None
		amended.set("employees", [])
		amended.set("omissions", [])
		amended.insert()
		self.assertEqual(amended.status, "Draft")
		self.assertFalse(amended.employees)
		amended.fetch_salary_slips()
		amended.reload()
		self.assertEqual(amended.status, "Validated")
		self.assertEqual(amended.total_employees, 1)
