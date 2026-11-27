# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe
from frappe.flow import ToolContext
from frappe.flow.doctype.flow_skill.flow_skill import sync_skills
from frappe.flow.lib.agent import sync_agents, sync_tools
from frappe.tests import IntegrationTestCase

from uae_wps.ai import agents, fetch_wps_salary_slips


class TestFlow(IntegrationTestCase):
	def test_tools_and_skill_sync_locked_to_the_app(self):
		sync_tools()
		synced = sync_skills()
		self.assertIn("uae_wps/uae-wps", synced)

		summary = frappe.db.get_value(
			"Flow Tool",
			"uae_wps/wps_file_summary",
			["type", "requires_confirmation", "is_system_generated"],
			as_dict=True,
		)
		fetch = frappe.db.get_value(
			"Flow Tool",
			"uae_wps/fetch_wps_salary_slips",
			["type", "requires_confirmation", "is_system_generated", "import_path"],
			as_dict=True,
		)
		self.assertEqual(
			(summary.type, summary.requires_confirmation, summary.is_system_generated),
			("Imported", 0, 1),
		)
		self.assertEqual(
			(fetch.type, fetch.requires_confirmation, fetch.is_system_generated, fetch.import_path),
			("Imported", 1, 1, "uae_wps.ai.fetch_wps_salary_slips"),
		)
		skill = frappe.get_doc("Flow Skill", "uae_wps/uae-wps")
		self.assertEqual(skill.app, "uae_wps")
		self.assertEqual(skill.is_system_generated, 1)
		self.assertIn("fetch_wps_salary_slips", skill.instructions)

	def test_fetch_refuses_a_read_only_call(self):
		ctx = ToolContext(session_id="test", agent="uae_wps/UAE WPS", user="Administrator", read_only=True)
		with self.assertRaises(frappe.ValidationError):
			fetch_wps_salary_slips(wps_salary_file="WPS-TEST", ctx=ctx)

	def test_agent_is_declared_only_when_a_model_is_enabled(self):
		declared = agents()
		if not frappe.db.get_value("Flow Model", {"enabled": 1}, "name"):
			self.assertEqual(declared, [])
			return
		self.assertEqual([agent.name for agent in declared], ["uae_wps/UAE WPS"])
		self.assertEqual(
			[tool.name for tool in declared[0].tools],
			["wps_file_summary", "fetch_wps_salary_slips"],
		)
		sync_skills()
		sync_agents()
		doc = frappe.get_doc("Flow Agent", "uae_wps/UAE WPS")
		self.assertEqual(doc.is_system_generated, 1)
		self.assertIn("uae_wps/fetch_wps_salary_slips", [row.tool for row in doc.tools])
		self.assertIn("uae_wps/uae-wps", [row.skill for row in doc.skills])
