# Odoo Real Estate – Odoo 19 ERP/CRM

Custom Odoo 19 Enterprise module `odoo_real_estate` for off-plan real estate developers (UAE).

## Features

| Area | What it does |
|---|---|
| Agencies & brokers | One profile per agency. Duplicates are blocked on name (legal suffixes ignored), Trade License, ORN and BRN, and similar names get a merge warning. Brokers are linked to their agency. |
| Registration documents | Trade License, ORN, BRN, MOA, IBAN letter, KYC and Emirates ID, each with files and expiry dates. A **Documents** smart button on agencies and brokers shows them. |
| Renewals | A daily job sends reminders to the RM (60/30/7 days before expiry by default). The registration is suspended or cancelled automatically when a required document expires, and reactivated when it is renewed. |
| Lead routing | The agency is found from the lead's contact or email domain, and the lead is assigned to the agency's Relationship Manager / territory. Broker and investor leads go to dedicated teams. |
| Projects & units | Off-plan projects and their units (building, floor, type, area, price). A unit is Booked on SO confirmation and Sold when the down payment is received. |
| Sales orders | Selecting a unit on the order line fills the product, price and unit details. |
| Payment plans | Any down payment %, any number of installments, interval, handover or post-handover milestones. The schedule can be edited per customer and rebalanced. |
| Collections | Installment invoices are created automatically, customers get due and overdue reminders, and payment status is tracked. |
| Commissions | Agency agreements with a validity period. Commission is earned on sale, pro-rata to collection, or after a collection threshold. It is blocked for inactive registrations. Vendor bills are created from commissions. |
| Contracts & migration | Contract repository, plus bulk OCR/AI migration of existing contracts and licenses: extract → validate → duplicate check → import, with the original file attached. |

## Installation

1. Copy `odoo_real_estate/` into a custom addons folder, e.g. `C:\odoo_custom_addons\`.
2. Add that folder to `addons_path` in `odoo.conf` (see `odoo.conf.example`).
3. Restart the Odoo service, then go to **Apps → Update Apps List** and install **Odoo Real Estate**.

Dependencies: `contacts`, `crm`, `sale_crm`, `sale_management`, `account`, `mail` (Odoo 19 Enterprise).

### Optional: AI extraction for scanned contracts

Go to **Real Estate → Configuration → Settings → Contract Migration** and choose the provider (Anthropic Claude, OpenAI or Google Gemini) and enter the API key.
For Claude, install the SDK in Odoo's Python:

```
"C:\Program Files\Odoo 19.0e.20260820\python\python.exe" -m pip install anthropic
```

Digital (text) PDFs such as DED licenses are read without AI.

## Configuration after install

- **Configuration → Territories**: territories and their Territory Manager / RM.
- **Configuration → Settings**: broker/investor lead teams, expiry rule, installment invoicing and reminder days, AI provider.
- **Configuration → Payment Plans / Commission Plans / Document Types**: adjust the templates.
