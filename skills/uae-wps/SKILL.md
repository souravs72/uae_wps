---
name: uae-wps
description: Prepare a UAE WPS Salary File from a submitted HRMS Payroll Entry. Use when the user asks about WPS, a salary file, SIF, MOHRE, or fetching slips. Do not use it to calculate payroll or invent a bank file.
---

# UAE WPS

Wage Protection System work in this app is a snapshot of a submitted HRMS payroll. The app does not calculate pay and does not pay the bank.

## What you may do

- Read Company, Employee, Salary Component, Bank Account, WPS Agent, WPS File Format, Payroll Entry, Salary Slip, and WPS Salary File with the read tools. Check the real field names first.
- Create a WPS Salary File when the user asks: Company, a submitted Payroll Entry, and the salary month. Leave the totals empty. Fetch fills them.
- Refresh that file with `fetch_wps_salary_slips` when you are the UAE WPS agent. Otherwise call the document method `fetch_salary_slips` through `run_action` after `describe` shows it. Both paths run the same code. Fetch asks the user to approve because it replaces the snapshot.
- Read a file with `wps_file_summary` or `read_records`. Summaries omit bank account numbers. Open the document when the user asks for an employee's row.

## What you must not do

- Do not change a Salary Slip, Payroll Entry, or Salary Component.
- Do not invent Fixed, Variable, or Excluded. When WPS Salary Category is blank, fetch follows the salary slip: an earning prorated by payment days is Fixed, an Additional Salary or an earning that is not prorated is Variable, and a row kept out of the slip total, or any deduction, is Excluded. An explicit category replaces that. The note says which one was used.
- Do not add an earnings or deduction amount that the fetch did not copy. Fixed and variable credit totals come only from earnings. Deductions are stored and are not added to those totals.
- Do not treat a mismatch between fixed plus variable and net pay as a failure. It is a note.
- Do not invent or paste SIF lines. Call `generate_sif` on the saved WPS Salary File, or `generate_wps_sif` when you are the UAE WPS agent. Both write the Ministry of Labour file: EDR, EVP only when the selected format supports it, then SCR. If generation refuses, report the message and stop.
- Do not guess a bank routing code. It lives on WPS Agent and is nine digits.
- Do not fetch a file whose status is Generated, Downloaded, Submitted, Accepted, or Rejected.

## Before a fetch can succeed

The Company has WPS enabled. The Payroll Entry and its Salary Slips are submitted. An employee is included only when WPS Eligible is checked. The file is Draft or Validated.

Employer bank account, WPS Agent, and WPS File Format may be empty on a draft. Fetch fills the bank account from the Payroll Entry when the file does not already have one, and the agent from that bank account when the file does not already have one.
