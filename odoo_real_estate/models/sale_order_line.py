from odoo import _, api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    re_unit_id = fields.Many2one(
        're.unit', string="Property Unit", index=True, copy=False,
        domain="[('state', 'in', ('available', 'reserved')), ('project_id.product_id', '=?', product_id)]")
    re_project_id = fields.Many2one(related='re_unit_id.project_id', string="Project")
    re_building = fields.Char(related='re_unit_id.building', string="Building / Tower")
    re_floor = fields.Char(related='re_unit_id.floor', string="Floor")
    re_unit_type = fields.Selection(related='re_unit_id.unit_type', string="Unit Type")
    re_area_sqft = fields.Float(related='re_unit_id.area_sqft', string="Area (sq.ft)")
    re_is_property = fields.Boolean(compute='_compute_re_is_property')

    @api.depends('product_id')
    def _compute_re_is_property(self):
        projects = self.env['re.project'].with_context(active_test=False).search(
            [('product_id', 'in', self.product_id.ids)])
        products = projects.product_id
        for line in self:
            line.re_is_property = line.product_id in products

    @api.onchange('re_unit_id')
    def _onchange_re_unit_id(self):
        unit = self.re_unit_id
        if unit and self.product_id != unit.project_id.product_id:
            self.product_id = unit.project_id.product_id

    @api.onchange('product_id')
    def _onchange_product_id_re(self):
        if self.re_unit_id and self.re_unit_id.project_id.product_id != self.product_id:
            self.re_unit_id = False

    @api.depends('product_id', 'product_uom_id', 'product_uom_qty', 're_unit_id')
    def _compute_price_unit(self):
        return super()._compute_price_unit()

    def _reset_price_unit(self):
        super()._reset_price_unit()
        if self.re_unit_id:
            price = self.re_unit_id.currency_id._convert(
                self.re_unit_id.price, self.currency_id or self.company_id.currency_id,
                self.company_id or self.env.company, self.order_id.date_order or fields.Date.today())
            self.update({'price_unit': price, 'technical_price_unit': price})

    @api.depends('product_id', 'linked_line_id', 'linked_line_ids', 're_unit_id')
    def _compute_name(self):
        super()._compute_name()
        for line in self.filtered('re_unit_id'):
            unit = line.re_unit_id
            type_label = dict(unit._fields['unit_type'].selection).get(unit.unit_type, '')
            details = [
                _("Project: %s", unit.project_id.name),
                unit.building and _("Building / Tower: %s", unit.building),
                _("Unit: %s", unit.name),
                unit.floor and _("Floor: %s", unit.floor),
                type_label and _("Type: %s", type_label),
                unit.area_sqft and _("Area: %s sq.ft", f"{unit.area_sqft:,.2f}"),
                unit.view and _("View: %s", unit.view),
            ]
            line.name = '\n'.join(d for d in details if d)
