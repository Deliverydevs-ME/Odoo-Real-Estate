from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class RealEstateCommissionPlan(models.Model):
    _name = 're.commission.plan'
    _description = "Commission Plan"
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    basis = fields.Selection(
        [('sale', "On Sale Confirmation"),
         ('collection', "Pro-rata to Collected Payments"),
         ('threshold', "Once a Collection Threshold is Reached")],
        string="Commission Earned", required=True, default='threshold',
        help="On Sale Confirmation: the full commission is payable when the sales order is confirmed.\n"
             "Pro-rata: the commission is earned in proportion to the payments received from the buyer.\n"
             "Threshold: the full commission becomes payable once the buyer has paid the threshold %.")
    threshold_percentage = fields.Float(
        string="Collection Threshold %", default=10.0,
        help="Share of the sale price the buyer must have paid before the commission is payable.")
    default_rate = fields.Float(string="Default Rate %", digits=(16, 4), default=2.0)
    line_ids = fields.One2many('re.commission.plan.line', 'plan_id', string="Specific Rates", copy=True)
    active = fields.Boolean(default=True)
    note = fields.Text()

    def _get_rate(self, project, unit):
        """Most specific matching rule: project + unit type > project > unit type > default."""
        self.ensure_one()
        best, best_score = None, -1
        for line in self.line_ids:
            if line.project_id and line.project_id != project:
                continue
            if line.unit_type and line.unit_type != unit.unit_type:
                continue
            score = (2 if line.project_id else 0) + (1 if line.unit_type else 0)
            if score > best_score:
                best, best_score = line, score
        if best:
            return best.rate, best.fixed_amount
        return self.default_rate, 0.0


class RealEstateCommissionPlanLine(models.Model):
    _name = 're.commission.plan.line'
    _description = "Commission Rate Rule"
    _order = 'plan_id, sequence, id'

    plan_id = fields.Many2one('re.commission.plan', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    project_id = fields.Many2one('re.project', string="Project")
    unit_type = fields.Selection(selection=lambda self: self.env['re.unit']._fields['unit_type'].selection,
                                 string="Unit Type")
    rate = fields.Float(string="Rate %", digits=(16, 4))
    fixed_amount = fields.Float(string="Fixed Bonus", help="Flat amount added to the percentage commission.")


class RealEstateCommissionAgreement(models.Model):
    _name = 're.commission.agreement'
    _description = "Agency Commission Agreement"
    _inherit = ['mail.thread']
    _order = 'date_from desc, id desc'

    name = fields.Char(string="Reference", required=True, copy=False,
                       default=lambda self: _("New"))
    agency_id = fields.Many2one('res.partner', string="Agency", required=True, index=True,
                                domain=[('re_partner_type', '=', 'agency')], tracking=True)
    plan_id = fields.Many2one('re.commission.plan', string="Commission Plan", required=True, tracking=True)
    date_from = fields.Date(string="Valid From", required=True, default=fields.Date.context_today, tracking=True)
    date_to = fields.Date(string="Valid Until", tracking=True,
                          help="Leave empty to follow the agency registration: the agreement stays valid "
                               "as long as the registration is active.")
    broker_share = fields.Float(string="Broker Share %",
                                help="Informative share of the commission passed on to the broker.")
    attachment_ids = fields.Many2many('ir.attachment', 're_commission_agreement_attachment_rel',
                                      'agreement_id', 'attachment_id', string="Signed Agreement")
    state = fields.Selection(
        [('draft', "Draft"), ('confirmed', "Confirmed"), ('cancelled', "Cancelled")],
        default='draft', required=True, tracking=True)
    validity = fields.Selection(
        [('upcoming', "Upcoming"), ('valid', "Valid"), ('expired', "Expired"), ('inactive', "Not Active")],
        compute='_compute_validity')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _("New")) == _("New"):
                vals['name'] = self.env['ir.sequence'].next_by_code('re.commission.agreement') or _("New")
        return super().create(vals_list)

    @api.depends('state', 'date_from', 'date_to')
    def _compute_validity(self):
        today = fields.Date.context_today(self)
        for agreement in self:
            if agreement.state != 'confirmed':
                agreement.validity = 'inactive'
            elif agreement.date_from > today:
                agreement.validity = 'upcoming'
            elif agreement.date_to and agreement.date_to < today:
                agreement.validity = 'expired'
            else:
                agreement.validity = 'valid'

    @api.constrains('date_from', 'date_to', 'agency_id', 'state')
    def _check_overlap(self):
        for agreement in self.filtered(lambda a: a.state == 'confirmed'):
            if agreement.date_to and agreement.date_to < agreement.date_from:
                raise ValidationError(_("The end date must be after the start date."))
            domain = [('id', '!=', agreement.id), ('agency_id', '=', agreement.agency_id.id),
                      ('state', '=', 'confirmed'),
                      '|', ('date_to', '=', False), ('date_to', '>=', agreement.date_from)]
            if agreement.date_to:
                domain.append(('date_from', '<=', agreement.date_to))
            if self.search_count(domain):
                raise ValidationError(_("%s already has a confirmed commission agreement for this period.",
                                        agreement.agency_id.name))

    def action_confirm(self):
        self.state = 'confirmed'

    def action_cancel(self):
        self.state = 'cancelled'

    def action_draft(self):
        self.state = 'draft'

    @api.model
    def _find_for(self, agency, on_date):
        return self.search([
            ('agency_id', '=', agency.id), ('state', '=', 'confirmed'), ('date_from', '<=', on_date),
            '|', ('date_to', '=', False), ('date_to', '>=', on_date),
        ], limit=1)


