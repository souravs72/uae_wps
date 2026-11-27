# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Rename legacy status values on existing WPS Salary File rows."""
	frappe.db.sql(
		"""
		UPDATE `tabWPS Salary File`
		SET status = 'Sent to Bank'
		WHERE status = 'Submitted'
		"""
	)
	frappe.db.sql(
		"""
		UPDATE `tabWPS Salary File`
		SET status = 'Generated'
		WHERE status = 'Downloaded'
		"""
	)
