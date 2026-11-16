{
    'name': 'Odoo DD Real Estate',
    'version': '19.0.1.0.0',
    'category': 'Sales/CRM',
    'summary': 'Agency/broker registry, lead routing, off-plan projects and flexible payment plans',
    'description': """
Odoo Real Estate - ERP/CRM for off-plan developers
==================================================
* Agency / broker registry with duplicate prevention (name, Trade License, ORN, BRN)
* Registration documents (Trade License, ORN, BRN, MOA, IBAN, KYC) with expiry tracking
* Automated renewal reminders and auto-suspension of expired registrations
* Territories and automatic lead assignment to the agency Relationship Manager
* Off-plan projects and units
* Flexible payment plans (any down payment %, any number of installments, handover payments)
* Automatic installment invoicing, customer reminders and payment status tracking
* Agency commission agreements, plans and vendor bills
* Contract repository and bulk OCR / AI migration of existing contracts and licenses
    """,
    "author": "Deliverydevs",
    "website": "https://deliverydevs.com",
    "license": "LGPL-3",
    'depends': [
        'contacts',
        'crm',
        'sale_crm',
        'sale_management',
        'account',
        'mail',
    ],
    'images': ['static/description/banner.gif'],
    'data': [
        'security/ir.model.access.csv',
        'data/document_type_data.xml',
        'data/payment_plan_data.xml',
        'data/mail_template_data.xml',
        'data/ir_cron_data.xml',
        'data/commission_data.xml',
        'views/territory_views.xml',
        'views/document_views.xml',
        'views/res_partner_views.xml',
        'views/crm_lead_views.xml',
        'views/project_views.xml',
        'views/payment_plan_views.xml',
        'views/installment_views.xml',
        'views/sale_order_views.xml',
        'views/commission_views.xml',
        'views/contract_views.xml',
        'views/migration_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'OEEL-1',
    "icon": "static/description/icon.png",
}