class RealEstateCommission(models.Model):
    _name = 're.commission'
    _description = "Agency / Broker Commission"
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(compute='_compute_name', store=True)
    order_id = fields.Many2one('sale.order', string="Sales Order", required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one(related='order_id.partner_id', string="Buyer", store=True)
    project_id = fields.Many2one(related='order_id.re_project_id', store=True)
    unit_id = fields.Many2one(related='order_id.re_unit_id', store=True)
    agency_id = fields.Many2one('res.partner', string="Agency", required=True, index=True)
    broker_id = fields.Many2one('res.partner', string="Broker")
    agreement_id = fields.Many2one('re.commission.agreement', string="Agreement")
    plan_id = fields.Many2one('re.commission.plan', string="Plan")
    basis = fields.Selection(related='plan_id.basis', store=True)
    company_id = fields.Many2one(related='order_id.company_id', store=True)
    currency_id = fields.Many2one(related='order_id.currency_id', store=True)
    rate = fields.Float(string="Rate %", digits=(16, 4), tracking=True)
    fixed_amount = fields.Monetary(string="Fixed Bonus")
    base_amount = fields.Monetary(string="Commission Base", help="Untaxed sale price.")
    total_amount = fields.Monetary(compute='_compute_total_amount', store=True, string="Total Commission",
                                   tracking=True)
    collected_percentage = fields.Float(compute='_compute_earned', string="Collected %")
    earned_amount = fields.Monetary(compute='_compute_earned', string="Earned")
    bill_line_ids = fields.Many2many('account.move.line', 're_commission_bill_line_rel', 'commission_id',
                                     'line_id', string="Bill Lines", copy=False)
    bill_ids = fields.Many2many('account.move', compute='_compute_billing', string="Vendor Bills")
    billed_amount = fields.Monetary(compute='_compute_billing', string="Billed")
    paid_amount = fields.Monetary(compute='_compute_billing', string="Paid")
    to_bill_amount = fields.Monetary(compute='_compute_billing', string="To Bill")
    blocked = fields.Boolean(tracking=True, copy=False)
    blocked_reason = fields.Char(tracking=True, copy=False)
    cancelled = fields.Boolean(copy=False)
    state = fields.Selection(
        [('blocked', "Blocked"), ('pending', "Not Yet Earned"), ('to_bill', "To Bill"),
         ('billed', "Billed"), ('paid', "Paid"), ('cancelled', "Cancelled")],
        compute='_compute_billing', search='_search_state', string="Status")

    @api.depends('order_id.name', 'agency_id.name')
    def _compute_name(self):
        for commission in self:
            commission.name = f"{commission.order_id.name} - {commission.agency_id.name or ''}"

    @api.depends('rate', 'base_amount', 'fixed_amount')
    def _compute_total_amount(self):
        for commission in self:
            commission.total_amount = commission.currency_id.round(
                commission.base_amount * commission.rate / 100.0 + commission.fixed_amount)

    @api.depends('total_amount', 'plan_id.basis', 'plan_id.threshold_percentage', 'order_id.re_amount_paid',
                 'order_id.amount_total', 'order_id.state')
    def _compute_earned(self):
        for commission in self:
            order = commission.order_id
            collected = (order.re_amount_paid / order.amount_total * 100.0) if order.amount_total else 0.0
            commission.collected_percentage = collected
            if order.state != 'sale':
                earned = 0.0
            elif commission.basis == 'collection':
                earned = commission.total_amount * min(collected, 100.0) / 100.0
            elif commission.basis == 'threshold':
                reached = collected + 1e-6 >= commission.plan_id.threshold_percentage
                earned = commission.total_amount if reached else 0.0
            else:
                earned = commission.total_amount
            commission.earned_amount = commission.currency_id.round(earned)

    @api.depends('bill_line_ids.move_id.state', 'bill_line_ids.move_id.payment_state', 'bill_line_ids.price_total',
                 'earned_amount', 'blocked', 'cancelled')
    def _compute_billing(self):
        for commission in self:
            lines = commission.bill_line_ids.filtered(lambda l: l.move_id.state != 'cancel')
            moves = lines.move_id
            commission.bill_ids = moves
            commission.billed_amount = sum(lines.mapped('price_total'))
            paid = 0.0
            for move in moves.filtered(lambda m: m.state == 'posted'):
                move_lines = lines.filtered(lambda l: l.move_id == move)
                ratio = (1 - move.amount_residual / move.amount_total) if move.amount_total else 0.0
                paid += sum(move_lines.mapped('price_total')) * ratio
            commission.paid_amount = commission.currency_id.round(paid)
            to_bill = commission.currency_id.round(commission.earned_amount - commission.billed_amount)
            commission.to_bill_amount = max(to_bill, 0.0)
            cur = commission.currency_id
            if commission.cancelled:
                state = 'cancelled'
            elif commission.blocked:
                state = 'blocked'
            elif not cur.is_zero(commission.total_amount) and not cur.is_zero(commission.billed_amount) \
                    and cur.compare_amounts(commission.paid_amount, commission.total_amount) >= 0:
                state = 'paid'
            elif cur.compare_amounts(commission.to_bill_amount, 0.0) > 0:
                state = 'to_bill'
            elif not cur.is_zero(commission.billed_amount):
                state = 'billed'
            else:
                state = 'pending'
            commission.state = state

    def _search_state(self, operator, value):
        if operator not in ('=', '!=', 'in', 'not in'):
            raise UserError(_("Unsupported search operator on commission status."))
        values = [value] if isinstance(value, str) else list(value)
        records = self.search([]).filtered(lambda c: c.state in values)
        positive = operator in ('=', 'in')
        return [('id', 'in' if positive else 'not in', records.ids)]

    # ------------------------------------------------------------------
    # Creation from sales orders
    # ------------------------------------------------------------------

    @api.model
    def _create_for_orders(self, orders):
        """Create the commission of each confirmed order sold through an agency."""
        commissions = self.browse()
        for order in orders.filtered(lambda o: o.re_agency_id):
            if self.search_count([('order_id', '=', order.id), ('cancelled', '=', False)]):
                continue
            agency = order.re_agency_id
            on_date = fields.Date.to_date(order.date_order) or fields.Date.context_today(self)
            agreement = self.env['re.commission.agreement']._find_for(agency, on_date)
            reasons = []
            if agency.re_registration_state != 'active':
                reasons.append(_("agency registration is %s",
                                 dict(agency._fields['re_registration_state'].selection).get(
                                     agency.re_registration_state, '-')))
            if order.re_broker_id and order.re_broker_id.re_registration_state != 'active':
                reasons.append(_("broker registration is not active"))
            if not agreement:
                reasons.append(_("no valid commission agreement on %s", on_date))
            plan = agreement.plan_id
            rate, fixed = plan._get_rate(order.re_project_id, order.re_unit_id) if plan else (0.0, 0.0)
            commissions |= self.create({
                'order_id': order.id,
                'agency_id': agency.id,
                'broker_id': order.re_broker_id.id,
                'agreement_id': agreement.id,
                'plan_id': plan.id,
                'rate': rate,
                'fixed_amount': fixed,
                'base_amount': order.amount_untaxed,
                'blocked': bool(reasons),
                'blocked_reason': '; '.join(reasons) if reasons else False,
            })
        return commissions

    def action_unblock(self):
        for commission in self:
            if not commission.plan_id:
                raise UserError(_("Set a commission plan and rate on %s before unblocking.", commission.name))
        self.write({'blocked': False})
        for commission in self:
            commission.message_post(body=_("Commission unblocked by %s", self.env.user.name))

    def action_block(self):
        self.write({'blocked': True, 'blocked_reason': _("Blocked manually by %s", self.env.user.name)})

    def action_cancel(self):
        if self.filtered(lambda c: c.billed_amount):
            raise UserError(_("Billed commissions cannot be cancelled. Cancel or credit the bills first."))
        self.write({'cancelled': True})

    # ------------------------------------------------------------------
    # Vendor bills
    # ------------------------------------------------------------------

    def action_create_bills(self):
        to_bill = self.filtered(lambda c: c.state == 'to_bill')
        if not to_bill:
            raise UserError(_("There is nothing to bill: commissions must be earned, not blocked, "
                              "and not already billed."))
        product = self.env.ref('odoo_dd_real_estate.product_broker_commission')
        bills = self.env['account.move']
        for (agency, company), commissions in to_bill.grouped(lambda c: (c.agency_id, c.company_id)).items():
            bill = self.env['account.move'].with_company(company).create({
                'move_type': 'in_invoice',
                'partner_id': agency.id,
                'invoice_date': fields.Date.context_today(self),
                'ref': _("Commission %s", ', '.join(commissions.order_id.mapped('name'))),
                'invoice_line_ids': [fields.Command.create({
                    'product_id': product.id,
                    'name': _("Commission %(order)s - %(unit)s (%(rate)s%%)",
                              order=c.order_id.name, unit=c.unit_id.display_name or c.project_id.name or '',
                              rate=round(c.rate, 4)),
                    'quantity': 1,
                    'price_unit': c.to_bill_amount,
                    'tax_ids': [fields.Command.clear()],
                }) for c in commissions],
            })
            for commission, line in zip(commissions, bill.invoice_line_ids.sorted('sequence')):
                commission.bill_line_ids = [fields.Command.link(line.id)]
            bills |= bill
        return {
            'type': 'ir.actions.act_window',
            'name': _("Commission Bills"),
            'res_model': 'account.move',
            'view_mode': 'list,form' if len(bills) > 1 else 'form',
            'res_id': bills.id if len(bills) == 1 else False,
            'domain': [('id', 'in', bills.ids)],
            'context': {'default_move_type': 'in_invoice'},
        }
