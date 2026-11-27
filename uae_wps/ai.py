# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

"""Flow tools and the UAE WPS agent.

Flow may read a salary file and ask the existing fetch to copy submitted slips.
It does not classify pay, recalculate a slip, or write a SIF file.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.flow import Agent, ToolContext, tool
from frappe.utils import cint, flt

INSTRUCTIONS = """You prepare UAE WPS salary files from HRMS. You do not run payroll.

Before the first WPS action in a conversation, load the skill uae_wps/uae-wps when it is available.

Use wps_file_summary to read one file. Use fetch_wps_salary_slips to copy submitted Salary Slips
into a Draft or Validated file. Use generate_wps_sif to write the SIF file from that snapshot.
All three tools call the app's own code. Do not recompute amounts, guess a Fixed or Variable
category, change a Salary Slip, or type SIF lines yourself. If generation refuses, repeat its message."""


@tool(requires_confirmation=False)
def wps_file_summary(wps_salary_file: str, *, ctx: ToolContext) -> dict:
	"""Read totals and notes for one WPS Salary File.

	Returns status, the linked Payroll Entry, employee count, fixed and variable totals, and notes.
	Does not change the file and does not include bank account numbers.
	"""
	if ctx.user and ctx.user != frappe.session.user:
		frappe.throw(_("This tool runs as the signed-in user."))
	name = (wps_salary_file or "").strip()
	if not name:
		frappe.throw(_("Name the WPS Salary File to read."))
	doc = frappe.get_doc("WPS Salary File", name)
	doc.check_permission("read")
	return {
		"name": doc.name,
		"status": doc.status,
		"company": doc.company,
		"payroll_entry": doc.payroll_entry,
		"salary_month": str(doc.salary_month) if doc.salary_month else None,
		"currency": doc.currency,
		"employee_count": cint(doc.total_employees),
		"total_fixed_salary": flt(doc.total_fixed_salary),
		"total_variable_salary": flt(doc.total_variable_salary),
		"total_salary": flt(doc.total_salary),
		"notes": doc.validation_summary or "",
	}


@tool(
	requires_confirmation=True,
	display_template="Fetching salary slips into {{ wps_salary_file }}",
)
def fetch_wps_salary_slips(wps_salary_file: str, *, ctx: ToolContext) -> dict:
	"""Copy submitted HRMS Salary Slips into a WPS Salary File.

	Replaces that file's employee snapshot with the amounts already on the slips.
	Does not recalculate pay, change the slips, or write a SIF file.
	The caller must be allowed to write the file. Generated and later files are refused.
	"""
	if ctx.user and ctx.user != frappe.session.user:
		frappe.throw(_("This tool runs as the signed-in user."))
	if ctx.read_only:
		frappe.throw(_("Fetching salary slips writes the WPS Salary File, so it cannot run as a read."))
	name = (wps_salary_file or "").strip()
	if not name:
		frappe.throw(_("Name the WPS Salary File to fetch into."))
	doc = frappe.get_doc("WPS Salary File", name)
	return doc.fetch_salary_slips()


@tool(
	requires_confirmation=True,
	display_template="Generating the SIF file for {{ wps_salary_file }}",
)
def generate_wps_sif(wps_salary_file: str, *, ctx: ToolContext) -> dict:
	"""Write the UAE WPS SIF file for one salary file.

	Uses the saved snapshot. Does not change payroll documents or invent salary amounts.
	The caller must be allowed to write the file. A generated file is refused.
	"""
	if ctx.user and ctx.user != frappe.session.user:
		frappe.throw(_("This tool runs as the signed-in user."))
	if ctx.read_only:
		frappe.throw(_("Generating a SIF file writes the WPS Salary File, so it cannot run as a read."))
	name = (wps_salary_file or "").strip()
	if not name:
		frappe.throw(_("Name the WPS Salary File to generate."))
	doc = frappe.get_doc("WPS Salary File", name)
	return doc.generate_sif()


def agents() -> list[Agent]:
	"""The WPS agent, once the site has an enabled Flow Model.

	The desk can switch that model later. Sync leaves a model the site already chose.
	"""
	model = frappe.db.get_value("Flow Model", {"enabled": 1}, "name")
	if not model:
		return []
	skills = ["uae-wps"] if frappe.db.exists("Flow Skill", "uae_wps/uae-wps") else []
	return [
		Agent(
			name="UAE WPS",
			model=model,
			description="Reads a WPS Salary File, fetches submitted HRMS slips, and writes the SIF file.",
			instructions=INSTRUCTIONS,
			tools=[wps_file_summary, fetch_wps_salary_slips, generate_wps_sif],
			skills=skills,
		)
	]
