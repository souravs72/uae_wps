# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, get_datetime

from uae_wps.payroll import load_component_rows

# This site's Item master has a mandatory UAE field, so ERPNext's Company and
# Item test records cannot be imported. The test builds its own HRMS rows.
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Company",
	"Payroll Entry",
	"Currency",
	"Bank Account",
	"User",
	"Employee",
	"Salary Slip",
	"Salary Component",
]


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
			}
		).insert()
		result = wps_file.fetch_salary_slips()
		wps_file.reload()
		load_component_rows(wps_file)

		self.assertEqual(result["total_employees"], 1)
		self.assertEqual(flt(result["total_fixed_salary"]), 5000)
		self.assertEqual(flt(result["total_variable_salary"]), 500)
		self.assertEqual(flt(result["total_salary"]), 5500)
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
		employee = _employee(company, agent=None, eligible=0)
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
		wps_file.status = "Submitted"
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
		result = wps_file.generate_sif()
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
				"net_pay": 1500,
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
		self.assertEqual(wps_file.total_variable_salary, 500)
		self.assertEqual(wps_file.total_salary, 1500)
		by_component = {row.salary_component: row.component_type for row in wps_file.employees[0].salary_components}
		self.assertEqual(by_component[basic], "Fixed")
		self.assertEqual(by_component[bonus], "Variable")
		self.assertEqual(by_component[loan], "Excluded")
		self.assertIn("payment days", wps_file.validation_summary)
		self.assertRaises(frappe.ValidationError, wps_file.submit)

	def test_generate_rejects_zero_salary(self):
		wps_file = _prepare_sif(_fetched_file())
		wps_file.employees[0].fixed_amount = 0
		wps_file.employees[0].variable_amount = 0
		wps_file.save()
		self.assertRaises(frappe.ValidationError, wps_file.generate_sif)
		wps_file.reload()
		self.assertEqual(wps_file.status, "Draft")
		self.assertFalse(wps_file.sif_file)


def _company() -> str:
	company = frappe.db.get_value("Company", {}, "name")
	if not company:
		if not frappe.db.exists("Currency", "AED"):
			frappe.get_doc({"doctype": "Currency", "currency_name": "AED", "enabled": 1}).insert(
				ignore_permissions=True
			)
		company = (
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": "WPS Test Company",
					"abbr": "WTC",
					"default_currency": "AED",
					"country": "United Arab Emirates",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
	frappe.db.set_value("Company", company, "wps_enabled", 1)
	return company


def _agent() -> str:
	name = "WPS Fetch Test Agent"
	if frappe.db.exists("WPS Agent", name):
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "WPS Agent",
				"agent_name": name,
				"agent_type": "Bank",
				"routing_code": "302620122",
			}
		)
		.insert()
		.name
	)


def _employee(company: str, agent: str | None, eligible: int) -> str:
	gender = frappe.db.get_value("Gender", {}, "name")
	if not gender:
		gender = frappe.get_doc({"doctype": "Gender", "gender": "Male"}).insert(ignore_permissions=True).name
	employee = frappe.get_doc(
		{
			"doctype": "Employee",
			"naming_series": "EMP-",
			"first_name": "WPS",
			"last_name": frappe.generate_hash(length=6),
			"company": company,
			"status": "Active",
			"gender": gender,
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"wps_eligible": eligible,
			"wps_agent": agent,
			"mohre_person_id": "12345678901234",
			"iban": "AE070331234567890123456",
			"bank_ac_no": "1234567890123456",
		}
	)
	employee.insert(ignore_permissions=True)
	return employee.name


def _prepare_sif(wps_file):
	frappe.db.set_value("Company", wps_file.company, "mohre_establishment_id", "1234567890123")
	agent = _agent()
	wps_file.wps_agent = agent
	wps_file.wps_file_format = _format(agent)
	wps_file.currency = "AED"
	wps_file.save()
	frappe.db.set_value("WPS Salary File", wps_file.name, "currency", "AED")
	wps_file.reload()
	return wps_file


def _format(agent: str) -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "WPS File Format",
				"format_name": f"WPS Test {frappe.generate_hash(length=6)}",
				"wps_agent": agent,
				"is_active": 1,
			}
		)
		.insert()
		.name
	)


def _component(component_type: str, category: str | None) -> str:
	suffix = frappe.generate_hash(length=6)
	name = f"WPS {component_type} {suffix}"
	return (
		frappe.get_doc(
			{
				"doctype": "Salary Component",
				"salary_component": name,
				"salary_component_abbr": suffix,
				"type": component_type,
				"wps_salary_category": category,
			}
		)
		.insert()
		.name
	)


def _empty_file(company: str, payroll: str):
	return frappe.get_doc(
		{
			"doctype": "WPS Salary File",
			"company": company,
			"payroll_entry": payroll,
			"salary_month": "2026-04-01",
			"currency": "AED",
		}
	).insert()


def _draft_payroll(company: str) -> str:
	payroll = frappe.get_doc(
		{
			"doctype": "Payroll Entry",
			"company": company,
			"posting_date": "2026-04-30",
			"start_date": "2026-04-01",
			"end_date": "2026-04-30",
			"currency": "AED",
			"exchange_rate": 1,
		}
	)
	payroll.flags.ignore_validate = True
	payroll.flags.ignore_mandatory = True
	payroll.insert(ignore_permissions=True, ignore_mandatory=True)
	return payroll.name


def _fetched_file():
	company = _company()
	employee = _employee(company, _agent(), eligible=1)
	basic = _component("Earning", "Fixed")
	payroll = _mark_submitted(
		"Payroll Entry",
		{
			"doctype": "Payroll Entry",
			"company": company,
			"posting_date": "2026-05-31",
			"start_date": "2026-05-01",
			"end_date": "2026-05-31",
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
			"posting_date": "2026-05-31",
			"start_date": "2026-05-01",
			"end_date": "2026-05-31",
			"currency": "AED",
			"exchange_rate": 1,
			"payroll_frequency": "Monthly",
			"total_working_days": 31,
			"payment_days": 31,
			"net_pay": 1000,
			"earnings": [{"salary_component": basic, "amount": 1000}],
		},
	)
	wps_file = frappe.get_doc(
		{
			"doctype": "WPS Salary File",
			"company": company,
			"payroll_entry": payroll,
			"salary_month": "2026-05-01",
			"currency": "AED",
		}
	).insert()
	wps_file.fetch_salary_slips()
	wps_file.reload()
	return wps_file


def _mark_submitted(doctype: str, values: dict) -> str:
	doc = frappe.get_doc(values)
	doc.flags.ignore_validate = True
	doc.flags.ignore_mandatory = True
	doc.flags.ignore_links = True
	doc.insert(ignore_permissions=True, ignore_mandatory=True)
	frappe.db.set_value(doctype, doc.name, "docstatus", 1, update_modified=False)
	return doc.name
