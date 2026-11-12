from odoo import _, api, fields, models
from odoo.exceptions import UserError


class RealEstateProject(models.Model):
    _name = 're.project'
    _description = "Real Estate Project"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, name'

    name = fields.Char(required=True, tracking=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    location = fields.Char()
    description = fields.Html()
    image_1920 = fields.Image(max_width=1920, max_height=1920)
    state = fields.Selection(
        [('upcoming', "Upcoming"), ('launched', "Launched / Selling"), ('sold_out', "Sold Out"),
         ('completed', "Completed")],
        default='upcoming', required=True, tracking=True)
    launch_date = fields.Date()
    handover_date = fields.Date(string="Expected Handover", tracking=True,
                                help="Used as due date for 'On Handover' payment plan milestones.")
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    product_id = fields.Many2one(
        'product.product', string="Sale Product", readonly=True, copy=False,
        help="Product used on quotations / sales orders of this project's units.")
    payment_plan_ids = fields.Many2many('re.payment.plan', string="Payment Plans")
    default_payment_plan_id = fields.Many2one('re.payment.plan', string="Default Payment Plan")
    unit_ids = fields.One2many('re.unit', 'project_id', string="Units")
    unit_count = fields.Integer(compute='_compute_unit_stats')
    available_unit_count = fields.Integer(compute='_compute_unit_stats')
    sold_unit_count = fields.Integer(compute='_compute_unit_stats')
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('UNIQUE(code, company_id)', "The project code must be unique.")

    @api.depends('unit_ids.state')
    def _compute_unit_stats(self):
        for project in self:
            project.unit_count = len(project.unit_ids)
            project.available_unit_count = len(project.unit_ids.filtered(lambda u: u.state == 'available'))
            project.sold_unit_count = len(project.unit_ids.filtered(lambda u: u.state in ('booked', 'sold')))

    @api.model_create_multi
    def create(self, vals_list):
        projects = super().create(vals_list)
        for project in projects:
            project.product_id = self.env['product.product'].create({
                'name': _("Property Unit - %s", project.name),
                'type': 'service',
                'invoice_policy': 'order',
                'sale_ok': True,
                'purchase_ok': False,
                'company_id': project.company_id.id,
                # Off-plan residential first sales are VAT exempt; add taxes on the product if needed
                'taxes_id': [fields.Command.clear()],
            })
        return projects

    def write(self, vals):
        res = super().write(vals)
        if 'name' in vals:
            for project in self.filtered('product_id'):
                project.product_id.name = _("Property Unit - %s", project.name)
        return res

    def action_view_units(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Units"),
            'res_model': 're.unit',
            'view_mode': 'list,kanban,form',
            'domain': [('project_id', '=', self.id)],
            'context': {'default_project_id': self.id},
        }


class RealEstateUnit(models.Model):
    _name = 're.unit'
    _description = "Property Unit"
    _inherit = ['mail.thread']
    _order = 'project_id, building, name'

    name = fields.Char(string="Unit No.", required=True, tracking=True)
    project_id = fields.Many2one('re.project', required=True, ondelete='restrict', index=True)
    building = fields.Char(string="Building / Tower")
    floor = fields.Char()
    unit_type = fields.Selection(
        [('studio', "Studio"), ('1br', "1 Bedroom"), ('2br', "2 Bedrooms"), ('3br', "3 Bedrooms"),
         ('4br', "4+ Bedrooms"), ('penthouse', "Penthouse"), ('townhouse', "Townhouse"), ('villa', "Villa"),
         ('plot', "Plot"), ('retail', "Retail"), ('office', "Office")],
        string="Type")
    area_sqft = fields.Float(string="Area (sq.ft)")
    view = fields.Char()
    company_id = fields.Many2one(related='project_id.company_id', store=True)
    currency_id = fields.Many2one(related='project_id.currency_id')
    price = fields.Monetary(tracking=True)
    price_per_sqft = fields.Monetary(compute='_compute_price_per_sqft')
    state = fields.Selection(
        [('available', "Available"), ('reserved', "Reserved"), ('booked', "Booked"), ('sold', "Sold"),
         ('blocked', "Blocked")],
        default='available', required=True, tracking=True, index=True)
    sale_order_id = fields.Many2one('sale.order', string="Sales Order", readonly=True, copy=False)
    partner_id = fields.Many2one(related='sale_order_id.partner_id', string="Buyer")
    active = fields.Boolean(default=True)

    _name_project_uniq = models.Constraint(
        'UNIQUE NULLS NOT DISTINCT (project_id, building, name)', "This unit already exists in the project.")

    @api.depends('price', 'area_sqft')
    def _compute_price_per_sqft(self):
        for unit in self:
            unit.price_per_sqft = unit.price / unit.area_sqft if unit.area_sqft else 0.0

    @api.depends('name', 'building', 'project_id')
    def _compute_display_name(self):
        for unit in self:
            parts = [unit.project_id.name, unit.building, unit.name]
            unit.display_name = ' / '.join(p for p in parts if p)

    def action_block(self):
        if self.filtered(lambda u: u.state in ('booked', 'sold')):
            raise UserError(_("Booked or sold units cannot be blocked."))
        self.state = 'blocked'

    def action_release(self):
        if self.filtered(lambda u: u.state == 'sold'):
            raise UserError(_("Sold units cannot be released."))
        self.write({'state': 'available', 'sale_order_id': False})
