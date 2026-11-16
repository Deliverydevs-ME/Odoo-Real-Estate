from odoo import api, fields, models


class RealEstateContract(models.Model):
    _name = 're.contract'
    _description = "Real Estate Contract"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'contract_date desc, id desc'

    name = fields.Char(string="Contract No.", required=True, tracking=True, index=True)
    contract_type = fields.Selection(
        [('spa', "Sale & Purchase Agreement"), ('reservation', "Reservation / Booking Form"),
         ('agency', "Agency / Broker Agreement"), ('other', "Other")],
        default='spa', required=True)
    partner_id = fields.Many2one('res.partner', string="Customer", tracking=True, index=True)
    agency_id = fields.Many2one('res.partner', string="Agency", domain=[('re_partner_type', '=', 'agency')],
                                tracking=True, index=True)
    broker_id = fields.Many2one('res.partner', string="Broker", domain=[('re_partner_type', '=', 'broker')])
    project_id = fields.Many2one('re.project', string="Project", tracking=True)
    unit_id = fields.Many2one('re.unit', string="Unit", domain="[('project_id', '=', project_id)]")
    property_description = fields.Char(help="Property as written in the contract when no unit is matched.")
    contract_date = fields.Date(tracking=True)
    start_date = fields.Date()
    end_date = fields.Date(tracking=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id, required=True)
    contract_value = fields.Monetary(tracking=True)
    payment_terms = fields.Text(string="Payment Terms / Schedule")
    commission_details = fields.Text()
    license_details = fields.Text(string="License / Registration Info")
    notes = fields.Text()
    sale_order_id = fields.Many2one('sale.order', string="Sales Order")
    migration_line_id = fields.Many2one('re.migration.line', string="Imported From", readonly=True)
    attachment_count = fields.Integer(compute='_compute_attachment_count', string="Document Count")
    state = fields.Selection(
        [('active', "Active"), ('completed', "Completed"), ('expired', "Expired"), ('terminated', "Terminated")],
        compute='_compute_state', store=True, readonly=False, tracking=True)

    _sql_constraints = [
        ('name_company_uniq', 'UNIQUE(name, company_id)', "A contract with this number already exists."),
    ]

    @api.depends('end_date')
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for contract in self:
            if contract.state in ('completed', 'terminated'):
                continue
            contract.state = 'expired' if contract.end_date and contract.end_date < today else 'active'

    def _compute_attachment_count(self):
        data = self.env['ir.attachment']._read_group(
            [('res_model', '=', self._name), ('res_id', 'in', self.ids)], ['res_id'], ['__count'])
        counts = dict(data)
        for contract in self:
            contract.attachment_count = counts.get(contract.id, 0)

    def action_view_attachments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': 'ir.attachment',
            'view_mode': 'kanban,list,form',
            'domain': [('res_model', '=', self._name), ('res_id', '=', self.id)],
            'context': {'default_res_model': self._name, 'default_res_id': self.id},
        }
